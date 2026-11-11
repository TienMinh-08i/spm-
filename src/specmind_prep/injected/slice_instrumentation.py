"""AST instrumentation for an explicit structured, primitive-local subset.

Acyclic return branches are normalized to explicit continuations. Returns
across loop/exception scopes still require a CFG model.
"""
import ast
import builtins
import copy
from specmind_prep.injected.slice_control_flow import lower_return_expressions

HELPER = "__specmind_slice_runtime__"
FRAME = "__specmind_slice_frame__"
PURE_CALLS = {"int", "float", "bool", "str", "abs", "min", "max"}


class Unsupported(ValueError):
    pass


def normalize_returns(body, function):
    """Put continuations into fall-through branches without evaluating twice.

    This preserves the false-edge control dependency after an early return.
    Returns crossing loops/exception scopes need a CFG and remain rejected.
    The synthetic implicit return has explicit provenance, not invented source.
    """
    for node in ast.walk(function):
        if isinstance(node, (ast.For, ast.While, ast.Try, ast.With)) and any(
            isinstance(child, ast.Return) for child in ast.walk(node)
        ):
            raise Unsupported("Return across loop/exception/context scope requires CFG support")
    implicit = ast.copy_location(ast.Return(value=None), function)
    implicit._slice_implicit_return = True

    def has_return(node):
        return any(isinstance(child, ast.Return) for child in ast.walk(node))

    def sequence(items, tail):
        result = list(tail)
        for node in reversed(items):
            if isinstance(node, ast.Return):
                result = [node]
            elif isinstance(node, ast.If) and has_return(node):
                branch = copy.deepcopy(node)
                branch.body = sequence(node.body, result)
                branch.orelse = sequence(node.orelse, result)
                result = [branch]
            else:
                result = [node, *result]
            if sum(1 for item in result for _ in ast.walk(item)) > 20000:
                raise Unsupported("Return normalization exceeds AST budget")
        return result

    class LocalAnnotations(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            return node

        def visit_ClassDef(self, node):
            return node

        def visit_AnnAssign(self, node):
            if node.value is None:
                return node  # Keep compiler local-binding semantics.
            return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)

    normalized = [LocalAnnotations().visit(copy.deepcopy(node)) for node in body]
    return sequence(lower_return_expressions(normalized), [implicit])


