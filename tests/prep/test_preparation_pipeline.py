import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from paper_eval_v2.preparation.extract import extract
from paper_eval_v2.prepare_dataset import prepare_dataset
from paper_eval_v2.inputs.prepared import load_prepared_dataset
from paper_eval_v2.mutation_scope import prepare_scope, body_line_count
from paper_eval_v2.common.storage import read
from paper_eval_v2.preparation.cli import main


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp("isolated")
    repo = root / "checkout"
    package = repo / "src/marshmallow"
    package.mkdir(parents=True)
    (repo / "tests").mkdir()
    (package / "__init__.py").write_text(
        '''def inc(x):
    """Increment nonnegative input."""
    if x < 0:
        raise ValueError('negative')
    return x+1
def wrapper(x):
    """Delegate to inc."""
    return inc(x)
def unused():
    """Not covered by any test."""
    return 0
class C:
    @property
    def prop(self):
        """A property."""
        return 2
    def __g(self):
        """A private method."""
        return 1
    def total(self):
        """Call a property and private method."""
        return self.prop+self.__g()
''',
        encoding="utf8",
    )
    (repo / "tests/test_demo.py").write_text(
        """import pytest
from marshmallow import inc,wrapper,C
@pytest.mark.parametrize('unused',['ff::aa:1::2','x'])
def test_good(unused):
    assert inc(0)==1
def test_raises():
    with pytest.raises(ValueError):
        inc(-1)
def test_swallowed():
    try:
        inc(0)
    except AssertionError:
        pass
def test_bad():
    assert inc(0)==99
def test_wrapper():
    assert wrapper(0)==1
def test_class():
    assert C().total()==3
@pytest.fixture
def setup_only():
    inc(0)
def test_setup_only(setup_only):
    assert True
""",
        encoding="utf8",
    )
    raw = root / "raw"
    dataset = root / "prepared"
    summary = extract(repo, raw, timeout=60)
    ds = prepare_dataset(
        raw / "problems.jsonl", raw / "tests.jsonl", repo, dataset, timeout=30, batch_size=100
    )
    return root, repo, raw, dataset, summary, ds


def test_exact_nodeids_and_initial_failures(prepared):
    root, repo, raw, dataset, summary, ds = prepared
    rows = [json.loads(x) for x in (raw / "tests.jsonl").read_text(encoding="utf8").splitlines()]
    assert any("[ff::aa:1::2]" in r["nodeid"] for r in rows)
    assert any("test_bad" in r["nodeid"] for r in rows)  # raw keeps failure evidence
    assert not any("test_setup_only" in r["nodeid"] for r in rows)
    assert summary["trace_exitcode"] == 1
    assert summary["candidate_functions"] == 6
    assert summary["unlinked_functions"] == ["marshmallow.unused"]


def test_two_probes_private_property_and_readonly_loader(prepared):
    root, repo, raw, dataset, summary, ds = prepared
    assert ds["ready_functions"] == 5 and ds["excluded_functions"] == 1
    problems, tests, manifest = load_prepared_dataset(dataset, repo)
    assert {"marshmallow.C.prop", "marshmallow.C.__g"} <= {p["problem_id"] for p in problems}
    chosen = {r["nodeid"] for r in tests["marshmallow.inc"]}
    assert any("[ff::aa:1::2]" in n for n in chosen)
    assert not any("test_raises" in n or "test_swallowed" in n or "test_bad" in n for n in chosen)
    record = next(r for r in read(dataset / "results.json") if r["function"] == "marshmallow.inc")
    reasons = {r["nodeid"].split("::")[-1]: r["reason"] for r in record["decisions"]}
    assert reasons["test_swallowed"] == "false_did_not_fail_test"
    assert reasons["test_bad"] == "true_not_passed"


