"""AST instrumentation for an explicit structured, primitive-local subset.

For this subset (no early return/break/continue/exception edges), structured
predicate nesting agrees with the control-dependence relation used by the paper.
"""
import ast
import builtins
import copy

HELPER = "__specmind_slice_runtime__"
FRAME = "__specmind_slice_frame__"
PURE_CALLS = {"int", "float", "bool", "str", "abs", "min", "max"}


class Unsupported(ValueError):
    pass


def analyze(source, function_line, filename):
    tree = ast.parse(source, filename=filename)
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.lineno == function_line]
    if len(matches) != 1:
        raise Unsupported("Only a top-level synchronous function is supported initially")
    fn = matches[0]
    if fn.decorator_list or fn.args.vararg or fn.args.kwarg:
        raise Unsupported("Decorators and variadic arguments are not modeled")
    if any(isinstance(n, ast.Name) and n.id.startswith("__specmind_") for n in ast.walk(fn)):
        raise Unsupported("Reserved instrumentation identifier occurs in function")
    args = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)]
    if any(name.startswith("__specmind_") for name in [fn.name, *args]):
        raise Unsupported("Reserved instrumentation identifier")
    locals_ = set(args)
    sites = {
        "entry": {
            "file": filename,
            "line": fn.lineno,
            "column": fn.col_offset + 1,
            "source": source.splitlines()[fn.lineno - 1],
            "kind": "entry",
            "statement": "entry",
        }
    }
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body or not isinstance(body[-1], ast.Return):
        raise Unsupported("A single final top-level return is required")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    if len(returns) != 1:
        raise Unsupported("Early or branch return requires general CFG support")
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            locals_.add(node.id)
    if locals_ & (PURE_CALLS | {"input", "print"}):
        raise Unsupported("Shadowed builtin operation")
    used_calls = set()

    def expression(node, input_allowed=False, print_allowed=False):
        if node is None:
            return
        if isinstance(node, ast.Constant):
            if type(node.value) not in (type(None), bool, int, float, str):
                raise Unsupported("Non-primitive literal")
        elif isinstance(node, ast.Name):
            if node.id not in locals_ or not isinstance(node.ctx, ast.Load):
                raise Unsupported("Global/free variable read is outside the local model: " + node.id)
        elif isinstance(node, ast.BinOp):
            expression(node.left)
            expression(node.right)
        elif isinstance(node, ast.UnaryOp):
            expression(node.operand)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                expression(v)
        elif isinstance(node, ast.Compare):
            expression(node.left)
            for v in node.comparators:
                expression(v)
        elif isinstance(node, ast.IfExp):
            expression(node.test)
            expression(node.body)
            expression(node.orelse)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or any(isinstance(a, ast.Starred) for a in node.args):
                raise Unsupported("Method/dynamic/starred call is not modeled")
            name = node.func.id
            if name == "input":
                if not input_allowed or node.args or node.keywords:
                    raise Unsupported("input() is allowed only as an explicit assignment input origin")
            elif name == "print":
                if not print_allowed or node.keywords:
                    raise Unsupported("Only a standalone positional print is modeled")
            elif name not in PURE_CALLS or node.keywords:
                raise Unsupported("Unmodeled call: " + name)
            used_calls.add(name)
            for a in node.args:
                expression(a, input_allowed=input_allowed and name in {"int", "float", "str", "bool"})
        else:
            raise Unsupported("Unmodeled expression: " + type(node).__name__)

    def statements(items):
        for node in items:
            site = f"{node.lineno}:{node.col_offset}:{type(node).__name__}"
            fragment = ast.get_source_segment(source, node)
            if isinstance(node, (ast.If, ast.While)):
                fragment = ("if " if isinstance(node, ast.If) else "while ") + ast.unparse(node.test) + ":"
            sites[site] = {
                "file": filename,
                "line": node.lineno,
                "column": node.col_offset + 1,
                "source": fragment,
                "kind": type(node).__name__,
                "statement": site,
            }
            if isinstance(node, ast.Assign):
                if not all(isinstance(t, ast.Name) for t in node.targets):
                    raise Unsupported("Heap writes and unpacking are not modeled")
                expression(node.value, input_allowed=True)
                for call in ast.walk(node.value):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "input"
                    ):
                        key = f"{call.lineno}:{call.col_offset}:input"
                        sites[key] = {
                            "file": filename,
                            "line": call.lineno,
                            "column": call.col_offset + 1,
                            "source": ast.get_source_segment(source, call),
                            "kind": "input",
                            "statement": site,
                        }
            elif isinstance(node, ast.AugAssign):
                if not isinstance(node.target, ast.Name):
                    raise Unsupported("Non-local augmented write")
                expression(node.value)
            elif isinstance(node, (ast.If, ast.While)):
                expression(node.test)
                if isinstance(node, ast.While) and node.orelse:
                    raise Unsupported("Loop else is not modeled")
                statements(node.body)
                statements(node.orelse)
            elif isinstance(node, ast.Return):
                expression(node.value)
            elif isinstance(node, ast.Expr):
                if (
                    not isinstance(node.value, ast.Call)
                    or not isinstance(node.value.func, ast.Name)
                    or node.value.func.id != "print"
                ):
                    raise Unsupported("Only standalone print expression statements are modeled")
                expression(node.value, print_allowed=True)
            elif not isinstance(node, ast.Pass):
                raise Unsupported("Unmodeled statement: " + type(node).__name__)

    statements(body)
    return fn, body, args, sites, used_calls