def analyze(source, function_line, filename, *, call_names=(), heap=False):
    tree = ast.parse(source, filename=filename)
    matches = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.lineno == function_line]
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
    body = normalize_returns(body, fn)
    for node in (n for statement in body for n in ast.walk(statement)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            locals_.add(node.id)
    if locals_ & (PURE_CALLS | {"input", "print"}):
        raise Unsupported("Shadowed builtin operation")
    used_calls = set()
    owner_site = None

    def has_traced_call(nodes):
        return bool(call_names) and any(
            isinstance(child, ast.Call) and isinstance(child.func, (ast.Name, ast.Attribute)) and ast.unparse(child.func) in call_names
            for node in nodes
            for child in ast.walk(node)
        )

    def reject_conditional_calls(nodes):
        if has_traced_call(nodes):
            raise Unsupported(
                "Conditional traced calls inside expressions require explicit if/else control flow"
            )

    def expression(node, input_allowed=False, print_allowed=False):
        if node is None:
            return
        if isinstance(node, ast.Constant):
            if type(node.value) not in (type(None), bool, int, float, str):
                raise Unsupported("Non-primitive literal")
        elif isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                raise Unsupported("Expression requires a variable read")
            if node.id not in locals_:
                site = f"{node.lineno}:{node.col_offset}:global"
                sites[site] = {"file":filename,"line":node.lineno,"column":node.col_offset+1,
                              "source":ast.get_source_segment(source,node),"kind":"input","statement":owner_site}
        elif heap and isinstance(node, ast.Attribute):
            expression(node.value)
            site = f"{node.lineno}:{node.col_offset}:heap_read"
            sites[site] = {"file":filename,"line":node.lineno,"column":node.col_offset+1,
                          "source":ast.get_source_segment(source,node),"kind":"heap_read","statement":owner_site}
        elif heap and isinstance(node, ast.Subscript):
            if not isinstance(node.ctx, ast.Load) or isinstance(node.slice, ast.Slice):
                raise Unsupported("Only heap item reads are modeled")
            expression(node.value)
            expression(node.slice)
            site = f"{node.lineno}:{node.col_offset}:heap_read"
            sites[site] = {"file":filename,"line":node.lineno,"column":node.col_offset+1,
                          "source":ast.get_source_segment(source,node),"kind":"heap_read","statement":owner_site}
        elif heap and isinstance(node, (ast.Dict, ast.List)):
            if isinstance(node, ast.Dict):
                if any(k is None for k in node.keys):raise Unsupported("Dict unpacking not modeled")
                for key in node.keys:expression(key)
                values=node.values
            else:values=node.elts
            for value in values:expression(value)
            site = f"{node.lineno}:{node.col_offset}:heap_origin"
            sites[site] = {"file":filename,"line":node.lineno,"column":node.col_offset+1,
                          "source":ast.get_source_segment(source,node),"kind":"heap_origin","statement":owner_site}
        elif isinstance(node, ast.BinOp):
            expression(node.left)
            expression(node.right)
        elif isinstance(node, ast.UnaryOp):
            expression(node.operand)
        elif isinstance(node, ast.BoolOp):
            # Later operands execute conditionally. The current model has no
            # expression-level predicate occurrence to attach to the invocation.
            reject_conditional_calls(node.values[1:])
            for v in node.values:
                expression(v)
        elif isinstance(node, ast.Compare):
            reject_conditional_calls(node.comparators[1:])
            expression(node.left)
            for v in node.comparators:
                expression(v)
        elif isinstance(node, ast.IfExp):
            reject_conditional_calls([node.body, node.orelse])
            expression(node.test)
            expression(node.body)
            expression(node.orelse)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, (ast.Name, ast.Attribute)) or any(isinstance(a, ast.Starred) for a in node.args):
                raise Unsupported("Method/dynamic/starred call is not modeled")
            name = ast.unparse(node.func)
            if name in call_names:
                if any(k.arg is None for k in node.keywords):
                    raise Unsupported("Unpacked call arguments are not modeled")
                site = f"{node.lineno}:{node.col_offset}:invoke"
                sites[site] = {
                    "file": filename,
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "source": ast.get_source_segment(source, node),
                    "kind": "invocation",
                    "statement": owner_site,
                }
                values = ([("self", node.func.value)] if isinstance(node.func, ast.Attribute) else []) + [(str(i), a) for i, a in enumerate(node.args)] + [
                    ("kw:" + k.arg, k.value) for k in node.keywords
                ]
                for key, argument in values:
                    sites[site + ":arg:" + key] = {
                        "file": filename,
                        "line": argument.lineno,
                        "column": argument.col_offset + 1,
                        "source": ast.get_source_segment(source, argument),
                        "kind": "argument",
                        "statement": owner_site,
                    }
                    expression(argument)
                return
            elif isinstance(node.func, ast.Attribute):
                raise Unsupported("Unselected method call")
            elif name == "input":
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
        nonlocal owner_site
        for node in items:
            site = getattr(node, "_slice_site", f"{node.lineno}:{node.col_offset}:{type(node).__name__}")
            owner_site = site
            fragment = "<implicit return None>" if getattr(node, "_slice_implicit_return", False) else ast.get_source_segment(source, getattr(node, "_slice_origin", node))
            if isinstance(node, (ast.If, ast.While)) and not hasattr(node, "_slice_origin"):
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
                if heap and len(node.targets)==1 and isinstance(node.targets[0],(ast.Subscript, ast.Attribute)):
                    target=node.targets[0]
                    if isinstance(target, ast.Subscript) and isinstance(target.slice,ast.Slice):raise Unsupported("Heap slices not modeled")
                    expression(target.value)
                    if isinstance(target, ast.Subscript): expression(target.slice)
                elif not all(isinstance(t, ast.Name) for t in node.targets):
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
            elif isinstance(node, ast.AnnAssign) and node.value is None and isinstance(node.target, ast.Name):
                pass  # Local annotations are not evaluated at runtime.
            elif not isinstance(node, ast.Pass):
                raise Unsupported("Unmodeled statement: " + type(node).__name__)

    statements(body)
    return fn, body, args, sites, used_calls


