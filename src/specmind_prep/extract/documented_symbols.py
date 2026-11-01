"""Static RST symbol selection extracted from the original dataset tool; no test tracing."""

from __future__ import annotations

import ast
import re

from collections import defaultdict

from dataclasses import dataclass

from pathlib import Path

from typing import Iterable

tqdm = None

RST_DIRECTIVE_RE = re.compile(r"^\s*\.\.\s+([A-Za-z0-9_-]+)::\s*(.*)$")


def _progress(iterable: Iterable, desc: str, unit: str = "it") -> Iterable:
    if tqdm is None:
        return iterable
    return tqdm(iterable, desc=desc, unit=unit)


@dataclass
class Problem:
    problem_id: str
    qualified_name: str
    aliases: list[str]
    module: str
    repo_path: str
    function_name: str
    parent_name: str | None
    signature: str
    prompt: str
    nl: str
    r: str
    source: str
    source_type: str
    start_lineno: int
    end_lineno: int


class SourceExtractor(ast.NodeVisitor):
    def __init__(
        self,
        module: str,
        repo_path: str,
        lines: list[str],
        public_aliases: dict[str, set[str]],
    ) -> None:
        self.module = module
        self.repo_path = repo_path
        self.lines = lines
        self.public_aliases = public_aliases
        self.problems: list[Problem] = []
        self.class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._maybe_add_problem(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._maybe_add_problem(node)
        self.generic_visit(node)

    def _maybe_add_problem(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.body:
            return
        docstring = ast.get_docstring(node, clean=False)
        if not docstring:
            return

        parent_name = ".".join(self.class_stack) if self.class_stack else None
        local_name = ".".join([*self.class_stack, node.name]) if self.class_stack else node.name
        qualified_name = f"{self.module}.{local_name}"
        aliases = {qualified_name, *self.public_aliases.get(qualified_name, set())}

        source = ast.get_source_segment("".join(self.lines), node) or _slice_source(
            self.lines, node.lineno, node.end_lineno
        )
        body_start = node.body[0].lineno
        if _is_docstring_expr(node.body[0]) and len(node.body) > 1:
            body_start = node.body[1].lineno
        prompt_end = node.body[0].end_lineno if _is_docstring_expr(node.body[0]) else node.lineno
        prompt = _slice_source(self.lines, node.lineno, prompt_end) + "\n"
        r = _slice_source(self.lines, node.lineno, node.end_lineno, skip_until=body_start)
        signature = _extract_signature(source)

        self.problems.append(
            Problem(
                problem_id=qualified_name,
                qualified_name=qualified_name,
                aliases=sorted(aliases),
                module=self.module,
                repo_path=self.repo_path,
                function_name=node.name,
                parent_name=parent_name,
                signature=signature,
                prompt=prompt,
                nl=docstring,
                r=r,
                source=source,
                source_type="py",
                start_lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
            )
        )


def _is_docstring_expr(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _extract_signature(source: str) -> str:
    lines = source.splitlines()
    collected: list[str] = []
    seen_def = False
    for line in lines:
        stripped = line.strip()
        if not seen_def:
            if stripped.startswith("def ") or stripped.startswith("async def "):
                seen_def = True
                collected.append(line)
                if _signature_line_ends(stripped):
                    break
            continue
        collected.append(line)
        if _signature_line_ends(stripped):
            break
    return "\n".join(collected).strip()


def _signature_line_ends(stripped_line: str) -> bool:
    return stripped_line.split("#", 1)[0].rstrip().endswith(":")


def _slice_source(
    lines: list[str], start_lineno: int, end_lineno: int | None, skip_until: int | None = None
) -> str:
    start = (skip_until or start_lineno) - 1
    end = end_lineno or start_lineno
    return "".join(lines[start:end]).rstrip()


def _module_name_from_path(package_root: Path, file_path: Path, package_name=None) -> str:
    rel = file_path.relative_to(package_root).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join((package_name or package_root.name, *parts))


def _repo_relative(repo_root: Path, file_path: Path) -> str:
    return file_path.relative_to(repo_root).as_posix()


def _iter_py_files(root: Path) -> Iterable[Path]:
    yield from root.rglob("*.py")


def collect_public_aliases(repo_root: Path, package_root=None, package_name=None) -> dict[str, set[str]]:
    if package_root is None:
        from specmind_prep.project import infer_project
        project = infer_project(repo_root)
        package_name = project['packages'][0]
        package_root = repo_root / project['source_root'] / package_name.replace('.', '/')
    package_name = package_name or package_root.name
    init_path = package_root / '__init__.py' 
    aliases: dict[str, set[str]] = defaultdict(set)
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return aliases

    import_map: dict[str, str] = {}
    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                import_map[local] = (package_name + "." if node.level else "") + node.module + "." + alias.name
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                exported.add(elt.value)

    for name in exported:
        target = import_map.get(name)
        if target:
            aliases[target].add(f"{package_name}.{name}")
    return aliases


def collect_problems(repo_root: Path, package_root: Path, package_name=None) -> list[Problem]:
    public_aliases = collect_public_aliases(repo_root, package_root, package_name)
    problems: list[Problem] = []
    paths = [path for path in _iter_py_files(package_root) if "__pycache__" not in path.parts]
    for path in _progress(paths, "Collecting source functions", "file"):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        extractor = SourceExtractor(
            module=_module_name_from_path(package_root, path, package_name),
            repo_path=_repo_relative(repo_root, path),
            lines=text.splitlines(keepends=True),
            public_aliases=public_aliases,
        )
        extractor.visit(tree)
        problems.extend(extractor.problems)
    return problems


def _normalize_doc_symbol(name: str, currentmodule: str | None) -> str | None:
    symbol = name.strip().lstrip("~")
    if not symbol or symbol.startswith(":"):
        return None
    symbol = symbol.split()[0]
    if currentmodule and "." not in symbol:
        symbol = f"{currentmodule}.{symbol}"
    return symbol


def collect_documented_symbols(repo_root: Path, problems: list[Problem], docs_dir="docs", packages=None) -> set[str]:
    docs_root = repo_root / docs_dir
    documented: set[str] = set()
    by_module: dict[str, set[str]] = defaultdict(set)
    public_by_module: dict[str, set[str]] = defaultdict(set)
    public_by_class: dict[str, set[str]] = defaultdict(set)
    public_aliases: set[str] = set()

    for problem in problems:
        by_module[problem.module].add(problem.problem_id)
        for alias in problem.aliases:
            if alias not in {problem.problem_id, problem.qualified_name}:
                public_aliases.add(alias)
        is_public = not problem.function_name.startswith("_")
        if problem.parent_name:
            if is_public:
                public_by_class[f"{problem.module}.{problem.parent_name}"].add(problem.problem_id)
        elif is_public:
            public_by_module[problem.module].add(problem.problem_id)

    doc_paths = list(docs_root.rglob("*.rst"))
    for path in _progress(doc_paths, "Collecting documented API", "file"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        currentmodule: str | None = None
        i = 0
        while i < len(lines):
            match = RST_DIRECTIVE_RE.match(lines[i])
            if not match:
                i += 1
                continue

            directive, value = match.groups()
            directive = directive.lower()
            value = value.strip()
            base_indent = len(lines[i]) - len(lines[i].lstrip(" "))
            has_members = False
            exclude_members: set[str] = set()

            j = i + 1
            while j < len(lines):
                raw = lines[j]
                stripped = raw.strip()
                indent = len(raw) - len(raw.lstrip(" "))
                if stripped and indent <= base_indent:
                    break
                if stripped == ":members:":
                    has_members = True
                elif stripped.startswith(":exclude-members:"):
                    members = stripped.split(":", 2)[-1].strip()
                    exclude_members.update(x.strip() for x in members.split(",") if x.strip())
                j += 1

            if directive == "currentmodule":
                currentmodule = value or None
            elif directive in {"autofunction", "automethod", "autoclass", "autoattribute"}:
                symbol = _normalize_doc_symbol(value, currentmodule)
                if symbol:
                    documented.add(symbol)
                    if directive == "autoclass" and has_members:
                        documented.update(public_by_class.get(symbol, set()))
            elif directive == "automodule":
                module_name = _normalize_doc_symbol(value, currentmodule)
                if module_name:
                    documented.add(module_name)
                    if has_members:
                        for problem_id in public_by_module.get(module_name, set()):
                            if problem_id.rsplit(".", 1)[-1] not in exclude_members:
                                documented.add(problem_id)
                        if module_name in (packages or {p.module.split(".")[0] for p in problems}):
                            documented.update(
                                alias for alias in public_aliases if alias.startswith(module_name + ".")
                            )
            elif directive == "autosummary":
                k = i + 1
                while k < j:
                    stripped = lines[k].strip()
                    if stripped and not stripped.startswith(":"):
                        symbol = _normalize_doc_symbol(stripped, currentmodule)
                        if symbol:
                            documented.add(symbol)
                    k += 1
            i = j

    return documented


def _problem_is_documented(problem: Problem, documented_symbols: set[str]) -> bool:
    candidates = {
        problem.problem_id,
        problem.qualified_name,
        *problem.aliases,
    }
    return any(candidate in documented_symbols for candidate in candidates)
