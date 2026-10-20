"""Render only observed evaluation evidence into feedback."""


def build_feedback(correctness, mutation):
    if correctness["status"] != "pass":
        return f"Correctness: {correctness['status']}.\n" + correctness.get(
            "log_tail", correctness.get("reason", "")
        )
    if mutation is None:
        return "Correctness passed; completeness unavailable (no mutation result)."
    feedback = mutation["metrics"]["feedback"]
    if not feedback["total"]:
        return "Correctness passed; completeness unavailable (no exported mutants)."
    text = (
        f"Correctness passed on the original test suite.\n"
        f"Assertion kills: {feedback['killed']}/{feedback['total']} exported mutants.\n"
        f"Completeness lower bound: {feedback['lower_bound']:.2%}; upper bound: {feedback['upper_bound']:.2%}.\n"
        f"Unknown assertion outcomes: {feedback['unknown']}. Unknowns are not survivors.\n"
        "Refine the assertion to detect more incorrect behavior while preserving correctness."
    )
    survivors = [r for r in mutation["mutants"] if r["assertion_killed"] is False]
    for row in survivors[:5]:
        text += f"\nSurviving mutant {row['id']}:\n{row.get('diff', '')[:3000]}"
    return text