def test_scope_is_one_level_and_resume(prepared, monkeypatch):
    root, repo, raw, dataset, summary, ds = prepared
    scope = root / "scope"
    prepare_scope(dataset, repo, scope, timeout=30)
    rows = read(scope / "scope.json")
    wrapper = next(r for r in rows if r["assertion_target"] == "marshmallow.wrapper")
    assert {r["target"] for r in wrapper["mutation_targets"]} == {"marshmallow.wrapper", "marshmallow.inc"}
    total = next(r for r in rows if r["assertion_target"] == "marshmallow.C.total")
    assert {"marshmallow.C.prop", "marshmallow.C.__g"} <= {r["target"] for r in total["mutation_targets"]}
    original_run = subprocess.run

    def forbidden(*a, **k):
        if a and a[0][0] == "git":
            return original_run(*a, **k)  # read-only commit provenance
        raise AssertionError("Resume must not execute pytest")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert extract(repo, raw, resume=True, timeout=60) == summary
    assert (
        prepare_dataset(
            raw / "problems.jsonl",
            raw / "tests.jsonl",
            repo,
            dataset,
            resume=True,
            timeout=30,
            batch_size=100,
        )["selected_links"]
        == ds["selected_links"]
    )
    assert load_prepared_dataset(dataset, repo)[2]["complete"]


def test_cli_pipeline_and_validation(prepared, tmp_path):
    repo = prepared[1]
    config = tmp_path / "config.json"
    out = tmp_path / "output"
    config.write_text(
        json.dumps(
            {
                "repo": str(repo),
                "output": str(out),
                "tasks": ["marshmallow.wrapper"],
                "tests": ["tests/test_demo.py::test_wrapper"],
                "pytest_timeout": 30,
                "trace_timeout": 60,
            }
        ),
        encoding="utf8",
    )
    assert main(["all", "--config", str(config)]) == 0
    assert read(out / "pipeline_summary.json")["dataset"]["ready_functions"] == 1
    assert (
        main(["validate", "--repo", str(repo), "--raw", str(out / "raw"), "--dataset", str(out / "prepared")])
        == 0
    )
    assert main(["all", "--config", str(config), "--resume"]) == 0
    assert not (out / "mutants").exists()


def test_output_guards_and_fingerprints(prepared, tmp_path):
    root, repo, raw, dataset, summary, ds = prepared
    with pytest.raises(ValueError, match="separate"):
        extract(repo, repo / "out")
    with pytest.raises(ValueError, match="fingerprint"):
        extract(repo, raw, resume=True, timeout=61)
    with pytest.raises(ValueError, match="Unknown task"):
        extract(repo, tmp_path / "unknown", tasks=["marshmallow.nope"])
    assert (
        body_line_count('def f():\n    "doc"\n    # comment\n    a=1; b=2\n    c=3\n    return a+b+c\n') == 4
    )
    assert body_line_count("def f():\n    a=1\n    b=2\n    c=3\n    d=4\n    return a\n") == 5


def test_isolated_entrypoint_and_no_main_imports(tmp_path):
    source = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(source))
    command = "import sys; import paper_eval_v2.preparation.cli, paper_eval_v2.preparation.extract, paper_eval_v2.prepare_mutants; assert not any(k=='paper_eval' or k.startswith('paper_eval.') or k=='specmind_test' or k.startswith('specmind_test.') or k=='openai' for k in sys.modules); print('isolated')"
    proc = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys;sys.path.insert(0,{str(source)!r});" + command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "isolated" in proc.stdout


def test_explicit_exclusion_requires_reason(prepared, tmp_path):
    repo = prepared[1]
    with pytest.raises(ValueError, match="reason"):
        extract(repo, tmp_path / "out", exclude=[{"pattern": "test_bad"}])


