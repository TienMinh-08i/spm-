"""Expression DDG for explicitly selected, single-argument read-only functions.

Opaque container identity is scoped to one root invocation. No input is retained
past that invocation, and no container contents or user hooks are inspected.
"""
import abc
import ast
import builtins
import copy
import inspect
import functools
import types
import sys
import threading
from collections import Counter
from collections.abc import Mapping

from specmind_prep.injected.slice_instrumentation import Unsupported
from specmind_prep.injected.slice_runtime import Recorder, primitive

HELPER = "__specmind_readonly_runtime__"
FRAME = "__specmind_readonly_frame__"
OPERATIONS = {
    "hasattr": builtins.hasattr,
    "isinstance": builtins.isinstance,
    "inspect.isgeneratorfunction": inspect.isgeneratorfunction,
    "inspect.isgenerator": inspect.isgenerator,
}


def _python_code(value):
    if type(value) is classmethod or type(value) is staticmethod:
        value = value.__func__
    return value.__code__ if type(value) is types.FunctionType else None


def external_sites(source, repo_path):
    sites = {}
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)]
    coverage = Counter(line for node in calls for line in range(node.lineno, node.end_lineno + 1))
    for node in calls:
        if len(node.args) == 1 and not node.keywords and not isinstance(node.args[0], ast.Starred):
            key = f"external:{node.lineno}:{node.col_offset}"
            arg = node.args[0]
            sites[key] = {
                "file": repo_path,
                "line": arg.lineno,
                "column": arg.col_offset + 1,
                "source": ast.get_source_segment(source, arg),
                "kind": "external_input",
                "statement": key,
                "call_line": node.lineno,
                "unambiguous_lines": [
                    line for line in range(node.lineno, node.end_lineno + 1) if coverage[line] == 1
                ],
            }
    return sites


def analyze(source, line, filename, function, selected_names):
    candidates = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.lineno == line]
    if len(candidates) != 1:
        raise Unsupported("Read-only scope requires a top-level synchronous function")
    fn = candidates[0]
    args = fn.args
    parameters = [a.arg for a in (*args.posonlyargs, *args.args)]
    if (
        fn.decorator_list
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or args.defaults
        or len(parameters) != 1
    ):
        raise Unsupported(
            "Read-only scope requires one explicit positional parameter, no decorators/defaults"
        )
    if any(isinstance(n, ast.Name) and n.id.startswith("__specmind_") for n in ast.walk(fn)):
        raise Unsupported("Reserved read-only instrumentation name")
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        raise Unsupported("Read-only model accepts a single return expression only")
    prefix = function + "|"
    owner = prefix + f"{body[0].lineno}:{body[0].col_offset}:Return"
    sites = {
        prefix
        + "entry": {
            "file": filename,
            "line": line,
            "column": fn.col_offset + 1,
            "source": source.splitlines()[line - 1],
            "kind": "entry",
            "statement": prefix + "entry",
        },
        owner: {
            "file": filename,
            "line": body[0].lineno,
            "column": body[0].col_offset + 1,
            "source": ast.get_source_segment(source, body[0]),
            "kind": "return",
            "statement": owner,
        },
    }
    calls = set()

    def site(node, suffix=""):
        key = prefix + f"{node.lineno}:{node.col_offset}:{type(node).__name__}" + suffix
        sites[key] = {
            "file": filename,
            "line": node.lineno,
            "column": node.col_offset + 1,
            "source": ast.get_source_segment(source, node),
            "kind": type(node).__name__,
            "statement": owner,
        }
        return key

    def visit(node):
        site(node)
        if isinstance(node, ast.Name):
            if node.id not in {parameters[0], "Mapping"}:
                raise Unsupported("Unmodeled read-only name: " + node.id)
        elif isinstance(node, ast.Constant):
            if type(node.value) not in (bool, int, float, str, type(None)):
                raise Unsupported("Unmodeled read-only literal")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            visit(node.operand)
        elif isinstance(node, ast.BoolOp):
            for i, value in enumerate(node.values):
                visit(value)
                if i < len(node.values) - 1:
                    site(value, f":predicate:{i}")
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name not in selected_names and name not in OPERATIONS:
                raise Unsupported("Unmodeled read-only call: " + name)
            if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                raise Unsupported("Read-only calls require explicit positional arguments")
            calls.add(name)
            for i, arg in enumerate(node.args):
                visit(arg)
                site(node, f":argument:{i}")
            site(node, ":binding")
        else:
            raise Unsupported("Unmodeled read-only expression: " + type(node).__name__)

    visit(body[0].value)
    return fn, parameters[0], sites, calls, owner, site


