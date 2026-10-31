import json
from pathlib import Path

import pytest

from paper_eval_v2.__main__ import DEFAULTS
from paper_eval_v2.mutants import prepare
from paper_eval_v2.runner import run
from paper_eval_v2.common.storage import read, write


def attach_graph(config, root):
    import hashlib

    graph = root / "graph"
    graph.mkdir()
    nodes = [
        {"id": "target.f", "module": "target", "source": "def f(): return 1"},
        {"id": "helper.g", "module": "helper", "source": "def g():\n    \"\"\"Return one.\"\"\"\n    return 1"},
    ]
    write(graph / "nodes.json", nodes)
    write(graph / "edges.json", [{"caller": "target.f", "callee": "helper.g", "confidence": "high"}])
    write(
        graph / "manifest.json",
        {
            "git_commit": "fixture",
            "source_files": {
                "target.py": hashlib.sha256(Path(config["repo_root"], "target.py").read_bytes()).hexdigest()
            },
            "artifact_sha256": {
                n: hashlib.sha256((graph / n).read_bytes()).hexdigest() for n in ["nodes.json", "edges.json"]
            },
        },
    )
    config["call_graph_dir"] = str(graph)
    return graph


def test_context_is_saved_and_graph_change_blocks_resume(setup, monkeypatch):
    import hashlib

    config, output, _ = setup
    graph = attach_graph(config, output.parent)
    monkeypatch.setattr("paper_eval_v2.context.verify_commit", lambda *a: None)
    config["context_mode"] = "callee"
    run(config, output)
    directory = next((output / "functions").iterdir())
    pack = read(directory / "context.json")
    assert "helper.g" in pack["repository_context"]
    assert pack["repository_context"] in read(directory / "generation.json")["history"][1]["content"]
    nodes = read(graph / "nodes.json")
    nodes[1]["source"] = "def g(): return 2"
    write(graph / "nodes.json", nodes)
    manifest = read(graph / "manifest.json")
    manifest["artifact_sha256"]["nodes.json"] = hashlib.sha256(
        (graph / "nodes.json").read_bytes()
    ).hexdigest()
    write(graph / "manifest.json", manifest)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        run(config, output, resume=True)


def test_no_mutants_can_still_generate_correctness(setup):
    config, output, _ = setup
    manifest_path = output.parent / "exports/manifest.json"
    manifest = read(manifest_path)
    manifest.update(mutants=[], total_exported=0)
    write(manifest_path, manifest)
    config.update(generate_without_mutants=True, negative_control=True)
    run(config, output)
    result = read(output / "results.json")[0]
    assert result["correctness"]["status"] == "pass"
    assert result["mutation"] is None
    assert result["mutation_status"] == "no_mutants"
    assert result["preflight"]["negative_control"]["assertion_violations"] > 0


def test_experiment_and_common_corpus_comparison(setup, monkeypatch):
    from paper_eval_v2.experiment import experiment, compare

    config, output, _ = setup
    attach_graph(config, output.parent)
    monkeypatch.setattr("paper_eval_v2.context.verify_commit", lambda *a: None)
    result = experiment(config, output)
    assert result == {"rows": 4, "functions": 1, "unfinished": 0}
    rows = read(output / "comparison.json")
    assert all(r["common_assertion_evaluated"] == 1 and r["common_assertion_score"] == 1 for r in rows)
    assert all(r["common_paired"] == 0 and r["common_new_assertion_score"] is None for r in rows)
    assert rows[0]["context_status"] == "disabled"
    assert rows[2]["context_status"] == "no_neighbors"
    assert experiment(config, output, resume=True) == result
    path = next((output / "D/functions").glob("*/mutants.json"))
    bundle = read(path)
    bundle["mutants"][0]["function_source"] = "def f(): return 3"
    write(path, bundle)
    with pytest.raises(ValueError, match="Different mutant corpus"):
        compare(output)


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "def f():\n    return 1\n"
    (repo / "target.py").write_text(source)
    (repo / "test_target.py").write_text("from target import f\ndef test_f():\n    assert f() == 1\n")
    problem = {
        "problem_id": "target.f",
        "module": "target",
        "entry_point": "f",
        "repo_path": "target.py",
        "source": source,
    }
    problems, tests = tmp_path / "problems.jsonl", tmp_path / "tests.jsonl"
    problems.write_text(json.dumps(problem) + "\n")
    tests.write_text(
        json.dumps({"problem_id": "target.f", "test_path": "test_target.py", "test_name": "test_f"}) + "\n"
    )
    exported = tmp_path / "exports"
    write(exported / "m1.json", {"id": "1", "function_source": "def f():\n    return 2\n"})
    manifest = {
        "original_function_source": source,
        "total_exported": 1,
        "mutants": [{"id": "1", "file": "m1.json"}],
    }
    write(exported / "manifest.json", manifest)
    write(
        tmp_path / "rows.json", [{"problem_id": "target.f", "manifest_path": str(exported / "manifest.json")}]
    )
    config = {
        **DEFAULTS,
        "problems": str(problems),
        "tests": str(tests),
        "repo_root": str(repo),
        "reuse_mutmut_rows": str(tmp_path / "rows.json"),
        "mock_assertion": "assert return_value == 1",
        "max_turns": 1,
    }
    return config, tmp_path / "run", problem