def test_docker_mounts_external_repo_and_maps_callee(tmp_path, monkeypatch):
    import paper_eval_v2.prepare_mutants as module
    from paper_eval_v2.common.fingerprint import digest
    from paper_eval_v2.common.storage import write

    repo = tmp_path / "external checkout"
    repo.mkdir()
    scope = tmp_path / "scope"
    scope.mkdir()
    out = tmp_path / "out"
    target = {
        "problem_id": "marshmallow.g",
        "source": "def g(x):\n    return x+1\n",
        "repo_path": "src/marshmallow/__init__.py",
    }
    root = {
        "assertion_target": "marshmallow.f",
        "body_line_count": 1,
        "expansion": True,
        "mutation_targets": [{"target": "marshmallow.g", "kind": "callee", "coverage": "observed"}],
    }
    monkeypatch.setattr(
        module,
        "load_scope",
        lambda *a: (
            [root],
            [target],
            {"fingerprint": "scope", "files": {}, "dataset_fingerprint": "dataset", "dataset_files": {}},
        ),
    )
    monkeypatch.setattr(module, "source_environment", lambda *a: {"repo": "fixed"})
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: type("Proc", (), {"stdout": "sha256:fixed"})()
    )
    commands = []

    class Worker:
        stdout = ["export complete\n"]

        def __init__(self, command, **kw):
            commands.append(command)
            write(
                out / "targets" / digest("marshmallow.g")[:16] / "export.json",
                {
                    "status": "ready",
                    "target": target,
                    "discovered": 1,
                    "exported": 1,
                    "skipped": [],
                    "mutants": [
                        {"id": "1", "function_source": "def g(x):\n    return x+2\n", "diff": "diff"}
                    ],
                },
            )

        def wait(self):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", Worker)
    result = module.prepare_mutants(scope, repo, out, image="test-image")
    assert result["function_mutant_pairs"] == 1
    cmd = commands[0]
    assert "paper_eval_v2.mutant_worker" in cmd
    assert any(f"source={repo.as_posix()},target=/target_repo,readonly" in x for x in cmd)
    assert "/target_repo" in cmd
    assert read(out / "functions.json")[0]["mutants"][0]["kind"] == "callee"


def test_documented_filter_is_optional(prepared, tmp_path):
    from paper_eval_v2.preparation.documentation import collect_problems, collect_documented_symbols, _problem_is_documented
    import shutil

    repo = tmp_path / "checkout"
    shutil.copytree(prepared[1], repo)
    (repo / "docs").mkdir()
    (repo / "docs/api.rst").write_text(".. currentmodule:: marshmallow\n\n.. autofunction:: wrapper\n")
    rows = collect_problems(repo, repo / "src/marshmallow")
    symbols = collect_documented_symbols(repo, rows)
    assert {r.problem_id for r in rows if _problem_is_documented(r, symbols)} == {"marshmallow.wrapper"}
    result = extract(repo, tmp_path / "raw", documented_only=True, tests=["tests/test_demo.py::test_wrapper"])
    assert result["candidate_functions"] == 1


def test_invalid_config_rejected_before_execution(tmp_path):
    config = tmp_path / "bad.json"
    config.write_text(json.dumps({"repo": "missing", "output": "out", "typo": True}))
    assert main(["all", "--config", str(config)]) == 1
    assert not (tmp_path / "out").exists()


def test_module_cli_propagates_failure(tmp_path):
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-m", "paper_eval_v2", "prepare", "inspect", "--output", str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "No summary" in proc.stderr


def test_raw_hash_tampering_rejected(prepared, tmp_path):
    import shutil
    from paper_eval_v2.preparation.cli import check_raw

    raw = tmp_path / "raw"
    shutil.copytree(prepared[2], raw)
    with (raw / "tests.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="artifact mismatch"):
        check_raw(raw)


@pytest.mark.parametrize("pipeline", [False, True])
def test_incomplete_mutants_return_nonzero(tmp_path, monkeypatch, pipeline):
    import paper_eval_v2.prepare_mutants as mutants
    import paper_eval_v2.prepare_dataset as dataset
    import paper_eval_v2.mutation_scope as scope
    import paper_eval_v2.preparation.extract as extraction

    monkeypatch.setattr(mutants, "prepare_mutants", lambda *a, **kw: {"pending_targets": 1})
    if pipeline:
        monkeypatch.setattr(extraction, "extract", lambda *a, **kw: {})
        monkeypatch.setattr(
            dataset, "prepare_dataset", lambda *a, **kw: {"pending_functions": 0, "ready_functions": 1}
        )
        monkeypatch.setattr(scope, "prepare_scope", lambda *a, **kw: {})
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"repo": str(tmp_path / "repo"), "output": str(tmp_path / "out")}))
        argv = ["all", "--config", str(cfg), "--through", "mutants"]
    else:
        argv = [
            "mutants",
            "--scope",
            str(tmp_path / "scope"),
            "--repo",
            str(tmp_path / "repo"),
            "--output",
            str(tmp_path / "out"),
        ]
    assert main(argv) == 2
