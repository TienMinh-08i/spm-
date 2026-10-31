"""Offline test-suite evaluation, separate from assertion generation and feedback."""

from pathlib import Path
import platform
import importlib.metadata

from paper_eval_v2.common.git import verify_commit
from .evaluation import Evaluator
from paper_eval_v2.common.fingerprint import digest, tree_digest
from paper_eval_v2.common.storage import read, run_lock, write
from paper_eval_v2.common.fingerprint import evaluation_identity


def suite_outcome(evaluator, mutant):
    payload = mutant if mutant.get("mutation_problem") else mutant["function_source"]
    previous = evaluator.config.get('_suite_stop_on_kill', False)
    evaluator.config['_suite_stop_on_kill'] = True
    try:
        arm = evaluator.arm("assert True", payload)
        neutral_key = evaluator.arm_key("assert True", payload)
    finally:
        evaluator.config['_suite_stop_on_kill'] = previous
    cases = [arm["cases"].get(n) for n in evaluator.eligible]
    reached = not mutant.get("mutation_problem") or any(c and c.get("mutation_calls") for c in cases)
    killed = any(c and c.get('mutation_calls') and c["reports"].get("call") == "failed" for c in cases)
    complete = arm["status"] in {"pass", "fail"} and all(
        c and all(p in c["reports"] for p in ("setup", "call", "teardown"))
        and all(c["reports"][p] == "passed" for p in ("setup", "teardown"))
        for c in cases
    )
    return {
        "id": mutant["id"],
        "source_sha256": digest(mutant["function_source"]),
        "suite_killed": True if killed else False if complete and reached else None,
        "neutral_status": arm["status"],
        "mutation_reached": reached,
        "mutation_target": mutant.get("mutation_target"),
        "neutral_arm": neutral_key,
        "reason": arm['detail'].get('error') if not killed and not complete else None,
        "target_error": any(c and c["target_errors"] for c in cases),
    }


def evaluate_suite(run, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    manifest = read(run / "manifest.json")
    if not manifest or "config" not in manifest:
        raise ValueError("Expected a single-arm run with manifest.json")
    config = dict(manifest["config"], negative_control=False)
    repo = Path(config["repo_root"]).resolve()
    if output == run or run in output.parents or output in run.parents:
        raise ValueError("Suite output must be separate from assertion run")
    if output == repo or repo in output.parents or output in repo.parents:
        raise ValueError("Suite output must be outside target checkout")
    verify_commit(repo, config.get("expected_repo_commit"))
    identity = evaluation_identity(repo, config)
    inputs = []
    for directory in sorted((run / "functions").glob("*")):
        data, bundle = read(directory / "input.json"), read(directory / "mutants.json")
        if data is not None and bundle is not None:
            inputs.append({"key": directory.name, "input": data, "bundle": bundle})
    if not inputs:
        raise ValueError("No saved function inputs and mutant bundles found")
    fingerprint = digest({
        "inputs": inputs, "repo": tree_digest(repo), "engine": tree_digest(Path(__file__).parent),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                           if d.metadata.get("Name")),
        "pytest_timeout": config["pytest_timeout"], "batch_size": config["batch_size"],
    })
    with run_lock(output):
        saved = read(output / "manifest.json")
        if saved and saved.get("fingerprint") != fingerprint:
            raise ValueError("Suite evaluation fingerprint mismatch; choose a new output directory")
        write(output / "manifest.json", {
            "kind": "test-suite-only", "source_run": str(run),
            "source_run_fingerprint": manifest.get("fingerprint"), "fingerprint": fingerprint,
            "evaluation_identity": identity,
        })
        results = []
        for item in inputs:
            directory = output / "functions" / item["key"]
            data = item["input"]
            ev = Evaluator(data["problem"], data["tests"], repo, directory, dict(config, _evaluation_identity=identity))
            if config.get("prepared_dataset"):
                from .dataset import pytest_nodeid
                ev.eligible = [pytest_nodeid(t) for t in data["tests"]]
                ev.ready = bool(ev.eligible)
                preflight = {"status": "pass" if ev.ready else "unavailable", "origin": "prepared-dataset", "eligible_nodeids": ev.eligible}
            else:
                preflight = ev.preflight()
            rows = []
            if preflight["status"] == "pass":
                for mutant in item["bundle"]["mutants"]:
                    rows.append(suite_outcome(ev, mutant))
                    write(directory / "mutants.json", rows)
            total = len(item["bundle"]["mutants"])
            evaluated = sum(r["suite_killed"] is not None for r in rows)
            killed = sum(r["suite_killed"] is True for r in rows)
            result = {
                "function": data["problem"].get("problem_id", item["key"]),
                "preflight": preflight, "exported": total, "evaluated": evaluated,
                "unknown": total - evaluated, "killed": killed,
                "score": killed / evaluated if evaluated else None, "mutants": rows,
                "done": preflight["status"] == "pass" and evaluated == total,
                "input_sha256": digest(data), "evaluation_identity": identity,
                "bundle_sha256": digest(item['bundle']),
            }
            write(directory / "result.json", result)
            results.append(result)
        summary = {
            "functions": len(results), "exported": sum(r["exported"] for r in results),
            "evaluated": sum(r["evaluated"] for r in results),
            "killed": sum(r["killed"] for r in results),
            "unknown": sum(r["unknown"] for r in results),
            "unfinished": sum(not r["done"] for r in results),
        }
        write(output / "results.json", results)
        write(output / "summary.json", summary)
        return summary