def test_end_to_end_and_resume(setup, monkeypatch):
    config, output, _ = setup
    summary = run(config, output)
    assert summary["correctness_pass"] == 1
    assert summary["assertion"]["killed"] == 1
    assert summary["new_assertion"]["killed"] == 0

    def forbidden(*args):
        raise AssertionError("Completed function was reevaluated")

    monkeypatch.setattr("paper_eval_v2.runner.Evaluator", forbidden)
    assert run(config, output, resume=True) == summary
    with pytest.raises(ValueError, match="Run exists"):
        run(config, output)
    Path(config["repo_root"], "target.py").write_text("def f(): return 2\n")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        run(config, output, resume=True)


def test_debug_replay_and_join_are_offline_and_preserve_run(setup, monkeypatch):
    from paper_eval_v2.debug import replay
    from paper_eval_v2.join import join_suite
    from paper_eval_v2.suite_evaluation import evaluate_suite
    config, output, _ = setup
    run(config, output)
    before = {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    def no_execution(**kwargs):
        pytest.fail('Debug cache hit should not run pytest')
    with monkeypatch.context() as patch:
        patch.setattr('paper_eval_v2.evaluation.run_pytest_postcondition', no_execution)
        result = replay(output, 'target.f', output.parent/'debug', attempt=1, mutant='1')
        assert result['correctness'] == 'pass'
        assert result['mutation']['assertion']['killed'] == 1
    suite = output.parent/'suite'
    evaluate_suite(output, suite)
    paired = join_suite(output, suite, output.parent/'paired')
    assert paired['combined']['killed'] == 1
    assert paired['test_suite']['killed'] == 1
    assert paired['new_assertion']['killed'] == 0
    assert {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()} == before
    results = read(suite/'results.json')
    results[0]['mutants'][0]['source_sha256'] = 'wrong'
    write(suite/'results.json', results)
    with pytest.raises(ValueError, match='hash mismatch'):
        join_suite(output, suite, output.parent/'bad-join')


def test_reused_mutant_file_changes_block_resume(setup):
    config, output, _ = setup
    run(config, output)
    write(output.parent / "exports/m1.json", {"id": "1", "function_source": "def f(): return 3\n"})
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        run(config, output, resume=True)


def test_missing_mutant_file_never_silently_shrinks_denominator(setup):
    config, output, problem = setup
    manifest_path = output.parent / "exports/manifest.json"
    manifest = read(manifest_path)
    manifest["mutants"][0]["file"] = "missing.json"
    write(manifest_path, manifest)
    with pytest.raises(ValueError, match="Missing exported"):
        prepare(problem, Path(config["repo_root"]), output, config)


def test_no_mutant_skips_llm(setup):
    config, output, _ = setup
    manifest_path = output.parent / "exports/manifest.json"
    manifest = read(manifest_path)
    manifest.update(total_exported=0, mutants=[])
    write(manifest_path, manifest)
    summary = run(config, output)
    assert summary["no_mutant_skipped"] == 1
    assert summary["generated"] == 0


def test_prepared_main_never_filters_or_exports(setup, monkeypatch):
    from paper_eval_v2.dataset import load
    from paper_eval_v2.evaluation import Evaluator
    config, output, problem = setup
    problems, tests = load(Path(config['problems']), Path(config['tests']))
    mutant={'id':'prepared-1','function_source':'def f(): return 2\n'}
    config.update(prepared_dataset='prepared',prepared_mutants='corpus')
    monkeypatch.setattr('paper_eval_v2.runner.load_prepared_dataset', lambda *a: (problems,tests,{'fingerprint':'dataset'}))
    monkeypatch.setattr('paper_eval_v2.runner.load_prepared_mutants', lambda *a: ({'target.f':{'done':True,'mutants':[mutant]}},{'fingerprint':'corpus'}))
    monkeypatch.setattr(Evaluator,'preflight',lambda *a: pytest.fail('Main reran dataset filtering'))
    monkeypatch.setattr('paper_eval_v2.runner.prepare',lambda *a: pytest.fail('Main exported mutants'))
    summary=run(config,output)
    assert summary['generated']==1
    result=read(output/'results.json')[0]
    assert result['preflight']['origin']=='prepared-dataset'
    assert result['mutation']['metrics']['assertion']['killed']==1
