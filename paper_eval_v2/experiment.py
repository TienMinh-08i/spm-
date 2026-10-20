"""Controlled context ablation; comparison never treats missing runs as zero scores."""

from pathlib import Path
import csv

from .metrics import mutation_metrics
from .report import token_usage
from .storage import digest, read, write, run_lock

ARMS = {"A": "none", "B": "callee", "C": "caller", "D": "callgraph"}


def experiment(config, output, resume=False):
    from .runner import run
    from .context import CallGraph, verify_commit

    output = Path(output)
    if not config.get("call_graph_dir"):
        raise ValueError("An A/B/C/D experiment requires call_graph_dir")
    repo = Path(config["repo_root"])
    verify_commit(repo, config.get("expected_repo_commit"))
    CallGraph.load(Path(config["call_graph_dir"]), repo)
    with run_lock(output):
        marker = output / "experiment.json"
        previous = read(marker)
        common = {k: v for k, v in config.items() if k != "context_mode"}
        if previous and (not resume or previous["config"] != common):
            raise ValueError("Experiment exists; resume requires identical common configuration")
        if not previous:
            write(marker, {"schema_version": "context-ablation-v1", "config": common, "arms": ARMS})
        for label, mode in ARMS.items():
            run({**config, "context_mode": mode}, output / label, resume)
        return compare(output)


def compare(output, paired=None):
    output = Path(output)
    paired = Path(paired) if paired is not None else None
    experiment_manifest = read(output / "experiment.json")
    if not experiment_manifest:
        raise ValueError("Missing experiment.json")
    manifests, records = {}, {}
    for label, mode in ARMS.items():
        manifest = read(output / label / "manifest.json")
        if manifest:
            common = {k: v for k, v in manifest["config"].items() if k != "context_mode"}
            if common != experiment_manifest["config"] or manifest["config"]["context_mode"] != mode:
                raise ValueError(f"Incompatible configuration in {label}")
            manifests[label] = manifest
        original_records = read(output / label / 'results.json', [])
        if paired is not None:
            joined = read(paired / label / 'manifest.json', {})
            if (not manifest or joined.get('source_run_fingerprint') != manifest['fingerprint']
                    or joined.get('source_results_sha256') != digest(original_records)):
                raise ValueError('Stale or incompatible paired report: ' + label)
            selected_records = read(paired / label / 'results.json', [])
            if {r['function'] for r in selected_records} != {r['function'] for r in original_records}:
                raise ValueError('Paired function set mismatch: ' + label)
        else:
            selected_records = original_records
        records[label] = {r['function']: r for r in selected_records}
    if manifests:
        env = next(iter(manifests.values()))["environment"]
        if any(m["environment"] != env for m in manifests.values()):
            raise ValueError("Cannot compare different dependency environments")
        fingerprints = {m.get("comparison_fingerprint") for m in manifests.values()}
        if None in fingerprints or len(fingerprints) != 1:
            raise ValueError("Cannot compare different source/tests/engine/corpus fingerprints")
        graph_hashes = {m.get("graph_sha256") for k, m in manifests.items() if k != "A"}
        if None in graph_hashes or len(graph_hashes) > 1:
            raise ValueError("Cannot compare different graph inputs")
    functions = set(experiment_manifest["config"].get("tasks") or [])
    for arm in records.values():
        functions.update(arm)
    if not functions:
        raise ValueError("No functions to compare")
    rows = []
    for function in sorted(functions):
        bundles, paired_ids, assertion_evaluated = {}, {}, {}
        for label in ARMS:
            directory = output / label / "functions" / digest(function)[:16]
            bundle = read(directory / "mutants.json")
            if bundle is not None:
                bundles[label] = {m["id"]: digest({'source': m['function_source'],
                                                 'target': m.get('mutation_problem')}) for m in bundle['mutants']}
                if len(bundles[label]) != len(bundle['mutants']):
                    raise ValueError('Duplicate mutant IDs: ' + function)
            result = records[label].get(function, {})
            mutant_rows = (result.get("mutation") or {}).get("mutants", [])
            paired_ids[label] = {
                m["id"]
                for m in mutant_rows
                if m["suite_killed"] is not None and m["assertion_killed"] is not None
            }
            assertion_evaluated[label] = {
                m["id"] for m in mutant_rows if m["assertion_killed"] is not None
            }
        if bundles and any(b != next(iter(bundles.values())) for b in bundles.values()):
            raise ValueError(f"Different mutant corpus for {function}")
        common_ids = set.intersection(*paired_ids.values())
        common_assertion_ids = set.intersection(*assertion_evaluated.values())
        for label, mode in ARMS.items():
            result = records[label].get(function, {})
            mutation = result.get("mutation") or {}
            metrics = mutation.get("metrics", {})
            shared = mutation_metrics([m for m in mutation.get("mutants", []) if m["id"] in common_ids])
            shared_assertion = mutation_metrics([
                m for m in mutation.get("mutants", []) if m["id"] in common_assertion_ids
            ])
            state = read(output / label / "functions" / digest(function)[:16] / "generation.json", {})
            row = {
                "function": function,
                "config": label,
                "context_mode": mode,
                "status": result.get("status", "not_run"),
                "done": result.get("done", False),
                "correctness": result.get("correctness", {}).get("status", "unavailable"),
                "attempts": result.get("attempts"),
                "final_assertion": result.get("assertion"),
                "context_chars": result.get("context_chars"),
                "context_status": result.get("context_status"),
                "exported": result.get("exported"),
                "paired": metrics.get("paired_evaluated"),
                "unknown": metrics.get("unresolved"),
                "mutation_status": result.get("mutation_status", "unavailable"),
                "common_paired": len(common_ids),
                "assertion_evaluated": metrics.get("assertion_evaluated"),
                "assertion_unknown": metrics.get("assertion_unresolved"),
                "common_assertion_evaluated": len(common_assertion_ids),
                "corpus_sha256": digest(bundles[label]) if label in bundles else None,
                **token_usage(state),
            }
            for name in ("assertion", "test_suite", "combined", "new_assertion"):
                shared_metric = shared_assertion[name] if name == "assertion" else shared[name]
                shared_total = len(common_assertion_ids) if name == "assertion" else len(common_ids)
                row[name + "_score"] = metrics.get(name, {}).get("score")
                row[name + "_kills"] = metrics.get(name, {}).get("killed")
                row["common_" + name + "_score"] = shared_metric["score"]
                row["common_" + name + "_kills"] = shared_metric["killed"] if shared_total else None
            rows.append(row)
    destination = paired or output
    write(destination / "comparison.json", rows)
    with (destination / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {"rows": len(rows), "functions": len(functions), "unfinished": sum(not r["done"] for r in rows)}
    write(destination / "comparison_summary.json", summary)
    return summary
