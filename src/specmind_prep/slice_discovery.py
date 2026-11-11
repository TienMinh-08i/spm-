"""Bounded runtime discovery of caller chains from freshly prepared tests."""
import json
import os
import sys
from pathlib import Path
from specmind_eval.inputs.prepared import load_prepared_dataset
from specmind_eval.inputs.dataset import pytest_nodeid
from specmind_eval.common.storage import read, write
from specmind_eval.common.fingerprint import file_hash
from specmind_prep.source_index import source_index
from specmind_prep.project import validate_project
from specmind_prep.slice_process import run_bounded


def discover(dataset, repo, project, output, *, timeout=180, tests_per_task=1, _prepared=None):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if output.exists() or output == repo or repo in output.parents:
        raise ValueError("Choose a fresh output outside source checkout")
    if type(tests_per_task) is not int or tests_per_task < 1:
        raise ValueError("tests_per_task must be positive")
    project = validate_project(read(project) if isinstance(project, (str, Path)) else project, repo)
    problems, tests, manifest = _prepared if _prepared is not None else load_prepared_dataset(dataset, repo)
    index, _ = source_index(repo, project)
    tasks, selection = [], {}
    for p in problems:
        key = p["function_id"]
        selection[key] = sorted({pytest_nodeid(t) for t in tests[key]})[:tests_per_task]
        tasks.append({"task": key, "role": "target", "file": p.get("repo_path", index[p["problem_id"]]["repo_path"]), "line": p.get("code_line", index[p["problem_id"]]["line"]),
                      "binding": p.get("_delegate_binding")})
        if key != p["problem_id"]:
            api = index[key]
            tasks.append({"task": key, "role": "api", "file": api["repo_path"], "line": api["line"]})
    nodeids = sorted({t for rows in selection.values() for t in rows})
    output.mkdir(parents=True)
    config = {"repo": str(repo), "tasks": tasks, "selection": selection, "max_calls": 10000,
              "dataset_fingerprint": manifest["fingerprint"]}
    write(output / "config.json", config)
    result_path = output / "execution.json"
    env = dict(os.environ, SPECMIND_DISCOVERY_CONFIG=str(output / "config.json"),
               SPECMIND_DISCOVERY_RESULT=str(result_path), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONNOUSERSITE="1", PYTHONUTF8="1",
               PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1]),
                                         *[str(repo / p) for p in project["python_path"]]]))
    env.pop("PYTEST_ADDOPTS", None)
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p",
               "specmind_prep.injected.slice_discovery_plugin", *nodeids]
    write(output / "command.json", command)
    execution = run_bounded(command, cwd=repo, env=env, log_path=output / "pytest.log",
                            result_path=result_path, timeout=timeout, max_bytes=8000000)
    result = read(result_path) if result_path.is_file() else {}
    if not isinstance(result, dict): result = {}
    complete = result.get("complete", False) and execution.get("exitcode") == result.get("exitcode") == 0
    rows = []
    for p in problems:
        key = p["function_id"]
        observed = [c for c in result.get("calls", []) if c["task"] == key and c["test"] in selection[key]]
        rows.append({"task": key, "target": p["problem_id"], "selected_tests": selection[key],
                     "target_observed": any(c["role"] == "target" for c in observed), "calls": observed})
    write(output / "coverage.json", rows)
    summary = {"tasks": len(rows), "observed_targets": sum(r["target_observed"] for r in rows),
               "tests": len(nodeids), "execution_complete": bool(complete),
               "context_generated": False, "execution": execution,
               "evidence_sha256": file_hash(result_path) if result_path.is_file() else None}
    write(output / "summary.json", summary)
    return summary
