import pytest

from paper_eval_v2.evaluation import Evaluator
from paper_eval_v2.suite_evaluation import suite_outcome


@pytest.fixture
def evaluator(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg").mkdir()
    (repo / "pkg/__init__.py").write_text("from .mod import f as alias\n")
    source = "def f(x=0):\n    return x + 1\n"
    (repo / "pkg/mod.py").write_text(source)
    (repo / "test_f.py").write_text(
        "from pkg import alias\n"
        "def test_original():\n    assert alias(0) == 1\n"
        "def test_usage():\n    alias(1)\n"
    )
    problem = {
        "module": "pkg.mod",
        "entry_point": "f",
        "_expected_source": source,
        "_expected_source_path": str(repo / "pkg/mod.py"),
    }
    tests = [{"test_path": "test_f.py", "test_name": n} for n in ["test_original", "test_usage"]]
    ev = Evaluator(problem, tests, repo, tmp_path / "output", {
        "pytest_timeout": 30, "batch_size": 25})
    assert ev.preflight()["status"] == "pass"
    return ev


def test_real_attribution_alias_and_cross_test_overlap(evaluator):
    mutants = [{"id": "1", "function_source": "def f(x=0):\n    return x + 2\n"}]
    result = evaluator.mutation("assert return_value == x + 1", mutants)
    assert result["metrics"]["assertion"]["killed"] == 1
    assert result["metrics"]["test_suite"]["score"] is None
    assert suite_outcome(evaluator, mutants[0])["suite_killed"] is True
    # test_usage passes neutral but another test already kills this mutant.
    assert result["metrics"]["new_assertion"]["killed"] == 0
    trivial = evaluator.mutation("assert True", mutants)
    assert trivial["metrics"]["assertion"]["killed"] == 0
    assert trivial["metrics"]["test_suite"]["score"] is None


def test_runtime_error_is_not_assertion_kill(evaluator):
    mutants = [{"id": "1", "function_source": "def f(x=0):\n    return x + 2\n"}]
    result = evaluator.mutation("if return_value != x + 1:\n    assert missing_name", mutants)
    assert result["mutants"][0]["postcondition_runtime_error"]
    assert result["mutants"][0]["assertion_killed"] is None


def test_runtime_source_mismatch_blocks_hook(evaluator):
    evaluator.problem["_expected_source"] = "def f(x=0):\n    return 999\n"
    evaluator.output = evaluator.output.parent / "mismatch"
    assert evaluator.preflight()["status"] == "unavailable"


def test_runtime_path_mismatch_blocks_hook(evaluator):
    evaluator.problem["_expected_source_path"] = str(evaluator.repo_root / "wrong.py")
    evaluator.output = evaluator.output.parent / "wrongpath"
    assert evaluator.preflight()["status"] == "unavailable"


def test_uncovered_mutant_is_unknown(evaluator):
    result = evaluator.mutation(
        "assert True", [{"id": "raise", "function_source": 'def f(x=0):\n    raise ValueError("mutant")\n'}]
    )
    assert result["mutants"][0]["suite_killed"] is None
    assert suite_outcome(evaluator, {"id": "raise", "function_source": 'def f(x=0):\n    raise ValueError("mutant")\n'})["suite_killed"] is True
    assert result["mutants"][0]["assertion_killed"] is None
    assert result["metrics"]["feedback"]["unknown"] == 1


def test_original_assert_false_is_correctness_fail(evaluator):
    result = evaluator.correctness("assert False")
    assert result["status"] == "fail"
    assert result["assertion_violations"] == 2


def test_two_probe_filter_excludes_uncalled_exception_swallowed_and_failing_tests(tmp_path):
    from paper_eval_v2.dataset_filter import filter_tests
    source = 'def f(x=0):\n    if x < 0:\n        raise ValueError("negative")\n    return x + 1\n'
    (tmp_path / 'target.py').write_text(source)
    (tmp_path / 'test_filter.py').write_text(
        'import pytest\nfrom target import f\n'
        'def test_good():\n    assert f() == 1\n'
        'def test_uncalled():\n    assert True\n'
        'def test_exception():\n    with pytest.raises(ValueError):\n        f(-1)\n'
        'def test_swallowed():\n    try:\n        f()\n    except AssertionError:\n        pass\n'
        'def test_bad():\n    assert f() == 999\n'
    )
    tests = [{'test_path': 'test_filter.py', 'test_name': 'test_' + n}
             for n in ['good', 'uncalled', 'exception', 'swallowed', 'bad']]
    ev = Evaluator({'module': 'target', 'entry_point': 'f', '_expected_source': source},
                   tests, tmp_path, tmp_path/'out', {'pytest_timeout': 30, 'batch_size': 25})
    result = filter_tests(ev)
    assert result['status'] == 'ready'
    assert result['true_pass_cases'] == 4
    assert result['eligible_nodeids'] == ['test_filter.py::test_good']
    assert ev.tests == tests[:1]
    assert ev.correctness('assert return_value == 1')['checks'] == 1
    assert result['requested'] == 5


def test_two_probe_filter_with_no_observable_failure_is_unavailable(tmp_path):
    from paper_eval_v2.dataset_filter import filter_tests
    source = 'def f():\n    return 1\n'
    (tmp_path/'target.py').write_text(source)
    (tmp_path/'test_filter.py').write_text(
        'from target import f\ndef test_swallowed():\n'
        '    try:\n        f()\n    except AssertionError:\n        pass\n')
    ev = Evaluator({'module': 'target', 'entry_point': 'f', '_expected_source': source},
                   [{'test_path': 'test_filter.py', 'test_name': 'test_swallowed'}],
                   tmp_path, tmp_path/'out', {'pytest_timeout': 30, 'batch_size': 25})
    result = filter_tests(ev)
    assert result['status'] == 'excluded'
    assert result['eligible_nodeids'] == []


def test_default_mutation_runs_candidate_only(evaluator, monkeypatch):
    # Even a stale caller setting cannot re-enable neutral in generation.
    evaluator.config["evaluate_test_suite"] = True
    original_arm = evaluator.arm
    calls = []

    def track(assertion, mutant=None):
        calls.append((assertion, mutant))
        return original_arm(assertion, mutant)

    monkeypatch.setattr(evaluator, "arm", track)
    mutant = {"id": "1", "function_source": "def f(x=0):\n    return x + 2\n"}
    result = evaluator.mutation("assert return_value == x + 1", [mutant])
    assert calls == [("assert return_value == x + 1", mutant["function_source"])]
    row = result["mutants"][0]
    assert row["assertion_killed"] is True
    assert row["suite_killed"] is None
    assert row["neutral_status"] == "not_run"
    assert row["neutral_arm"] is None
    assert (evaluator.output / "arms" / (row["candidate_arm"] + ".json")).is_file()
    assert result["metrics"]["assertion"]["score"] == 1
    assert result["metrics"]["feedback"]["lower_bound"] == 1
    assert result["metrics"]["new_assertion"]["score"] is None


def test_invalid_nodeids_do_not_discard_observed_assertion_events(evaluator, monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    def fake_run(command, **kwargs):
        path = Path(kwargs["env"]["SPECMIND_DATASET_EVENTS"])
        with path.open("a", encoding="utf-8") as stream:
            for kind in ("post_check", "assertion_violation"):
                stream.write(json.dumps({"nodeid": "test_f.py::test_original", "kind": kind}) + "\n")
        return SimpleNamespace(returncode=4, stdout="ERROR: found no collectors for test_f.py::test_usage")

    monkeypatch.setattr("paper_eval_v2.execution.subprocess.run", fake_run)
    result = evaluator.correctness("assert False")
    assert result["status"] == "fail"
    assert result["assertion_violations"] > 0


def test_recursive_mutant_checks_each_recursive_return(tmp_path):
    source = "def f(n):\n    return 0 if n == 0 else f(n-1) + 1\n"
    (tmp_path / "target.py").write_text(source)
    (tmp_path / "test_f.py").write_text("from target import f\ndef test_f():\n    assert f(2) == 2\n")
    ev = Evaluator(
        {"module": "target", "entry_point": "f", "_expected_source": source},
        [{"test_path": "test_f.py", "test_name": "test_f"}],
        tmp_path,
        tmp_path / "out",
        {"pytest_timeout": 30, "batch_size": 25},
    )
    assert ev.preflight()["assertion_executions"] == 3
    arm = ev.arm("assert return_value == n", "def f(n):\n    return 1 if n == 0 else f(n-1) + 1\n")
    assert arm["cases"]["test_f.py::test_f"]["violations"] == 1


def test_mutated_default_does_not_redefine_assertion_input(tmp_path):
    source = "def f(x=1):\n    return x\n"
    (tmp_path / "target.py").write_text(source)
    (tmp_path / "test_f.py").write_text("from target import f\ndef test_f():\n    f()\n")
    ev = Evaluator(
        {"module": "target", "entry_point": "f", "_expected_source": source},
        [{"test_path": "test_f.py", "test_name": "test_f"}],
        tmp_path,
        tmp_path / "out",
        {"pytest_timeout": 30, "batch_size": 25},
    )
    assert ev.preflight()["status"] == "pass"
    result = ev.mutation(
        "assert return_value == x", [{"id": "default", "function_source": source.replace("x=1", "x=2")}]
    )
    assert result["metrics"]["assertion"]["killed"] == 1


@pytest.mark.parametrize("kind", ["classmethod", "staticmethod"])
@pytest.mark.parametrize("legacy", [False, True])
def test_method_descriptor_preserved(tmp_path, kind, legacy):
    params = "cls, x" if kind == "classmethod" else "x"
    source = f"@{kind}\ndef f({params}):\n    return x + 1\n"
    import textwrap

    (tmp_path / "target.py").write_text("class C:\n" + textwrap.indent(source, "    "))
    (tmp_path / "test_f.py").write_text("from target import C\ndef test_f():\n    assert C.f(1) == 2\n")
    ev = Evaluator(
        {"module": "target", "parent_name": "C", "entry_point": "f",
         "_expected_source": source.split("\n", 1)[1] if legacy else source},
        [{"test_path": "test_f.py", "test_name": "test_f"}],
        tmp_path,
        tmp_path / "out",
        {"pytest_timeout": 30, "batch_size": 25},
    )
    assert ev.preflight()["status"] == "pass"
    assert ev.correctness("assert False")["assertion_violations"] == 1
    mutant = source.replace("x + 1", "x + 2")
    result = ev.mutation("assert return_value == x + 1", [{"id": "1", "function_source": mutant}])
    assert result["metrics"]["assertion"]["killed"] == 1


@pytest.mark.parametrize("expected, runtime, reason", [
    ("@staticmethod\ndef f(cls, x):\n    return x + 1\n", "classmethod", "Dataset descriptor mismatch"),
    ("def f(cls, x):\n    return x + 2\n", "classmethod", "AST mismatch"),
    ("def f(cls, x=2):\n    return x + 1\n", "classmethod", "AST mismatch"),
    ("def f(cls, x):\n    return x + 1\n", None, "Runtime descriptor mismatch"),
])
def test_descriptor_guard_rejects_real_mismatches(tmp_path, expected, runtime, reason):
    import importlib.util
    from paper_eval_v2.pytest_plugin import _verify_runtime

    path = tmp_path / "target.py"
    path.write_text("class C:\n    @classmethod\n    def f(cls, x):\n        return x + 1\n")
    spec = importlib.util.spec_from_file_location("descriptor_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match=reason):
        _verify_runtime({"_expected_source": expected}, module.C.f.__func__,
                        classmethod if runtime else None)


def test_legacy_descriptor_normalization_preserves_other_decorators(tmp_path):
    import importlib.util
    from paper_eval_v2.pytest_plugin import _verify_runtime

    path = tmp_path / "target.py"
    path.write_text(
        "def marker(fn):\n    return fn\n"
        "class C:\n    @classmethod\n    @marker\n"
        "    def f(cls, x):\n        return x\n"
    )
    spec = importlib.util.spec_from_file_location("decorated_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.C.f.__func__
    expected = "@marker\ndef f(cls, x):\n    return x\n"
    _verify_runtime({"_expected_source": expected}, original, classmethod)
    with pytest.raises(RuntimeError, match="AST mismatch"):
        _verify_runtime({"_expected_source": expected.split("\n", 1)[1]}, original, classmethod)
