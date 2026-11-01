"""Render only observed evaluation evidence into feedback."""


def build_feedback(correctness, mutation):
    if correctness["status"] != "pass":
        return f"Correctness: {correctness['status']}.\n" + correctness.get(
            "log_tail", correctness.get("reason", "")
        )
    if mutation is None:
        return "Correctness passed on the checking tests; mutation score: N/A (no mutants). This does not prove correctness across the API."
    feedback = mutation["metrics"]["feedback"]
    if not feedback["total"]:
        return "Correctness passed on the checking tests; mutation score: N/A (no mutants)."
    text = (
        f"Correctness passed on the checking tests; this does not prove correctness across the API.\n"
        f"Assertion kills: {feedback['killed']}/{feedback['total']} prepared mutants.\n"
        f"Mutation score: {feedback['killed'] / feedback['total']:.2%}.\n"
        f"Unknown assertion outcomes: {feedback['unknown']}. Unknowns are not survivors.\n"
        "Refine the assertion to detect more incorrect behavior while preserving correctness."
    )
    survivors = [r for r in mutation["mutants"] if r["assertion_killed"] is False]
    for row in survivors[:5]:
        text += f"\nSurviving mutant {row['id']}:\n{row.get('diff', '')[:3000]}"
    return text
