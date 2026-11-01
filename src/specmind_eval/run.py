"""The only orchestration entrypoint. Every function has its own checkpoint."""

from __future__ import annotations

import importlib.metadata
import platform
from pathlib import Path
from time import perf_counter

from specmind_eval.inputs.dataset import function_code, load, pytest_nodeid, repo_path_for_problem
from specmind_eval.context.callgraph import CallGraph, build_context_pack
from specmind_eval.common.git import verify_commit
from specmind_eval.evaluate.evaluator import Evaluator
from specmind_eval.legacy_mutants import export_bundle_digest, prepare
from specmind_eval.inputs.prepared import load_prepared_dataset
from specmind_eval.inputs.prepared import load_prepared_mutants
from specmind_eval.report import report
from specmind_eval.generate.loop import generate
from specmind_eval.common.fingerprint import digest, tree_digest, engine_digest
from specmind_eval.common.storage import read, run_lock, write
from specmind_eval.common.fingerprint import evaluation_identity


def run(config, output, resume=False):
    with run_lock(output):
        return _run(config, output, resume)


def _run(config, output, resume=False):
    output = Path(output).resolve()
    repo = Path(config["repo_root"]).resolve()
    if not repo.is_dir():
        raise ValueError(f"Missing target checkout: {repo}")
    if output == repo or repo in output.parents:
        raise ValueError("Run output must be outside target checkout")
    verify_commit(repo, config.get("expected_repo_commit"))
    context_mode = config.get("context_mode", "none")
    graph = None
    if context_mode != "none":
        if not config.get("call_graph_dir"):
            raise ValueError("call_graph_dir is required for enabled context")
        graph = CallGraph.load(Path(config["call_graph_dir"]), repo)
    corpus = None
    if config.get("prepared_dataset") or config.get("prepared_mutants"):
        if not config.get("prepared_dataset") or not config.get("prepared_mutants"):
            raise ValueError("prepared_dataset and prepared_mutants must be supplied together")
        problems, tests_by_id, dataset_manifest = load_prepared_dataset(config["prepared_dataset"], repo)
        config = dict(config, python_path=(dataset_manifest.get("project") or {}).get("python_path"))
        corpus, corpus_manifest = load_prepared_mutants(config["prepared_mutants"], repo, dataset_manifest)
        if any((p.get("problem_id") or p["task_id"]) not in corpus for p in problems):
            raise ValueError("Prepared corpus missing functions")
    else:
        problems, tests_by_id = load(Path(config["problems"]), Path(config["tests"]))
    if config.get("tasks"):
        wanted = set(config["tasks"])
        problems = [p for p in problems if (p.get("problem_id") or p["task_id"]) in wanted]
        found = {p.get("problem_id") or p["task_id"] for p in problems}
        if found != wanted:
            raise ValueError(f"Unknown task IDs: {sorted(wanted - found)}")
    if not problems:
        raise ValueError("No functions selected")
    packs = {}
    if graph:
        for problem in problems:
            key = str(problem.get("problem_id") or problem["task_id"])
            pack = build_context_pack(
                graph,
                key,
                mode=context_mode,
                budget=config.get("context_budget", 8000),
                depth=config.get("context_depth", 1),
            )
            status = pack["metadata"]["status"]
            if status in {"missing_target", "budget_too_small"}:
                raise ValueError(f"Context unavailable for {key}: {status}")
            if status == "no_usable_context":
                neighbors = (
                    graph.get_callees(key, config.get("context_depth", 1))
                    if context_mode == "callee"
                    else graph.get_callers(key, config.get("context_depth", 1))
                )
                if context_mode == "callgraph":
                    neighbors += graph.get_callees(key, config.get("context_depth", 1))
                if neighbors:
                    raise ValueError(f"No usable snippets for {key}; inspect budget/source")
                pack["metadata"]["status"] = "no_neighbors"
            packs[key] = pack
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": sorted(
            (d.metadata["Name"], d.version)
            for d in importlib.metadata.distributions()
            if d.metadata.get("Name")
        ),
    }
    fingerprint = digest(
        {
            "config": config,
            "repo": tree_digest(repo),
            "problems": problems,
            "tests": tests_by_id,
            "engine": engine_digest(),
            "environment": environment,
            "export_bundle": corpus_manifest if corpus is not None else export_bundle_digest(config),
            "context": {"packs": packs, "manifest": graph.manifest_sha256} if graph else None,
        }
    )
    comparison_fingerprint = digest(
        {
            "repo": tree_digest(repo),
            "problems": problems,
            "tests": tests_by_id,
            "engine": engine_digest(),
            "environment": environment,
            "export_bundle": corpus_manifest if corpus is not None else export_bundle_digest(config),
        }
    )
    manifest_path = output / "manifest.json"
    previous = read(manifest_path)
    if previous and not resume:
        raise ValueError("Run exists; choose another output or pass --resume")
    if previous and previous["fingerprint"] != fingerprint:
        raise ValueError("Resume fingerprint mismatch: config/source/tests/environment/engine changed")
    if not previous:
        write(
            manifest_path,
            {
                "schema_version": "dataset-eval-v2",
                "pipeline_revision": "2026-10-04-offline-dataset-preparation",
                "fingerprint": fingerprint,
                "comparison_fingerprint": comparison_fingerprint,
                "config": config,
                "environment": environment,
                "planned_functions": len(problems),
                "protocol": "repository-adapted SpecMind; confirmed assertion kills on prepared mutants",
                "graph_sha256": graph.digest if graph else None,
            },
        )
    identity = evaluation_identity(repo, config)
    for index, problem in enumerate(problems, 1):
        key = str(problem.get("problem_id") or problem["task_id"])
        directory = output / "functions" / digest(key)[:16]
        result_path = directory / "result.json"
        existing = read(result_path)
        if existing and existing.get("done"):
            print(f"[{index}/{len(problems)}] reused {key}", flush=True)
            continue
        print(f"[{index}/{len(problems)}] {key}", flush=True)
        started = perf_counter()
        result = {
            "function": key,
            "status": "unavailable",
            "done": False,
            "assertion": None,
            "correctness": {"status": "unavailable"},
            "context_mode": context_mode,
            "context_chars": packs.get(key, {}).get("metadata", {}).get("actual_chars", 0),
            "context_status": packs.get(key, {}).get("metadata", {}).get("status", "disabled"),
        }
        try:
            tests = tests_by_id.get(key, [])
            tests = list({pytest_nodeid(t): t for t in tests}.values())
            problem = dict(problem, _expected_source=function_code(problem))
            source_path = repo_path_for_problem(problem, repo)
            if not source_path:
                raise ValueError("Cannot resolve target source path")
            problem["_expected_source_path"] = str((repo / source_path).resolve())
            write(directory / "input.json", {"problem": problem, "tests": tests})
            if key in packs:
                write(directory / "context.json", packs[key])
            evaluator = Evaluator(problem, tests, repo, directory, dict(config, _evaluation_identity=identity))
            if corpus is not None:
                evaluator.eligible = [pytest_nodeid(t) for t in tests]
                evaluator.ready = bool(tests)
                preflight = {"status": "pass", "origin": "prepared-dataset",
                             "eligible_nodeids": evaluator.eligible, "reason": None}
                write(directory / "preflight.json", preflight)
            else:
                preflight = evaluator.preflight()
            result["preflight"] = preflight
            result["evaluation_identity"] = identity
            if preflight["status"] != "pass":
                raise ValueError(preflight["reason"])
            if corpus is not None:
                row = corpus[key]
                if not row["done"]:
                    raise ValueError("Prepared mutation targets incomplete")
                prepared = {"mutants": row["mutants"], "manifest": {"origin": "prepared-mutants",
                            "fingerprint": corpus_manifest["fingerprint"]}}
                write(directory / "mutants.json", prepared)
            else:
                prepared = prepare(problem, repo, directory, config)
            result["exported"] = len(prepared["mutants"])
            result["discovered"] = prepared["manifest"].get("total_discovered")
            result["export_skipped"] = len(prepared["manifest"].get("skipped_mutants", []))
            result["mutation_status"] = "available" if prepared["mutants"] else "no_mutants"
            if not prepared["mutants"] and not config.get("generate_without_mutants", False):
                result.update(
                    status="no_mutants", done=True, reason="Verified export manifest contains zero mutants"
                )
            else:
                state = generate(
                    function_code(problem),
                    problem["entry_point"],
                    evaluator,
                    prepared["mutants"],
                    config,
                    directory / "generation.json",
                    repository_context=packs.get(key, {}).get("repository_context", ""),
                )
                selected = state["best"]
                diagnostic = state["latest"]
                result.update(
                    status=("completed" if state["done"] else "evaluation_pending"
                            if state.get("generation_done") else "interrupted"),
                    done=state["done"],
                    generation_done=state.get("generation_done", False),
                    evaluation_complete=state.get("evaluation_complete", False),
                    attempts=len(state["attempts"]),
                    stop_reason=state.get("stop_reason"),
                    error=state.get("error"),
                    selected_submission=selected is not None and selected["action"] == "submit",
                    best_submission_turn=(state.get("best_submission") or {}).get("turn"),
                    latest_turn=(state.get("latest") or {}).get("turn"),
                )
                # Preserve failed submissions for correctness reporting; never call an exploration a final submission.
                if selected is None:
                    selected = next(
                        (
                            a
                            for a in reversed(state["attempts"])
                            if a["action"] == "submit" and a["assertion"]
                        ),
                        None,
                    )
                if selected:
                    result.update(
                        assertion=selected["assertion"],
                        correctness=selected["correctness"],
                        mutation=selected["mutation"],
                        selected_turn=selected["turn"],
                    )
                elif diagnostic:
                    result["reason"] = "No submitted candidate; inspect exploratory attempts"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["seconds"] = perf_counter() - started
        write(result_path, result)
        write(output / "progress.json", {"index": index, "planned": len(problems),
                                         "function": key, "status": result["status"]})
    return report(output)
