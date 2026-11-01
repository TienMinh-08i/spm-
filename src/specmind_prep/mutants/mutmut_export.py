from __future__ import annotations
import ast
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import importlib.metadata
import textwrap
from pathlib import Path
from typing import Any

from specmind_eval.inputs.dataset import DatasetSpec, function_code, repo_path_for_problem
from specmind_eval.inputs.source import get_function_source
from specmind_eval.evaluate.pytest_runner import pythonpath_for


def remove_workspace(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"Refusing removal outside export directory: {path}")
    if path.exists():
        shutil.rmtree(path)


def safe_name(value: Any) -> str:
    text = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)[:180]


def load_exported_mutants(export_dir: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    mutants: list[dict[str, str]] = []
    for item in manifest.get("mutants", []):
        rel = item.get("file")
        if not rel:
            continue
        path = export_dir / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        source = data.get("function_source")
        if source:
            mutants.append(
                {
                    "id": str(data.get("id") or item.get("id") or len(mutants)),
                    "function_source": source,
                    "diff": str(data.get("diff") or ""),
                }
            )
    return mutants


def import_prelude_for_problem(problem: dict[str, Any], repo_root: Path) -> str:
    path_text = repo_path_for_problem(problem, repo_root)
    if not path_text:
        return ""
    path = repo_root / path_text
    if not path.exists():
        return ""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return ""

    lines = source.splitlines()
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            start = getattr(node, "lineno", 0)
            end = getattr(node, "end_lineno", start)
            if start and end:
                chunks.append("\n".join(lines[start - 1 : end]))
    return "\n".join(chunks)


def discover_isolated_mutants(workspace: Path, max_probe: int) -> tuple[list[str], dict[str, Any]]:
    statuses = ["survived", "killed", "timeout", "suspicious", "skipped", "untested"]
    ids: list[str] = []
    seen: set[str] = set()
    by_status: dict[str, list[str]] = {}
    commands: dict[str, dict[str, Any]] = {}
    for status in statuses:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "mutmut", "result-ids", status],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            commands[status] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        parsed = parse_mutmut_result_ids(result.stdout or "")
        by_status[status] = parsed
        commands[status] = {
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[-1000:],
            "stderr": (result.stderr or "")[-1000:],
        }
        if result.returncode == 0:
            for mutant_id in parsed:
                if mutant_id not in seen:
                    seen.add(mutant_id)
                    ids.append(mutant_id)

    fallback = None
    if not ids:
        result = subprocess.run(
            [sys.executable, "-m", "mutmut", "results"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fallback_ids = parse_mutmut_result_ids(result.stdout or "")
        fallback = {
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[-2000:],
            "stderr": (result.stderr or "")[-1000:],
            "ids": fallback_ids,
        }
        for mutant_id in fallback_ids:
            if mutant_id not in seen:
                seen.add(mutant_id)
                ids.append(mutant_id)

    detail = {
        "discovery_method": "mutmut result-ids",
        "discovered_by_status": by_status,
        "discovery_commands": commands,
        "fallback_results": fallback,
    }
    if not any(item.get("returncode") == 0 for item in commands.values()):
        raise RuntimeError("mutmut result-ids failed for every status; not a zero-mutant result")
    return ids, detail


def parse_mutmut_result_ids(output: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or not re.fullmatch(r"\d+(?:-\d+)?(?:[\s,]+\d+(?:-\d+)?)*", stripped):
            continue
        for token in re.split(r"[\s,]+", stripped):
            if not token:
                continue
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                start, end = int(start_text), int(end_text)
                if end < start:
                    continue
                values = range(start, end + 1)
            else:
                values = [int(token)]
            for value in values:
                text = str(value)
                if text not in seen:
                    seen.add(text)
                    ids.append(text)
    return ids


def write_isolated_mutant_json(
    *,
    export_dir: Path,
    mutant_id: str,
    function_source: str,
    diff: str,
) -> dict[str, str]:
    mutants_dir = export_dir / "mutants"
    mutants_dir.mkdir(parents=True, exist_ok=True)
    path = mutants_dir / f"{mutant_id}.json"
    data = {"id": mutant_id, "function_source": function_source, "diff": diff}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"id": mutant_id, "file": f"mutants/{mutant_id}.json"}


def export_mutmut_function_mutants(
    *,
    spec: DatasetSpec,
    problem: dict[str, Any],
    repo_root: Path,
    args: argparse.Namespace,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    if os.name != "posix":
        raise RuntimeError(
            "Native mutmut export requires Linux/Docker; use verified existing exports on Windows"
        )
    if importlib.metadata.version("mutmut") != "2.5.1":
        raise RuntimeError(
            "Native export requires mutmut==2.5.1; existing exports can be reused without mutmut"
        )

    function_source = textwrap.dedent(function_code(problem)).strip() + "\n"
    entrypoint = str(problem.get("entry_point") or problem.get("function_name"))
    run_dir = Path(args._run_dir)
    export_dir = (
        run_dir
        / "mutmut_function_exports"
        / safe_name(problem.get("task_id"))
        / safe_name(problem.get("problem_id") or problem.get("qualified_name") or entrypoint)
    )
    workspace = export_dir / "workspace"
    if workspace.exists():
        remove_workspace(workspace, export_dir)
    (workspace / "tests").mkdir(parents=True, exist_ok=True)

    prelude = import_prelude_for_problem(problem, repo_root)
    target_source = "\n".join(
        item
        for item in [
            "from __future__ import annotations",
            prelude,
            function_source,
        ]
        if item.strip()
    )
    target_path = workspace / "target_func.py"
    target_path.write_text(target_source, encoding="utf-8")
    (workspace / "tests" / "test_import.py").write_text(
        "def test_import_target_func():\n"
        "    import target_func\n"
        f"    assert hasattr(target_func, {entrypoint!r})\n",
        encoding="utf-8",
    )
    export_env = os.environ.copy()
    export_env["PYTHONPATH"] = pythonpath_for(repo_root, problem.get("_python_path"))
    baseline = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=workspace,
        env=export_env,
        capture_output=True,
        text=True,
        timeout=args.mutmut_run_timeout,
    )
    if baseline.returncode:
        raise RuntimeError("Mutmut import baseline failed: " + (baseline.stdout + baseline.stderr)[-4000:])
    subprocess.run(["git", "init", "-q"], cwd=workspace, capture_output=True, text=True, timeout=30)
    subprocess.run(
        ["git", "add", "target_func.py", "tests/test_import.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=paper-eval@example.invalid",
            "-c",
            "user.name=paper-eval",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
    )

    command = [
        sys.executable,
        "-m",
        "mutmut",
        "run",
        "--paths-to-mutate",
        "target_func.py",
        "--runner",
        f'"{sys.executable}" -m pytest tests -q --tb=no',
        "--tests-dir",
        "tests",
    ]
    try:
        run_result = subprocess.run(
            command,
            cwd=workspace,
            env=export_env,
            capture_output=True,
            text=True,
            timeout=args.mutmut_run_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        manifest = {
            "engine": "mutmut-function",
            "function_name": entrypoint,
            "file_path": "target_func.py",
            "original_function_source": function_source,
            "total_discovered": 0,
            "total_exported": 0,
            "error": f"mutmut run timed out after {args.mutmut_run_timeout}s",
            "stdout": (
                (exc.stdout or b"").decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )[-4000:],
        }
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return "dataset-pytest-mutmut-function", [], manifest

    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "mutmut.stdout.log").write_text(run_result.stdout or "", encoding="utf-8")
    (export_dir / "mutmut.stderr.log").write_text(run_result.stderr or "", encoding="utf-8")
    if run_result.returncode not in (0, 1, 2, 4, 8):
        manifest = {
            "engine": "mutmut-function",
            "function_name": entrypoint,
            "file_path": "target_func.py",
            "original_function_source": function_source,
            "total_discovered": 0,
            "total_exported": 0,
            "error": f"mutmut run failed with exit={run_result.returncode}",
            "stdout": (run_result.stdout or "")[-4000:],
            "stderr": (run_result.stderr or "")[-4000:],
        }
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return "dataset-pytest-mutmut-function", [], manifest

    max_probe = max(args.max_mutants * 20, 200)
    mutant_ids, discovery_detail = discover_isolated_mutants(workspace, max_probe=max_probe)
    pristine_workspace = export_dir / "workspace_pristine"
    if pristine_workspace.exists():
        remove_workspace(pristine_workspace, export_dir)
    shutil.copytree(workspace, pristine_workspace)

    apply_root = export_dir / "apply_workspaces"
    if apply_root.exists():
        remove_workspace(apply_root, export_dir)
    apply_root.mkdir(parents=True, exist_ok=True)

    exported: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    for mutant_id in mutant_ids:
        if len(exported) >= args.max_mutants:
            break
        apply_workspace = apply_root / safe_name(mutant_id)
        if apply_workspace.exists():
            remove_workspace(apply_workspace, export_dir)
        shutil.copytree(pristine_workspace, apply_workspace)
        apply_result = subprocess.run(
            [sys.executable, "-m", "mutmut", "apply", mutant_id],
            cwd=apply_workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if apply_result.returncode != 0:
            skipped.append(
                {
                    "id": mutant_id,
                    "reason": "apply_failed",
                    "returncode": apply_result.returncode,
                    "stdout": (apply_result.stdout or "")[-1000:],
                    "stderr": (apply_result.stderr or "")[-1000:],
                }
            )
            remove_workspace(apply_workspace, export_dir)
            continue
        mutated_target_source = (apply_workspace / "target_func.py").read_text(encoding="utf-8")
        remove_workspace(apply_workspace, export_dir)
        try:
            mutated_function_source = get_function_source(mutated_target_source, entrypoint, lineno=None)
        except ValueError as exc:
            skipped.append({"id": mutant_id, "reason": "extract_failed", "error": str(exc)})
            continue
        if mutated_function_source.strip() == function_source.strip():
            skipped.append({"id": mutant_id, "reason": "unchanged_function"})
            continue
        show_result = subprocess.run(
            [sys.executable, "-m", "mutmut", "show", mutant_id],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        exported.append(
            write_isolated_mutant_json(
                export_dir=export_dir,
                mutant_id=mutant_id,
                function_source=mutated_function_source,
                diff=show_result.stdout if show_result.returncode == 0 else "",
            )
        )

    manifest = {
        "engine": "mutmut-function",
        "repo": spec.project,
        "function_name": entrypoint,
        "file_path": "target_func.py",
        "original_repo_path": repo_path_for_problem(problem, repo_root),
        "original_function_source": function_source,
        "total_discovered": len(mutant_ids),
        "total_exported": len(exported),
        "mutmut_discovery": discovery_detail,
        "skipped_mutants": skipped,
        "mutants": exported,
        "stdout": (run_result.stdout or "")[-4000:],
        "stderr": (run_result.stderr or "")[-4000:],
    }
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    mutants = load_exported_mutants(export_dir, manifest)
    return "dataset-pytest-mutmut-function", mutants, manifest