def instrument(source, line, filename, function, selected_names, *, future_flags=0):
    fn, parameter, sites, calls, owner, site = analyze(source, line, filename, function, selected_names)

    def invoke(name, *args):
        return ast.Call(ast.Attribute(ast.Name(HELPER, ast.Load()), name, ast.Load()), list(args), [])

    def c(value):
        return ast.Constant(value)

    def frame():
        return ast.Name(FRAME, ast.Load())

    def expr(node):
        key = site(node)
        if isinstance(node, ast.Name):
            value = invoke("name", frame(), c(node.id), copy.deepcopy(node))
        elif isinstance(node, ast.Constant):
            value = copy.deepcopy(node)
        elif isinstance(node, ast.UnaryOp):
            value = invoke("negate", frame(), expr(node.operand))
        elif isinstance(node, ast.Call):
            value = invoke(
                "invoke",
                frame(),
                c(key),
                c(ast.unparse(node.func)),
                copy.deepcopy(node.func),
                ast.Tuple([expr(arg) for arg in node.args], ast.Load()),
            )
        else:  # BoolOp: preserve the actual operand value and evaluate each operand once.

            def tail(i):
                if i == len(node.values) - 1:
                    return expr(node.values[i])
                condition = invoke(
                    "truth", frame(), c(site(node.values[i], f":predicate:{i}")), expr(node.values[i])
                )
                saved = invoke("saved", frame())
                return (
                    ast.IfExp(condition, tail(i + 1), saved)
                    if isinstance(node.op, ast.And)
                    else ast.IfExp(condition, saved, tail(i + 1))
                )

            value = tail(0)
        wrapped = invoke(
            "end",
            frame(),
            c(key),
            ast.Subscript(ast.Tuple([invoke("start", frame(), c(key)), value], ast.Load()), c(1), ast.Load()),
        )
        return ast.copy_location(wrapped, node)

    new = copy.deepcopy(fn)
    new.returns = None
    new.args.defaults = []
    for arg in (*new.args.posonlyargs, *new.args.args):
        arg.annotation = None
    enter = ast.Assign(
        [ast.Name(FRAME, ast.Store())], invoke("enter", c(function), ast.Name(parameter, ast.Load()))
    )
    returned = ast.Return(invoke("returned", frame(), c(owner), expr(fn.body[-1].value)))
    inactive = ast.If(
        ast.Compare(frame(), [ast.Is()], [c(None)]),
        [ast.Return(invoke("inactive", c(function), ast.Name(parameter, ast.Load())))],
        [],
    )
    handler = ast.ExceptHandler(None, None, [ast.Expr(invoke("raised", frame())), ast.Raise()])
    new.body = [
        ast.copy_location(enter, fn),
        ast.copy_location(inactive, fn),
        ast.copy_location(
            ast.Try(
                [ast.copy_location(returned, fn.body[-1])],
                [ast.copy_location(handler, fn)],
                [],
                [ast.copy_location(ast.Expr(invoke("leave", frame())), fn)],
            ),
            fn,
        ),
    ]
    module = ast.fix_missing_locations(ast.Module([new], []))
    namespace = {}
    exec(compile(module, filename, "exec", flags=future_flags, dont_inherit=True), namespace)
    return namespace[fn.name].__code__, sites


