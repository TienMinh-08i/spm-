"""Lower return-level boolean/conditional expressions without duplicate evaluation."""
import ast
import copy


def lower_return_expressions(body):
    counter = 0

    def generated(node, origin):
        nonlocal counter
        counter += 1
        ast.copy_location(node, origin)
        node._slice_site = f"{origin.lineno}:{origin.col_offset}:lowered:{counter}"
        node._slice_origin = origin
        return node

    def result(value, destination=None):
        nonlocal counter

        def consume(expression):
            node = (ast.Return(value=expression) if destination is None else
                    ast.Assign(targets=[ast.Name(destination, ast.Store())], value=expression))
            return [generated(node, value)]

        if isinstance(value, ast.BoolOp):
            first, rest = value.values[0], value.values[1:]
            # Allocate a distinct name before descending into a nested operand.
            counter += 1
            name = "__specmind_bool_" + str(counter)
            prefix = result(first, name)
            tail = rest[0] if len(rest) == 1 else ast.copy_location(ast.BoolOp(op=value.op, values=rest), rest[0])
            reference = ast.copy_location(ast.Name(name, ast.Load()), value)
            exit_branch = consume(reference)
            continued = result(tail, destination)
            branch = generated(ast.If(test=copy.deepcopy(reference),
                                       body=continued if isinstance(value.op, ast.And) else exit_branch,
                                       orelse=exit_branch if isinstance(value.op, ast.And) else continued), value)
            return [*prefix, branch]
        if isinstance(value, ast.IfExp):
            return [generated(ast.If(test=value.test, body=result(value.body, destination),
                                     orelse=result(value.orelse, destination)), value)]
        return consume(value)

    def visit(items):
        output = []
        for original in items:
            node = copy.deepcopy(original)
            if isinstance(node, ast.Return) and isinstance(node.value, (ast.BoolOp, ast.IfExp)):
                output.extend(result(node.value))
            else:
                for field in ("body", "orelse", "finalbody"):
                    if hasattr(node, field) and not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        setattr(node, field, visit(getattr(node, field)))
                output.append(node)
        return output

    return visit(body)