def instrument(source, function_line, filename, globals_, *, future_flags=0, call_names=(), function_id=None, heap=False):
    fn, body, args, sites, calls = analyze(source, function_line, filename, call_names=call_names, heap=heap)
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

    local_names = set(args) | {n.id for statement in body for n in ast.walk(statement) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}

    class Reads(ast.NodeTransformer):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                if node.id not in local_names:
                    return locate(invoke("read_global", frame(), const(f"{node.lineno}:{node.col_offset}:global"), const(node.id), copy.deepcopy(node)), node)
                return locate(invoke("read", frame(), const(node.id), copy.deepcopy(node)), node)
            return node

        def scalar(self,node):
            return locate(invoke("heap_scalar",frame(),node),node)

        def visit_BinOp(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            if heap:new.left,new.right=self.scalar(new.left),self.scalar(new.right)
            return new

        def visit_UnaryOp(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            if heap:new.operand=self.scalar(new.operand)
            return new

        def visit_Compare(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            if heap and not all(isinstance(op,(ast.Is,ast.IsNot)) for op in node.ops):
                new.left=self.scalar(new.left)
                new.comparators=[self.scalar(x) for x in new.comparators]
            return new

        def visit_BoolOp(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            if heap:new.values=[self.scalar(x) for x in new.values]
            return new

        def visit_IfExp(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            if heap:new.test=self.scalar(new.test)
            return new

        def visit_Attribute(self, node):
            site=f"{node.lineno}:{node.col_offset}:heap_read"
            return locate(invoke("heap_attr_read",frame(),const(site),self.visit(node.value),const(node.attr)),node)

        def visit_Subscript(self, node):
            if heap:
                site=f"{node.lineno}:{node.col_offset}:heap_read"
                return locate(invoke("heap_read",frame(),const(site),self.visit(node.value),self.visit(node.slice)),node)
            return self.generic_visit(node)

        def visit_Dict(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            return locate(invoke("heap_literal",frame(),const(f"{node.lineno}:{node.col_offset}:heap_origin"),new),node) if heap else new

        def visit_List(self,node):
            new=self.generic_visit(copy.deepcopy(node))
            return locate(invoke("heap_literal",frame(),const(f"{node.lineno}:{node.col_offset}:heap_origin"),new),node) if heap else new

        def visit_Call(self, node):
            if ast.unparse(node.func) in call_names:
                site = f"{node.lineno}:{node.col_offset}:invoke"

                def argument(value, key):
                    prepared = ast.Subscript(
                        ast.Tuple([invoke("argument_begin", frame()), self.visit(value)], ast.Load()),
                        const(1),
                        ast.Load(),
                    )
                    return locate(
                        invoke("argument_value", frame(), const(site + ":arg:" + key), prepared), value
                    )

                positional = ast.Tuple([argument(a, str(i)) for i, a in enumerate(node.args)], ast.Load())
                keywords = ast.Dict(
                    [const(k.arg) for k in node.keywords],
                    [argument(k.value, "kw:" + k.arg) for k in node.keywords],
                )
                return locate(
                    invoke(
                        "call_target", frame(), const(site),
                        (invoke("method_target",frame(),argument(node.func.value,"self"),const(node.func.attr))
                         if isinstance(node.func,ast.Attribute) else copy.deepcopy(node.func)), positional, keywords
                    ),
                    node,
                )
            new = copy.deepcopy(node)
            arguments = [self.visit(a) for a in node.args]
            if heap:arguments=[self.scalar(a) for a in arguments]
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
            site = getattr(node, "_slice_site", f"{node.lineno}:{node.col_offset}:{type(node).__name__}")
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
            elif heap and isinstance(node,ast.Assign) and isinstance(node.targets[0],(ast.Subscript, ast.Attribute)):
                target=node.targets[0]
                # Helper args preserve RHS -> container -> key evaluation order.
                result.extend([begin,action("heap_attr_store" if isinstance(target, ast.Attribute) else "heap_store",node,frame(),const(site),expr(node.value),expr(target.value),(expr(target.slice) if isinstance(target, ast.Subscript) else const(target.attr)))])
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
    enter_arguments = [ast.Dict([const(n) for n in args], [ast.Name(n, ast.Load()) for n in args])]
    if function_id is not None:
        enter_arguments.append(const(function_id))
    enter = ast.Assign(
        [ast.Name(FRAME, ast.Store())],
        invoke("enter", *enter_arguments),
    )
    handler = ast.ExceptHandler(None, None, [action("raised", fn, frame()), locate(ast.Raise(), fn)])
    wrapped = ast.Try(rewrite(body), [locate(handler, fn)], [], [action("leave", fn, frame())])
    new.body = [locate(enter, fn), locate(wrapped, fn)]
    module = ast.fix_missing_locations(ast.Module([new], []))
    namespace = {}
    exec(compile(module, filename, "exec", flags=future_flags, dont_inherit=True), namespace)
    return namespace[fn.name].__code__, sites
