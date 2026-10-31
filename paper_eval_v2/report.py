"""Reports derived from completed checkpoints, without rerunning evaluation."""

import csv

from paper_eval_v2.common.storage import read, write


def token_usage(state):
    usages = [a.get("usage", {}) for a in state.get("attempts", [])]
    totals = [u.get("total_tokens") for u in usages]
    return {
        "tokens": sum(v for v in totals if v is not None)
        if totals and all(v is not None for v in totals)
        else None,
        "known_tokens": sum(v for v in totals if v is not None),
        "usage_complete": bool(totals) and all(v is not None for v in totals),
    }


def report(output):
    records = [read(p) for p in sorted((output / "functions").glob("*/result.json"))]
    generated = [r for r in records if r.get("assertion") is not None]
    evaluable = [r for r in generated if r.get("correctness", {}).get("status") in {"pass", "fail"}]
    correct = [r for r in evaluable if r["correctness"]["status"] == "pass"]
    summary = {
        "functions_with_records": len(records),
        "generated": len(generated),
        "correctness_evaluable": len(evaluable),
        "correctness_pass": len(correct),
        "correctness_rate": len(correct) / len(evaluable) if evaluable else None,
        "correctness_unavailable": len(generated) - len(evaluable),
        "no_mutant_skipped": sum(r["status"] == "no_mutants" for r in records),
        "unfinished": sum(not r.get("done", False) for r in records),
    }
    for kind in ("test_suite", "assertion", "combined", "new_assertion"):
        values = [r["mutation"]["metrics"][kind] for r in records if r.get("mutation")]
        total, killed = sum(v["total"] for v in values), sum(v["killed"] for v in values)
        macro = [v["score"] for v in values if v["score"] is not None]
        summary[kind] = {
            "killed": killed,
            "total": total,
            "micro_score": killed / total if total else None,
            "macro_score": sum(macro) / len(macro) if macro else None,
        }
    summary["exported_in_final_mutation_results"] = sum(
        r["mutation"]["metrics"]["exported"] for r in records if r.get("mutation")
    )
    summary["unresolved_in_final_mutation_results"] = sum(
        r["mutation"]["metrics"]["unresolved"] for r in records if r.get("mutation")
    )
    summary["assertion_unknown"] = sum(
        r["mutation"]["metrics"].get("assertion_unresolved", 0) for r in records if r.get("mutation"))
    feedback = [r["mutation"]["metrics"]["feedback"] for r in records if r.get("mutation")]
    exported = sum(f["total"] for f in feedback)
    proven = sum(f["killed"] for f in feedback)
    unknown = sum(f["unknown"] for f in feedback)
    summary["assertion_on_exported"] = {
        "killed": proven, "total": exported, "unknown": unknown,
        "lower_bound": proven / exported if exported else None,
        "upper_bound": (proven + unknown) / exported if exported else None,
    }
    write(output / "summary.json", summary)
    write(output / "results.json", records)
    attempts = []
    mutant_rows = []
    for directory in sorted((output / "functions").glob("*")):
        record = read(directory / "result.json", {})
        state = read(directory / "generation.json", {})
        for attempt in state.get("attempts", []):
            fb = (attempt.get("mutation") or {}).get("metrics", {}).get("feedback", {})
            attempts.append(
                {
                    "function": record.get("function"),
                    "turn": attempt["turn"],
                    "action": attempt["action"],
                    "assertion": attempt["assertion"],
                    "correctness": attempt["correctness"]["status"],
                    "lower_bound": fb.get("lower_bound"),
                    "upper_bound": fb.get("upper_bound"),
                    "exported": fb.get("total"),
                    "unknown": fb.get("unknown"),
                    "seconds": attempt["seconds"],
                    "feedback": attempt["feedback"],
                }
            )
        for mutant in (record.get("mutation") or {}).get("mutants", []):
            mutant_rows.append({"function": record.get("function"), **mutant})

    def table(name, rows, columns):
        with (output / name).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    table(
        "attempts.csv",
        attempts,
        [
            "function",
            "turn",
            "action",
            "assertion",
            "correctness",
            "lower_bound",
            "upper_bound",
            "exported",
            "unknown",
            "seconds",
            "feedback",
        ],
    )
    table(
        "mutants.csv",
        mutant_rows,
        [
            "function",
            "id",
            "suite_killed",
            "assertion_killed",
            "covered",
            "postcondition_runtime_error",
            "target_error",
            "neutral_status",
            "candidate_status",
            "diff",
            "neutral_arm",
            "candidate_arm",
            "mutation_target",
            "mutation_reached",
        ],
    )
    columns = [
        "function",
        "status",
        "correctness",
        "assertion",
        "attempts",
        "exported",
        "paired_evaluated",
        "unresolved",
        "assertion_evaluated",
        "assertion_unresolved",
        "test_suite",
        "assertion_kills",
        "combined",
        "new_assertion",
        "test_suite_score",
        "assertion_score",
        "combined_score",
        "new_assertion_score",
        "requested_tests",
        "collected_cases",
        "executed_cases",
        "covered_cases",
        "assertion_executions",
        "discovered",
        "export_skipped",
        "context_mode",
        "context_chars",
        "context_status",
        "mutation_status",
        "tokens",
        "known_tokens",
        "usage_complete",
    ]
    with (output / "functions.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            m = (record.get("mutation") or {}).get("metrics", {})
            row = {k: record.get(k) for k in ["function", "status", "assertion", "attempts"]}
            row["correctness"] = record.get("correctness", {}).get("status")
            from paper_eval_v2.common.fingerprint import digest

            state = read(output / "functions" / digest(record["function"])[:16] / "generation.json", {})
            row.update(token_usage(state))
            row.update(
                {
                    k: record.get(k)
                    for k in ("context_mode", "context_chars", "context_status", "mutation_status")
                }
            )
            row.update({k: m.get(k) for k in ["exported", "paired_evaluated", "unresolved",
                                           "assertion_evaluated", "assertion_unresolved"]})
            row["exported"] = m.get("exported", record.get("exported"))
            row["discovered"] = record.get("discovered")
            row["export_skipped"] = record.get("export_skipped")
            preflight = record.get("preflight", {})
            for column, field in [
                ("requested_tests", "requested"),
                ("collected_cases", "collected_cases"),
                ("executed_cases", "executed_cases"),
                ("covered_cases", "assertion_covered_cases"),
                ("assertion_executions", "assertion_executions"),
            ]:
                row[column] = preflight.get(field)
            for name in ("test_suite", "assertion", "combined", "new_assertion"):
                row[name + "_score"] = m.get(name, {}).get("score")
            row.update(
                {
                    out: m.get(key, {}).get("killed")
                    for out, key in [
                        ("test_suite", "test_suite"),
                        ("assertion_kills", "assertion"),
                        ("combined", "combined"),
                        ("new_assertion", "new_assertion"),
                    ]
                }
            )
            writer.writerow(row)
    lines = [
        "# Dataset evaluation v2",
        "",
        "Unknowns are not survivors. Assertion score uses assertion-known mutants; suite/combined/new use paired-known mutants. 0/0 means unavailable, not zero performance. Suite evaluation is separate; use join-suite for paired attribution.",
        "",
        "| Metric | Killed / evaluated | Micro score |",
        "|---|---:|---:|",
    ]
    for key in ("test_suite", "assertion", "combined", "new_assertion"):
        v = summary[key]
        score = f"{v['micro_score']:.2%}" if v["micro_score"] is not None else "unavailable"
        lines.append(f"| {key} | {v['killed']}/{v['total']} | {score} |")
    lines += [
        "",
        f"Correctness: {len(correct)}/{len(evaluable)} evaluated generated assertions; {len(generated) - len(evaluable)} unavailable.",
        f"Assertion evidence: {proven}/{exported} exported mutants killed; {unknown} assertion outcomes unknown.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
