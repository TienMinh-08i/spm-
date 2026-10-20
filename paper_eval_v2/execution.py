from __future__ import annotations
import base64
import json
import os
import subprocess
import sys
import tempfile
from time import monotonic
from pathlib import Path
from typing import Any

from .dataset import pytest_nodeid

WORKSPACE = Path.cwd()
POSTCONDITION_FAILURE_MARKER = "SPECMIND_DATASET_POSTCONDITION_FAILURE"
DATASET_PYTEST_PLUGIN = Path(__file__).with_name("pytest_plugin.py").read_text(encoding="utf-8")


def b64_json(data: Any) -> str:
    return base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")


def b64_text(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def pytest_nodeid_batches(nodeids: list[str], batch_size: int, max_command_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for nodeid in nodeids:
        nodeid_chars = len(nodeid) + 3
        if current and (len(current) >= batch_size or current_chars + nodeid_chars > max_command_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(nodeid)
        current_chars += nodeid_chars
    if current:
        batches.append(current)
    return batches


def log_indicates_invalid_nodeid(log: str) -> bool:
    invalid_markers = [
        "ERROR: not found:",
        "ERROR: file or directory not found:",
        "ERROR: found no collectors for",
        "no match in any of",
    ]
    return any(marker in log for marker in invalid_markers)


def pythonpath_for(repo_root: Path) -> str:
    candidates = [
        repo_root,
        repo_root / "src",
        repo_root / "keras",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    existing.append(os.environ.get("PYTHONPATH", ""))
    return os.pathsep.join(item for item in existing if item)


def pythonpath_for_mode(repo_root: Path, package_mode: str) -> str:
    if package_mode == "installed":
        return os.environ.get("PYTHONPATH", "")
    return pythonpath_for(repo_root)


def run_pytest_postcondition(
    *,
    problem: dict[str, Any],
    tests: list[dict[str, Any]],
    postcondition: str,
    repo_root: Path,
    timeout: int,
    batch_size: int,
    max_command_chars: int,
    mutant_source: str | None = None,
    mutation_problem: dict[str, Any] | None = None,
    package_mode: str = "repo",
    test_nodeid_mode: str = "path",
    allow_partial_nodeids: bool = False,
    collect_only: bool = False,
    collect_all: bool = False,
    stop_requested: Any | None = None,
    suite_stop_on_kill: bool = False,
    total_timeout: float = 300,
    max_processes: int = 64,
) -> tuple[str, dict[str, Any]]:
    if not tests:
        return "error", {"error": "No linked tests found.", "logs": []}

    nodeids = [pytest_nodeid(item, mode=test_nodeid_mode) for item in tests]
    with tempfile.TemporaryDirectory(prefix="paper_eval_dataset_") as tmp:
        tmp_dir = Path(tmp)
        plugin_path = tmp_dir / "paper_eval_dataset_plugin.py"
        plugin_path.write_text(DATASET_PYTEST_PLUGIN, encoding="utf-8")

        env = os.environ.copy()
        env.pop('SPECMIND_SUITE_STOP_ON_KILL', None)
        if suite_stop_on_kill:
            env['SPECMIND_SUITE_STOP_ON_KILL'] = '1'
        env.pop("SPECMIND_DATASET_MUTANT_SOURCE_B64", None)
        env.pop('SPECMIND_MUTATION_PROBLEM_B64', None)
        env.pop("PYTEST_ADDOPTS", None)
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in [str(tmp_dir), pythonpath_for_mode(repo_root, package_mode), str(Path(__file__).resolve().parent.parent)] if item
        )
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["SPECMIND_DATASET_PROBLEM_JSON_B64"] = b64_json(problem)
        env["SPECMIND_DATASET_POSTCONDITION_B64"] = b64_text(postcondition)
        events_path = tmp_dir / "events.jsonl"
        env["SPECMIND_DATASET_EVENTS"] = str(events_path)

        def evidence():
            events = []
            if events_path.exists():
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
            return {"events": events}

        if mutant_source is not None:
            env["SPECMIND_DATASET_MUTANT_SOURCE_B64"] = b64_text(mutant_source)
            if mutation_problem is not None:
                env['SPECMIND_MUTATION_PROBLEM_B64'] = b64_json(mutation_problem)

        logs: list[str] = []
        invalid_nodeids: list[str] = []
        valid_nodeids: list[str] = []
        pending_batches = pytest_nodeid_batches(nodeids, batch_size, max_command_chars)
        overall_status = "pass"
        started = monotonic()
        process_count = 0
        collection_errors = []

        def file_key(nodeid):
            name = nodeid.split('::', 1)[0].replace('\\', '/')
            return os.path.normcase(str((repo_root / name).resolve()))

        def details(**extra):
            return {**evidence(), 'logs': logs, 'process_count': process_count,
                    'collection_errors': collection_errors,
                    'invalid_nodeids': invalid_nodeids, 'valid_nodeids': valid_nodeids,
                    'requested_nodeids': nodeids, **extra}

        while pending_batches:
            remaining = total_timeout - (monotonic() - started)
            if process_count >= max_processes or remaining <= 0:
                return 'budget_exhausted', details(error='Per-mutant process/time budget exhausted')
            if stop_requested is not None and stop_requested():
                return "budget_exhausted", {
                    **evidence(),
                    "error": "Function time budget exhausted before next pytest batch",
                    "logs": logs,
                    "invalid_nodeids": invalid_nodeids,
                    "valid_nodeids": valid_nodeids,
                    "requested_nodeids": nodeids,
                }
            batch = pending_batches.pop(0)
            command = [sys.executable, "-m", "pytest", "-q"]
            if collect_only:
                command.append("--collect-only")
            else:
                command.extend(["-p", "paper_eval_dataset_plugin"])
            if test_nodeid_mode == "module":
                command.append("--pyargs")
            command.extend(batch)
            previous_events = len(evidence()['events'])
            process_count += 1
            try:
                proc = subprocess.run(
                    command,
                    cwd=repo_root if package_mode == "repo" else WORKSPACE,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=min(timeout, remaining) if timeout else remaining,
                )
            except subprocess.TimeoutExpired as exc:
                output = (
                    exc.stdout.decode("utf-8", errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else (exc.stdout or "")
                )
                log = f"TIMEOUT after {timeout}s\n{output}"
                logs.append(log)
                return "timeout", {
                    **details(),
                    "logs": logs,
                    **evidence(),
                    "postcondition_marker": POSTCONDITION_FAILURE_MARKER in log,
                }

            log = proc.stdout or ""
            logs.append(log)
            new_events = evidence()['events'][previous_events:]
            if suite_stop_on_kill:
                cases = _case_evidence({'events': new_events})
                if any(c['mutation_calls'] and c['reports'].get('call') == 'failed'
                       for n, c in cases.items() if n in nodeids):
                    return 'fail', details(early_stop='confirmed_suite_kill')
            errors = [e for e in new_events if e.get('kind') == 'collection_error']
            if errors:
                collection_errors.extend(errors)
                failed_files = {file_key(e.get('collector_nodeid', '')) for e in errors}
                # A collection failure affects every selector in that file. Retrying
                # its individual test names cannot repair the failed module import.
                all_pending = batch + [n for group in pending_batches for n in group]
                affected = [n for n in all_pending if file_key(n) in failed_files]
                if not affected:  # e.g. conftest/package import failure
                    return 'collection_error', details(error='Collection failed outside a selected test file')
                remaining_nodes = [n for n in all_pending if file_key(n) not in failed_files]
                pending_batches = pytest_nodeid_batches(remaining_nodes, batch_size, max_command_chars)
                continue
            if log_indicates_invalid_nodeid(log):
                if len(batch) > 1:
                    midpoint = len(batch) // 2
                    pending_batches.insert(0, batch[midpoint:])
                    pending_batches.insert(0, batch[:midpoint])
                else:
                    invalid_nodeids.extend(batch)
                continue
            if proc.returncode != 0:
                if collect_all and proc.returncode == 1:
                    overall_status = "fail"
                    valid_nodeids.extend(batch)
                    continue
                return "error" if collect_only or proc.returncode != 1 else "fail", {
                    **evidence(),
                    "logs": logs,
                    "postcondition_marker": POSTCONDITION_FAILURE_MARKER in log,
                    "invalid_nodeids": invalid_nodeids,
                    "valid_nodeids": valid_nodeids,
                    "requested_nodeids": nodeids,
                }
            valid_nodeids.extend(batch)

        if collection_errors:
            return 'collection_error', details(error='Some test files failed during collection')
        if invalid_nodeids and len(invalid_nodeids) == len(nodeids):
            return "error", {
                **evidence(),
                "error": "No valid pytest nodeids found.",
                "logs": logs,
                "invalid_nodeids": invalid_nodeids,
            }
        if invalid_nodeids and not allow_partial_nodeids:
            return "error", {
                **evidence(),
                "error": "Some requested pytest nodeids were invalid; correctness would only cover a subset.",
                "logs": logs,
                "invalid_nodeids": invalid_nodeids,
                "valid_nodeids": valid_nodeids,
                "requested_nodeids": nodeids,
            }
        return overall_status, {
            **details(),
            **evidence(),
            "logs": logs,
            "invalid_nodeids": invalid_nodeids,
            "valid_nodeids": valid_nodeids,
            "requested_nodeids": nodeids,
            "collected_nodeid_count" if collect_only else "executed_nodeid_count": len(valid_nodeids),
            "invalid_nodeid_count": len(invalid_nodeids),
        }


def _case_evidence(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for event in detail.get("events", []):
        nodeid = event.get("report_nodeid") or event.get("nodeid")
        if not nodeid:
            continue
        case = cases.setdefault(
            nodeid, {"checks": 0, "violations": 0, "runtime_errors": 0, "target_errors": 0,
                     "mutation_calls": 0, "mutation_errors": 0, "reports": {}}
        )
        kind = event.get("kind")
        if kind == "report":
            case["reports"][event["when"]] = event["outcome"]
        elif kind in {"post_check", "assertion_violation", "post_runtime_error", "target_runtime_error", 'mutation_call', 'mutation_runtime_error'}:
            case[
                {
                    "post_check": "checks",
                    "assertion_violation": "violations",
                    "post_runtime_error": "runtime_errors",
                    "target_runtime_error": "target_errors",
                    'mutation_call': 'mutation_calls',
                    'mutation_runtime_error': 'mutation_errors',
                }[kind]
            ] += 1
    for case in cases.values():
        case["passed"] = all(case["reports"].get(p) == "passed" for p in ("setup", "call", "teardown"))
    return cases
