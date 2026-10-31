"""Exact-nodeid extraction. Complete raw candidates are kept for audit."""

import ast
import os
import subprocess
import sys
from pathlib import Path
from specmind_prep.source_index import source_index
from specmind_eval.common.fingerprint import source_environment, file_hash
from specmind_eval.common.jsonl import write_jsonl
from specmind_eval.common.fingerprint import digest, tree_digest, engine_digest
from specmind_eval.common.storage import read, write, run_lock
from specmind_eval.common.outputs import separate_output


def extract(
    repo,
    output,
    *,
    tasks=None,
    tests=None,
    exclude=None,
    documented_only=False,
    resume=False,
    timeout=1800,
    expected_commit=None,
):
    repo = Path(repo).resolve()
    output = separate_output(output, repo)
    if not (repo / "src/marshmallow").is_dir() or not (repo / "tests").is_dir():
        raise ValueError("Expected checkout with src/marshmallow and tests")
    if timeout <= 0:
        raise ValueError("Trace timeout must be positive")
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = commit.stdout.strip() if commit.returncode == 0 else None
    if expected_commit and commit != expected_commit:
        raise ValueError("Checkout commit mismatch")
    env_identity = source_environment(repo)
    index, locations = source_index(repo)
    candidates = [
        dict(row, docstring=ast.get_docstring(ast.parse(row["source"]).body[0], clean=False))
        for row in index.values()
        if row["problem_id"].startswith("marshmallow.")
        and ast.get_docstring(ast.parse(row["source"]).body[0])
    ]
    if documented_only:
        from specmind_prep.extract.documented_symbols import collect_problems, collect_documented_symbols, _problem_is_documented

        old = collect_problems(repo, repo / "src/marshmallow")
        documented = collect_documented_symbols(repo, old)
        wanted = {p.problem_id for p in old if _problem_is_documented(p, documented)}
        candidates = [p for p in candidates if p["problem_id"] in wanted]
    if tasks:
        wanted = set(tasks)
        candidates = [p for p in candidates if p["problem_id"] in wanted]
        if {p["problem_id"] for p in candidates} != wanted:
            raise ValueError("Unknown task or task without docstring")
    candidates = sorted(candidates, key=lambda p: p["problem_id"])
    if not candidates:
        raise ValueError("No candidate functions")
    chosen = {p["problem_id"] for p in candidates}
    exclude = exclude or []
    for e in exclude:
        if not e.get("pattern") or not e.get("reason"):
            raise ValueError("Every exclusion needs pattern and reason")
    test_args = list(tests or ["tests"])
    for value in test_args:
        if value.startswith("-"):
            raise ValueError("Use test paths/nodeids, not pytest options")
        path = (repo / value.split("::", 1)[0]).resolve()
        if repo not in path.parents:
            raise ValueError("Test path must stay inside checkout")
    config = {"locations": [[p, line, key] for (p, line), key in locations.items() if key in chosen]}
    fingerprint = digest(
        {
            "environment": env_identity,
            "code": engine_digest(),
            "candidates": candidates,
            "tests": test_args,
            "exclude": exclude,
            "timeout": timeout,
            "documented_only": documented_only,
        }
    )
    with run_lock(output):
        previous = read(output / "manifest.json")
        if previous and not resume:
            raise ValueError("Raw output exists; use --resume or a new directory")
        if previous and previous.get("fingerprint") != fingerprint:
            raise ValueError("Raw fingerprint mismatch; use a new directory")
        if previous and previous.get("complete"):
            for name, h in previous["files"].items():
                if file_hash(output / name) != h:
                    raise ValueError("Raw artifact mismatch: " + name)
            return read(output / "summary.json")
        manifest = {
            "schema": "raw-marshmallow-v1",
            "complete": False,
            "fingerprint": fingerprint,
            "repo": str(repo),
            "repo_commit": commit,
            "source_environment": env_identity,
            "excluded_tests": exclude,
            "settings": {
                "tasks": tasks,
                "tests": test_args,
                "timeout": timeout,
                "documented_only": documented_only,
            },
        }
        write(output / "manifest.json", manifest)
        write(output / "trace_config.json", config)
        for i, p in enumerate(candidates):
            p["task_id"] = f"Marshmallow/{i}"
        write_jsonl(output / "problems.jsonl", candidates)
        env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                [str(Path(__file__).resolve().parents[2]), str(repo / "src"), str(repo)]
            ),
            PYTHONUTF8="1",
            PYTHONIOENCODING="utf-8",
            MM_PREP_TRACE_CONFIG=str(output / "trace_config.json"),
            MM_PREP_TRACE_RESULT=str(output / "trace.json"),
        )
        env.pop("PYTEST_ADDOPTS", None)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-p",
            "specmind_prep.injected.call_trace_plugin",
        ]
        if exclude:
            command += ["-k", " and ".join(f"not ({e['pattern']})" for e in exclude)]
        command += test_args
        write(output / "command.json", {"argv": command, "cwd": str(repo)})
        (output / "trace.json").unlink(missing_ok=True)
        try:
            with (output / "trace.log").open("w", encoding="utf8") as log:
                proc = subprocess.run(
                    command, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout
                )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                "Extraction timed out; inspect trace.log, resume or use a new directory"
            ) from exc
        if proc.returncode not in (0, 1) or not (output / "trace.json").exists():
            raise ValueError("Trace failed or collection incomplete; inspect trace.log")
        trace = read(output / "trace.json")
        rows = []
        for nodeid, ids in sorted(trace["links"].items()):
            for key in ids:
                if key in chosen:
                    rows.append(
                        {
                            "problem_id": key,
                            "nodeid": nodeid,
                            "test_id": nodeid,
                            "test_path": nodeid.split("::", 1)[0],
                            "framework": "pytest-profile",
                            "trace_reports": trace["reports"].get(nodeid, {}),
                        }
                    )
        if source_environment(repo) != env_identity:
            raise ValueError("Source/environment changed during extraction")
        write_jsonl(output / "tests.jsonl", rows)
        linked = {r["problem_id"] for r in rows}
        summary = {
            "candidate_functions": len(candidates),
            "linked_functions": len(linked),
            "test_links": len(rows),
            "unique_tests": len({r["nodeid"] for r in rows}),
            "unlinked_functions": sorted(chosen - linked),
            "collected_tests": len(trace["nodeids"]),
            "trace_exitcode": proc.returncode,
            "excluded_tests": exclude,
            "meaning": "Call-phase observed links; original test failures allowed here, filtered offline next",
            "output": str(output),
        }
        write(output / "summary.json", summary)
        manifest.update(
            complete=True,
            files={
                name: file_hash(output / name)
                for name in ["problems.jsonl", "tests.jsonl", "trace.json", "summary.json", "command.json"]
            },
        )
        write(output / "manifest.json", manifest)
        return summary