def instrument(source, function_line, filename, globals_, *, future_flags=0):
    fn, body, args, sites, calls = analyze(source, function_line, filename)
    for name in calls:
        if name in globals_ and globals_[name] is not getattr(builtins, name):
            raise Unsupported("Global builtin binding is shadowed: " + name)
    if HELPER in globals_:
        raise Unsupported("Instrumentation global already exists")

    def invoke(method, *values):
        return ast.Call(ast.Attribute(ast.Name(HELPER, ast.Load()), method, ast.Load()), list(values), [])

    def frame():
        return ast.Name(FRAME, ast.Load())

    def const(value):
        return ast.Constant(value)

    def locate(node, original):
        return ast.copy_location(node, original)

    class Reads(ast.NodeTransformer):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                return locate(invoke("read", frame(), const(node.id), copy.deepcopy(node)), node)
            return node

        def visit_Call(self, node):
            new = copy.deepcopy(node)
            arguments = [self.visit(a) for a in node.args]
            new = locate(
                invoke("call", frame(), const(node.func.id), copy.deepcopy(node.func), *arguments), node
            )
            if node.func.id == "input":
                site = f"{node.lineno}:{node.col_offset}:input"
                assert site in sites
                return locate(invoke("input_value", frame(), const(site), new), node)
            return new

    def expr(value):
        return Reads().visit(copy.deepcopy(value)) if value is not None else const(None)

    def action(method, node, *values):
        return locate(ast.Expr(invoke(method, *values)), node)

    def rewrite(items):
        result = []
        for original in items:
            node = copy.deepcopy(original)
            site = f"{node.lineno}:{node.col_offset}:{type(node).__name__}"
            begin = action("begin", node, frame(), const(site))
            if isinstance(node, (ast.If, ast.While)):
                test = locate(invoke("predicate", frame(), expr(node.test)), node.test)

                def block(body):
                    return (
                        [locate(ast.With([ast.withitem(invoke("control", frame()))], rewrite(body)), node)]
                        if body
                        else []
                    )

                transformed = (
                    ast.If(test, block(node.body), block(node.orelse))
                    if isinstance(node, ast.If)
                    else ast.While(test, block(node.body), [])
                )
                if isinstance(node, ast.If):
                    result.extend([begin, locate(transformed, node)])
                else:
                    # The predicate needs a fresh observation boundary for EVERY test,
                    # including the final false test. A helper begins it before evaluation.
                    transformed.test = locate(
                        ast.Subscript(
                            ast.Tuple([invoke("begin", frame(), const(site)), test], ast.Load()),
                            const(1),
                            ast.Load(),
                        ),
                        node.test,
                    )
                    result.append(locate(transformed, node))
            elif isinstance(node, ast.Assign):
                node.value = expr(node.value)
                values = ast.Dict(
                    [const(t.id) for t in node.targets], [ast.Name(t.id, ast.Load()) for t in node.targets]
                )
                result.extend([begin, node, action("write", node, frame(), values)])
            elif isinstance(node, ast.AugAssign):
                node.value = expr(node.value)
                result.extend(
                    [
                        begin,
                        action(
                            "read", node, frame(), const(node.target.id), ast.Name(node.target.id, ast.Load())
                        ),
                        node,
                        action(
                            "write",
                            node,
                            frame(),
                            ast.Dict([const(node.target.id)], [ast.Name(node.target.id, ast.Load())]),
                        ),
                    ]
                )
            elif isinstance(node, ast.Return):
                result.extend(
                    [begin, locate(ast.Return(invoke("return_value", frame(), expr(node.value))), node)]
                )
            elif isinstance(node, ast.Expr):
                node.value = expr(node.value)
                result.extend([begin, node, action("expression", node, frame())])
            else:
                result.append(node)
        return result

    new = copy.deepcopy(fn)
    new.decorator_list = []
    new.returns = None
    new.type_comment = None
    new.args.defaults = []
    new.args.kw_defaults = [None for _ in new.args.kwonlyargs]
    for arg in (*new.args.posonlyargs, *new.args.args, *new.args.kwonlyargs):
        arg.annotation = None
    enter = ast.Assign(
        [ast.Name(FRAME, ast.Store())],
        invoke("enter", ast.Dict([const(n) for n in args], [ast.Name(n, ast.Load()) for n in args])),
    )
    handler = ast.ExceptHandler(None, None, [action("raised", fn, frame()), locate(ast.Raise(), fn)])
    wrapped = ast.Try(rewrite(body), [locate(handler, fn)], [], [action("leave", fn, frame())])
    new.body = [locate(enter, fn), locate(wrapped, fn)]
    module = ast.fix_missing_locations(ast.Module([new], []))
    namespace = {}
    exec(compile(module, filename, "exec", flags=future_flags, dont_inherit=True), namespace)
    return namespace[fn.name].__code__, sites
