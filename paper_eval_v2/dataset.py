from __future__ import annotations
import ast
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    project: str
    directory: Path
    problems_file: str
    tests_file: str
    curated_file: str | None = None
    default_repo_root: Path | None = None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_dataset(spec: DatasetSpec) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    problems_path = spec.directory / spec.problems_file
    tests_path = spec.directory / spec.tests_file
    if not problems_path.exists():
        raise FileNotFoundError(f"Missing problems file: {problems_path}")
    if not tests_path.exists():
        raise FileNotFoundError(f"Missing tests file: {tests_path}")

    problems = read_jsonl(problems_path)
    tests_by_problem_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in read_jsonl(tests_path):
        problem_id = item.get("problem_id")
        if problem_id:
            tests_by_problem_id[problem_id].append(item)

    for index, problem in enumerate(problems):
        problem.setdefault("task_id", f"{spec.name}/{index}")
        problem.setdefault(
            "entry_point", problem.get("function_name") or str(problem.get("problem_id", "")).split(".")[-1]
        )
        problem.setdefault("prompt", problem.get("source") or "")
        problem.setdefault("canonical_solution", "")
    return problems, tests_by_problem_id


def function_code(problem: dict[str, Any]) -> str:
    source = problem.get("source")
    if source:
        return normalize_function_source(str(source))
    return normalize_function_source(
        str(problem.get("prompt", "")) + str(problem.get("canonical_solution", ""))
    )


def normalize_function_source(source: str) -> str:
    text = source.strip()
    try:
        tree = ast.parse(text + "\n")
    except SyntaxError:
        return text + "\n"
    if len(tree.body) != 1:
        return text + "\n"
    node = tree.body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return text + "\n"
    # Some dataset rows have an empty implementation represented as the same
    # docstring twice. Keep the real docstring and drop extra leading string
    # literals so the evaluated source matches the checkout.
    while (
        len(node.body) > 1
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
        and isinstance(node.body[1], ast.Expr)
        and isinstance(node.body[1].value, ast.Constant)
        and isinstance(node.body[1].value.value, str)
    ):
        del node.body[1]

    return ast.unparse(node).strip() + "\n"


def _test_suffix(name: str) -> str:
    """Legacy IDs use dots for class nesting, never for parameter values."""
    prefix, bracket, parameters = name.partition("[")
    return prefix.replace(".", "::") + bracket + parameters


def pytest_nodeid(test: dict[str, Any], *, mode: str = "path") -> str:
    if test.get("nodeid"):
        return str(test["nodeid"])
    if mode == "module":
        test_id = str(test.get("test_id", ""))
        if "::" in test_id:
            module_name, test_name_part = test_id.split("::", 1)
            return module_name + "::" + _test_suffix(test_name_part)

    test_path = test.get("test_path")
    test_name = test.get("test_name")
    class_name = test.get("class_name")
    if test_path and test_name:
        nodeid = str(test_path).replace("\\", "/")
        if class_name:
            nodeid += f"::{class_name}"
        nodeid += f"::{test_name}"
        return nodeid

    test_id = str(test.get("test_id", ""))
    if "::" not in test_id:
        return test_id
    module_name, test_name_part = test_id.split("::", 1)
    path = module_name.replace(".", "/") + ".py"
    return path + "::" + _test_suffix(test_name_part)


def repo_path_for_problem(problem: dict[str, Any], repo_root: Path) -> str:
    explicit = problem.get("repo_path")
    if explicit:
        return str(explicit).replace("\\", "/")

    candidates: list[str] = []
    module = problem.get("module")
    if module:
        module_path = str(module).replace(".", "/")
        if module_path.endswith("/__init__"):
            module_path = module_path[: -len("/__init__")]
        candidates.extend([f"src/{module_path}.py", f"{module_path}.py", f"{module_path}/__init__.py"])

    problem_id = str(problem.get("problem_id") or problem.get("qualified_name") or "")
    parts = problem_id.split(".") if problem_id else []
    for split_at in range(len(parts) - 1, 0, -1):
        module_path = "/".join(parts[:split_at])
        candidates.extend([f"src/{module_path}.py", f"{module_path}.py", f"{module_path}/__init__.py"])

    for candidate in candidates:
        if (repo_root / candidate).exists():
            return candidate
    return str(explicit or "").replace("\\", "/")


def load(problems_path, tests_path):
    spec = DatasetSpec("dataset", "", problems_path.parent, problems_path.name, str(tests_path.resolve()))
    problems, tests = load_dataset(spec)
    ids = [str(p.get("problem_id") or p["task_id"]) for p in problems]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate function IDs in dataset")
    return problems, tests
