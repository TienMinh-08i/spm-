import pytest

from paper_eval_v2.metrics import mutation_metrics
from paper_eval_v2.specmind import generate


def test_union_and_new_kills_with_unknowns():
    metrics = mutation_metrics(
        [
            {"id": "overlap", "suite_killed": True, "assertion_killed": True},
            {"id": "new", "suite_killed": False, "assertion_killed": True},
            {"id": "test-only", "suite_killed": True, "assertion_killed": False},
            {"id": "unknown", "suite_killed": None, "assertion_killed": True},
        ]
    )
    assert metrics["paired_evaluated"] == 3
    assert metrics["combined"]["killed"] == 3
    assert metrics["new_assertion"]["ids"] == ["new"]
    assert metrics["feedback"]["lower_bound"] == 3 / 4


def test_unknown_does_not_improve_feedback_or_become_survivor():
    metrics = mutation_metrics(
        [
            {"id": "1", "suite_killed": False, "assertion_killed": True},
            {"id": "2", "suite_killed": True, "assertion_killed": None},
        ]
    )
    assert metrics["feedback"]["lower_bound"] == 0.5
    assert metrics["feedback"]["upper_bound"] == 1
    assert metrics["feedback"]["unknown"] == 1
    assert metrics["unresolved"] == 1


def test_assertion_only_metrics_do_not_infer_suite_survival():
    metrics = mutation_metrics([
        {"id": "kill", "suite_killed": None, "assertion_killed": True},
        {"id": "survive", "suite_killed": None, "assertion_killed": False},
        {"id": "unknown", "suite_killed": None, "assertion_killed": None},
    ])
    assert metrics["assertion"]["score"] == 0.5
    assert metrics["assertion_evaluated"] == 2
    assert metrics["assertion_unresolved"] == 1
    assert metrics["feedback"]["lower_bound"] == 1 / 3
    assert metrics["paired_evaluated"] == 0
    for name in ("test_suite", "combined", "new_assertion"):
        assert metrics[name]["total"] == 0
        assert metrics[name]["score"] is None


class FakeEvaluator:
    def correctness(self, post):
        return {"status": "fail" if post == "assert False" else "pass"}

    def mutation(self, post, mutants):
        rows = [{"id": "1", "suite_killed": False, "assertion_killed": "== 1" in post}]
        return {"metrics": mutation_metrics(rows), "mutants": rows}


def config():
    return {"mode": "multiturn", "max_turns": 3, "threshold": 0.9}


@pytest.mark.parametrize("mode", ["multiturn", "retry"])
def test_context_none_preserves_baseline_prompt(tmp_path, mode):
    from pathlib import Path

    source = "def f(): return 1"
    template = Path(__file__).parents[1].joinpath("prompts", mode + ".txt").read_text(encoding="utf-8")
    for key, value in {
        "function_code": source,
        "codeStubAndDocstring": source,
        "canonical_solution": "",
        "entrypoint": "f",
        "max_turns": 1,
    }.items():
        template = template.replace("{" + key + "}", str(value))

    def call(messages, settings):
        assert messages[1]["content"] == template
        return "<solution>assert return_value == 1</solution>", {"total_tokens": 10}

    generate(
        source,
        "f",
        FakeEvaluator(),
        [{}],
        {**config(), "mode": mode, "max_turns": 1, "context_mode": "none"},
        tmp_path / "state.json",
        call,
    )


def test_only_submissions_selected_and_best_survives_later_failure(tmp_path):
    answers = iter(
        [
            "<solution>assert return_value >= 0</solution>",
            "<assert>assert return_value == 1</assert>",
            "<solution>assert False</solution>",
        ]
    )

    def call(messages, config):
        assert "def f()" in messages[1]["content"]
        assert "{codeStubAndDocstring}" not in messages[1]["content"]
        return next(answers), {}

    result = generate(
        "def f():\n    return 1", "f", FakeEvaluator(), [{}], config(), tmp_path / "state.json", call
    )
    assert result["best"]["turn"] == 1
    assert result["latest"]["correctness"]["status"] == "fail"
    assert result["done"]


def test_resume_does_not_repeat_paid_response_after_evaluator_interrupt(tmp_path):
    calls = []

    def call(messages, config):
        calls.append(1)
        return "<solution>assert return_value == 1</solution>", {}

    class Interrupted(FakeEvaluator):
        def correctness(self, post):
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError):
        generate("def f(): return 1", "f", Interrupted(), [{}], config(), tmp_path / "state.json", call)
    result = generate(
        "def f(): return 1", "f", FakeEvaluator(), [{}], config(), tmp_path / "state.json", call
    )
    assert len(calls) == 1
    assert result["stop_reason"] == "threshold_reached"


def test_unavailable_keeps_previous_candidate(tmp_path):
    class Unavailable(FakeEvaluator):
        def correctness(self, post):
            return {"status": "unavailable" if "missing" in post else "pass"}

    answers = iter(["<solution>assert return_value >= 0</solution>", "<solution>assert missing</solution>"])
    cfg = {**config(), "max_turns": 2}
    result = generate(
        "def f(): return 1",
        "f",
        Unavailable(),
        [{}],
        cfg,
        tmp_path / "state.json",
        lambda *_: (next(answers), {}),
    )
    assert result["best"]["correctness"]["status"] == "pass"
