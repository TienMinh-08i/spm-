"""All reported mutation metrics use explicit sets of mutant IDs."""


def rate(killed, total):
    return killed / total if total else None


def mutation_metrics(rows):
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate mutant IDs")
    # Suite attribution needs paired evidence; assertion detection does not.
    paired = [r for r in rows if r["suite_killed"] is not None and r["assertion_killed"] is not None]
    universe = {r["id"] for r in paired}
    tests = {r["id"] for r in paired if r["suite_killed"]}
    assertions = {r["id"] for r in paired if r["assertion_killed"]}
    result = {
        "exported": len(rows),
        "paired_evaluated": len(universe),
        "unresolved": len(rows) - len(universe),
        "assertion_evaluated": sum(r["assertion_killed"] is not None for r in rows),
        "assertion_unresolved": sum(r["assertion_killed"] is None for r in rows),
    }
    for name, values in (
        ("test_suite", tests),
        ("combined", tests | assertions),
        ("new_assertion", assertions - tests),
    ):
        result[name] = {
            "killed": len(values),
            "total": len(universe),
            "score": rate(len(values), len(universe)),
            "ids": sorted(values),
        }
    # A fixed denominator prevents an attempt gaining feedback score by losing coverage.
    proven = sum(r["assertion_killed"] is True for r in rows)
    unknown = sum(r["assertion_killed"] is None for r in rows)
    result["feedback"] = {
        "metric": "assertion_kill_lower_bound_on_exported_mutants",
        "killed": proven,
        "total": len(rows),
        "unknown": unknown,
        "lower_bound": rate(proven, len(rows)),
        "upper_bound": rate(proven + unknown, len(rows)),
        "complete": unknown == 0,
    }
    assert result["combined"]["killed"] == result["test_suite"]["killed"] + result["new_assertion"]["killed"]
    assertion_rows = [r for r in rows if r["assertion_killed"] is not None]
    assertion_ids = sorted(r["id"] for r in assertion_rows if r["assertion_killed"])
    result["assertion"] = {
        "killed": len(assertion_ids), "total": len(assertion_rows),
        "score": rate(len(assertion_ids), len(assertion_rows)), "ids": assertion_ids,
    }
    return result
