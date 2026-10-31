import json

import pytest

from specmind_eval.evaluate.evaluator import Evaluator
from specmind_eval.common.storage import read, write
from specmind_eval.offline.suite import evaluate_suite


def test_offline_suite_has_separate_artifacts_and_reuses_cache(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "def f(x):\n    return x + 1\n"
    (repo / "target.py").write_text(source)
    (repo / "test_target.py").write_text(
        "from target import f\ndef test_f():\n    assert f(0) == 1\n"
    )
    run = tmp_path / "assertion-run"
    output = tmp_path / "suite-run"
    write(run / "manifest.json", {"fingerprint": "original-run", "config": {
        "repo_root": str(repo), "pytest_timeout": 30, "batch_size": 25,
    }})
    directory = run / "functions" / "f"
    write(directory / "input.json", {
        "problem": {"problem_id": "target.f", "module": "target", "entry_point": "f",
                    "_expected_source": source, "_expected_source_path": str(repo / "target.py")},
        "tests": [{"test_path": "test_target.py", "test_name": "test_f"}],
    })
    write(directory / "mutants.json", {"mutants": [
        {"id": "killed", "function_source": source.replace("x + 1", "x + 2")},
        {"id": "survived", "function_source": source.replace("x + 1", "1")},
    ]})
    before = {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    monkeypatch.setattr(Evaluator, "mutation", lambda *a: pytest.fail("Candidate evaluation invoked"))
    summary = evaluate_suite(run, output)
    assert summary == {"functions": 1, "exported": 2, "evaluated": 2,
                       "killed": 1, "unknown": 0, "unfinished": 0}
    rows = read(output / "functions/f/result.json")["mutants"]
    assert [r["suite_killed"] for r in rows] == [True, False]
    assert all((output / "functions/f/arms" / (r["neutral_arm"] + ".json")).is_file() for r in rows)
    monkeypatch.setattr("specmind_eval.evaluate.evaluator.run_pytest_postcondition",
                        lambda **kw: pytest.fail("Cached suite was rerun"))
    assert evaluate_suite(run, output) == summary
    assert {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    (repo / "target.py").write_text(source + "\n# changed checkout\n")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        evaluate_suite(run, output)
    with pytest.raises(ValueError, match="separate"):
        evaluate_suite(run, run)
