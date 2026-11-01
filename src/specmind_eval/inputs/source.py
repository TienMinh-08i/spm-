from __future__ import annotations
import ast
import textwrap


def get_function_source(
    source_code: str,
    func_name: str,
    lineno: int | None = None,
) -> str:
    """Lấy source code của một hàm cụ thể từ file.

    Nếu có nhiều hàm trùng tên (overload stubs), truyền ``lineno`` để chọn đúng định nghĩa.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        raise ValueError(f"Cannot parse source: {e}")

    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                if lineno is None or node.lineno == lineno:
                    matches.append(node)

    if not matches:
        raise ValueError(f"Function '{func_name}' not found in source")

    node = matches[0] if lineno is not None else matches[-1]
    lines = source_code.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    return "".join(lines[start:end])


def strip_function_docstring(source: str) -> str:
    import ast

    module = ast.parse(textwrap.dedent(source).strip() + "\n")
    if not module.body or not isinstance(module.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return textwrap.dedent(source).strip()
    node = module.body[0]
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        node.body = node.body[1:]
    return ast.dump(node)


def function_ast_dump(source: str) -> str:
    import ast

    module = ast.parse(textwrap.dedent(source).strip() + "\n")
    if module.body and isinstance(module.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ast.dump(module.body[0])
    return ast.dump(module)


def source_match(expected: str, actual: str) -> str:
    try:
        if function_ast_dump(expected) == function_ast_dump(actual):
            return "exact_ast"
        if strip_function_docstring(expected) == strip_function_docstring(actual):
            return "same_except_docstrings"
    except SyntaxError:
        return "parse_error"
    return "mismatch"