class Runtime(Recorder):
    def __init__(self, config):
        super().__init__(config)
        self.bindings, self.codes, self.globals = {}, {}, {}
        self.tokens = []
        self.epoch = 0
        self.root_id = None
        self.root_frame = None
        self.abc_token = None
        self.operation_codes = {name: getattr(value, "__code__", None) for name, value in OPERATIONS.items()}
        self.original_functions = {}
        self.environment = []
        self.environment_sizes = []
        for module in (inspect, functools, builtins):
            namespace = module.__dict__
            self.environment_sizes.append((namespace, len(namespace)))
            for name, value in namespace.items():
                self.environment.append((namespace, name, value, _python_code(value)))
        # ABC registry tokens do not change when a class method is monkeypatched.
        classes = type.__getattribute__(Mapping, "__mro__") + type.__getattribute__(type(Mapping), "__mro__")
        for cls in classes:
            namespace = type.__getattribute__(cls, "__dict__")
            self.environment_sizes.append((namespace, len(namespace)))
            self.environment.extend(
                (namespace, name, value, _python_code(value)) for name, value in namespace.items()
            )

    def snapshot(self, value):
        cls = type(value)
        if any(cls is t for t in (list, tuple, dict, bytes)):
            if id(value) != self.root_id:
                self.issue("Object is not the current root input")
            return {"kind": "opaque_input", "type": cls.__name__, "token": self.epoch}
        if value is Mapping:
            return {"kind": "global_type", "name": "collections.abc.Mapping"}
        try:
            return primitive(value)
        except ValueError as exc:
            self.issue(str(exc))
            return {"kind": "unsupported"}

    def guard(self):
        if self.abc_token != abc.get_cache_token():
            self.issue("ABC registry changed during selected execution")
        if any(len(namespace) != size for namespace, size in self.environment_sizes):
            self.issue("Trusted standard-library namespace changed")
        for namespace, name, value, code in getattr(self, "environment", []):
            if namespace.get(name) is not value or (code is not None and _python_code(value) is not code):
                self.issue("Trusted inspect/functools environment changed")
        for fid, namespace in self.globals.items():
            if fid in self.codes and self.bindings[fid].__code__ is not self.codes[fid]:
                self.issue("Selected function code changed")
            if "Mapping" in namespace and namespace["Mapping"] is not Mapping:
                self.issue("Mapping binding changed")
            if "inspect" in namespace and namespace["inspect"] is not inspect:
                self.issue("inspect module binding changed")
            for name, expected in self.config["functions"][fid]["bindings"].items():
                if "." not in name:
                    actual = namespace.get(name, builtins.__dict__.get(name))
                    wanted = self.bindings.get(expected, OPERATIONS.get(expected))
                    if actual is not wanted:
                        self.issue("Read-only function binding changed: " + name)

    def root_start(self, obj):
        self.epoch += 1
        self.root_id = id(obj)
        if not any(type(obj) is t for t in (list, tuple, dict, bytes, str, int, float, bool, type(None))):
            self.issue("Root input is outside exact builtin value types")
        self.guard()

    def inactive(self, function, obj):
        return self.original_functions[function](obj)

    def emit(self, frame, site, kind, *, data=(), reads=None, writes=(), values=None, links=None, **details):
        state = self.frames[frame]
        links = dict(links or {})
        if state["frame_invocation"] is not None:
            links["frame_invocation"] = state["frame_invocation"]
        return super().emit(
            frame,
            site,
            kind,
            reads=reads,
            writes=writes,
            values=values,
            links=links,
            details=dict(details, data=list(data), function=state["function"]),
        )

    def enter(self, function, obj):
        if not self.active:
            return None
        if threading.get_ident() != self.owner:
            self.issue("Worker-thread read-only calls are unsupported")
            return None
        token = self.tokens[-1] if self.tokens and self.tokens[-1]["callee"] == function else None
        if function != self.config["function"] and token is None:
            return None
        if function == self.config["function"]:
            if self.root_frame is not None:
                self.issue("Recursive root invocation is unsupported")
            self.root_start(obj)
        fid = len(self.calls) + 1
        self.frames[fid] = {
            "function": function,
            "definitions": {},
            "controls": [],
            "stack": [],
            "frame_invocation": token["event"] if token else None,
        }
        native = sys._getframe(1).f_back
        caller = (
            token["caller"]
            if token
            else {
                "function": native.f_globals.get("__name__", "") + "." + native.f_code.co_qualname,
                "file": native.f_code.co_filename,
                "line": native.f_lineno,
            }
        )
        parameter = self.config["functions"][function]["parameter"]
        call = {
            "frame": fid,
            "function": function,
            "parent_frame": token["frame"] if token else None,
            "caller": caller,
            "phase": self.phase,
            "arguments": {parameter: self.snapshot(obj)},
            "returned": False,
            "return": None,
        }
        self.calls.append(call)
        if token:
            token["callee_frame"] = fid
        if token:
            self.emit(
                fid,
                function + "|entry",
                "parameter",
                writes=[parameter],
                values={parameter: self.snapshot(obj)},
                links={"argument": token["argument"]},
            )
        else:
            self.root_frame = fid
            choices = [
                key
                for key, s in self.config["external_sites"].items()
                if caller["line"] in s["unambiguous_lines"] and self.config["test_file"] == caller["file"]
            ]
            site = choices[0] if len(choices) == 1 else function + "|entry"
            origin = self.emit(
                fid, site, "external_input", values={parameter: self.snapshot(obj)}, external_caller=caller
            )
            self.emit(
                fid,
                function + "|entry",
                "parameter",
                data=[origin],
                writes=[parameter],
                values={parameter: self.snapshot(obj)},
            )
        call["event"] = self.emit(fid, function + "|entry", "call")
        return fid

    def start(self, frame, site):
        if frame is not None:
            self.guard()
            self.frames[frame]["stack"].append(
                {"site": site, "data": [], "reads": {}, "base": len(self.frames[frame]["controls"])}
            )

    def name(self, frame, name, value):
        if frame is None:
            return value
        state = self.frames[frame]
        context = state["stack"][-1]
        if name in state["definitions"]:
            context["reads"][name] = state["definitions"][name]
        elif name == "Mapping":
            if value is not Mapping:
                self.issue("Unmodeled global type binding")
            ident = self.emit(
                frame,
                context["site"],
                "global",
                values={"binding": "collections.abc.Mapping", "abc_state": self.abc_token},
            )
            context["data"].append(ident)
        else:
            self.issue("Missing parameter source")
        return value

    def end(self, frame, site, value):
        if frame is None:
            return value
        state = self.frames[frame]
        context = state["stack"].pop()
        ident = self.emit(
            frame,
            site,
            "expression",
            data=context["data"],
            reads=context["reads"],
            values={"value": self.snapshot(value)},
        )
        del state["controls"][context["base"] :]
        if state["stack"]:
            state["stack"][-1]["data"].append(ident)
        else:
            state["last"] = ident
        return value

    def truth(self, frame, site, value):
        if frame is None:
            return bool(value)
        if any(type(value) is t for t in (list, tuple, dict, bytes)):
            self.issue("Container truth/content reads are outside type-only model")
        result = bool(value)
        state = self.frames[frame]
        context = state["stack"][-1]
        ident = self.emit(frame, site, "predicate", data=context["data"][-1:], values={"outcome": result})
        state["controls"].append(ident)
        context["saved"] = value
        return result

    def saved(self, frame):
        return self.frames[frame]["stack"][-1]["saved"]

    def negate(self, frame, value):
        if frame is not None and any(type(value) is t for t in (list, tuple, dict, bytes)):
            self.issue("Container truth/content reads are outside type-only model")
        return not value

    def invoke(self, frame, site, name, function, args):
        if frame is None:
            return function(*args)
        state = self.frames[frame]
        context = state["stack"][-1]
        binding = self.config["functions"][state["function"]]["bindings"][name]
        expected = self.bindings.get(binding, OPERATIONS.get(binding))
        if function is not expected or (
            binding in self.codes and function.__code__ is not self.codes[binding]
        ):
            self.issue("Read-only callable changed: " + name)
            return function(*args)
        if binding in self.bindings:
            if len(args) != 1:
                self.issue("Selected helper requires one explicit argument")
                return function(*args)
            arg = self.emit(
                frame,
                site + ":argument:0",
                "argument",
                data=context["data"],
                values={"value": self.snapshot(args[0])},
            )
            invocation = self.emit(
                frame,
                site,
                "invocation",
                callee=binding,
                arguments={self.config["functions"][binding]["parameter"]: arg},
            )
            record = self.config["functions"][state["function"]]
            token = {
                "event": invocation,
                "frame": frame,
                "argument": arg,
                "callee": binding,
                "caller": {
                    "function": state["function"],
                    "file": record["source_file"],
                    "line": int(site.split("|", 1)[1].split(":", 1)[0]),
                },
            }
            self.tokens.append(token)
            try:
                value = function(*args)
            finally:
                self.tokens.pop()
            returned = self.calls[token["callee_frame"] - 1]["return_event"]
            bridge = self.emit(
                frame,
                site,
                "call_result",
                links={"result_invocation": invocation, "return": returned},
                values={"value": self.snapshot(value)},
            )
            context["data"] = [bridge]
            return value
        if binding == "hasattr":
            if len(args) != 2 or type(args[1]) is not str or args[1] not in {"__iter__", "strip"}:
                self.issue("Unmodeled hasattr lookup")
        elif binding == "isinstance":
            if len(args) != 2 or args[1] is not Mapping:
                self.issue("Only Mapping isinstance is modeled")
        if not args or not any(
            type(args[0]) is t for t in (list, tuple, dict, bytes, str, int, float, bool, type(None))
        ):
            self.issue("Custom object protocol operations are unsupported")
        if (
            self.operation_codes[binding] is not None
            and function.__code__ is not self.operation_codes[binding]
        ):
            self.issue("Trusted operation code changed")
        origin = self.emit(
            frame, site + ":binding", "global", values={"operation": binding, "abc_state": self.abc_token}
        )
        value = function(*args)
        self.guard()
        if type(value) is not bool:
            self.issue("Trusted predicate did not return bool")
        if binding.startswith("inspect.") and value is not False:
            self.issue("Inspect summary is only defined for non-function/non-generator exact builtin inputs")
        operation = self.emit(
            frame,
            site,
            "operation",
            data=context["data"] + [origin],
            values={"value": self.snapshot(value)},
            operation=binding,
        )
        context["data"] = [operation]
        return value

    def returned(self, frame, site, value):
        if frame is None:
            return value
        if type(value) is not bool:
            self.issue("Read-only functions must return bool")
        ident = self.emit(
            frame,
            site,
            "return",
            data=[self.frames[frame]["last"]],
            writes=["$return"],
            values={"$return": self.snapshot(value)},
        )
        self.calls[frame - 1].update(returned=True, return_event=ident, **{"return": self.snapshot(value)})
        return value

    def leave(self, frame):
        if frame is not None:
            self.frames[frame]["stack"].clear()
            self.frames[frame]["controls"].clear()
            if frame == self.root_frame:
                self.root_frame = None
                self.root_id = None
