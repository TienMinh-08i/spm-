"""Actual pytest subprocesses: causal slices and unchanged observed behavior."""
import json
from pathlib import Path
import pytest
from specmind_eval.common.storage import read, write
from specmind_eval.common.fingerprint import file_hash, digest
from specmind_prep.project import validate_project
from specmind_prep.slicing import trace_function, load_trace, query_slice


def fixture(tmp_path, source, test):
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/demo/__init__.py").write_text(source, encoding="utf-8")
    (repo / "tests/test_example.py").write_text(test, encoding="utf-8")
    project = validate_project({"name": "demo", "packages": ["demo"]}, repo)
    return repo, project, tmp_path / "trace"


def run(repo, project, out, **kwargs):
    return trace_function(repo, project, "tests/test_example.py::test_f", "demo.f", out, **kwargs)


def return_event(out, frame=1):
    return read(out / "calls.json")[frame - 1]["return_event"]


def lines(result):
    return {s["line"] for s in result["source_statements"]}


def test_actual_branches_alias_import_and_queries_do_not_rerun_test(tmp_path, monkeypatch):
    counter = tmp_path / "counter.txt"
    monkeypatch.setenv("SLICE_TEST_COUNTER", str(counter))
    repo, project, out = fixture(
        tmp_path,
        """def f(x, flag):
    unrelated = 99
    if flag:
        y = x + 1
    else:
        y = x - 1
    return y
""",
        """from demo import f as alias
import os
from pathlib import Path
def test_f():
    p=Path(os.environ['SLICE_TEST_COUNTER'])
    with p.open('a') as stream: stream.write('run\\n')
    assert alias(3, True)==4
    assert alias(3, False)==2
""",
    )
    before = (repo / "src/demo/__init__.py").read_bytes()
    assert run(repo, project, out)["status"] == "complete_supported"
    assert query_slice(out, repo, return_event(out), "y")["criterion"]["frame"] == 1
    assert lines(query_slice(out, repo, return_event(out), "y")) == {1, 3, 4}
    assert lines(query_slice(out, repo, return_event(out, 2), "y")) == {1, 3, 6}
    assert counter.read_text().splitlines() == ["run", "run"]
    assert (repo / "src/demo/__init__.py").read_bytes() == before
    assert len(read(out / "calls.json")) == 2


def test_paper_figure7_real_loop_uses_only_last_value_occurrence(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """def f():
    n = int(input())
    i = 1
    while i <= n:
        x = int(input())
        if x < 0:
            y = -x
        else:
            y = x + 100
        z = y * 2
        i = i + 1
    return z
""",
        """from demo import f
def test_f(monkeypatch):
    values=iter(['3','-4','3','-2'])
    monkeypatch.setattr('builtins.input',lambda:next(values))
    assert f()==4
""",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = query_slice(out, repo, return_event(out), "z")
    assert lines(result) == {2, 3, 4, 5, 6, 7, 10, 11}
    ys = [n for n in read(out / "ddg.json") if "y" in n["writes"]]
    selected = {n["id"] for n in result["nodes"]}
    assert ys[-1]["id"] in selected and ys[0]["id"] not in selected and ys[1]["id"] not in selected
    assert read(out / "calls.json")[0]["return"] == {"type": "int", "value": 4}


def test_paper_figure9_keep_earlier_definition_not_latest_static_edges(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """def f():
    n = int(input())
    a = 0
    i = 1
    while i <= n:
        x = int(input())
        if x < 0:
            y = -x
        else:
            y = -x
        z = y
        if z > 0:
            a = a + z
        i += 1
    return a
""",
        """from demo import f
def test_f(monkeypatch):
    values=iter(['2','-4','3'])
    monkeypatch.setattr('builtins.input',lambda:next(values))
    assert f()==4
""",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = query_slice(out, repo, return_event(out), "a")
    assert 8 in lines(result) and 10 not in lines(result)
    zs = [n for n in read(out / "ddg.json") if "z" in n["writes"]]
    assert zs[0]["id"] in {n["id"] for n in result["nodes"]}
    assert zs[-1]["id"] not in {n["id"] for n in result["nodes"]}


def test_short_circuit_only_records_evaluated_reads(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(flag, x):\n    y = flag and x\n    return y\n",
        "from demo import f\ndef test_f():\n    assert f(False,99) is False\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = query_slice(out, repo, return_event(out), "y")
    assert {n["writes"][0] for n in result["nodes"] if n["kind"] == "parameter"} == {"flag"}


def test_failing_test_after_criterion_can_still_have_a_valid_trace(tmp_path):
    repo, project, out = fixture(
        tmp_path, "def f(x):\n    return x+1\n", "from demo import f\ndef test_f():\n    assert f(1)==99\n"
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    assert read(out / "execution.json")["baseline_exitcode"] == 1
    assert read(out / "execution.json")["instrumented_exitcode"] == 1
    assert query_slice(out, repo, return_event(out), "x")["nodes"]


@pytest.mark.parametrize(
    "source",
    [
        "def f(x):\n    if x:\n        return 0\n    return x\n",
        "def f(x):\n    x[0]=1\n    return x\n",
        "def f(x):\n    return other(x)\n",
        "def f(x):\n    return GLOBAL+x\n",
        "def f(x):\n    for i in range(x):\n        pass\n    return x\n",
    ],
)
def test_unsupported_source_is_not_silently_approximated(tmp_path, source):
    repo, project, out = fixture(tmp_path, source, "def test_f():\n    pass\n")
    assert run(repo, project, out)["status"] == "unsupported"
    result = query_slice(out, repo, 1, "x")
    assert result["status"] == "unsupported" and result["nodes"] is None
    assert not (out / "baseline.log").exists()


def test_nonprimitive_parameter_is_explicitly_unsupported(tmp_path):
    repo, project, out = fixture(
        tmp_path, "def f(x):\n    return x\n", "from demo import f\ndef test_f():\n    assert f([])==[]\n"
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert read(out / "baseline.json")["exitcode"] == 0
    assert not (out / "instrumented.json").exists()


def test_target_exception_is_not_treated_as_a_normal_return(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return 1//x\n",
        "import pytest\nfrom demo import f\ndef test_f():\n    with pytest.raises(ZeroDivisionError): f(0)\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"


def test_instrumentation_changed_behavior_is_rejected(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return x\n",
        "from demo import f\ndef test_f():\n    assert '__specmind_slice_runtime__' not in f.__code__.co_names\n    assert f(1)==1\n",
    )
    assert run(repo, project, out)["status"] == "instrumentation_changed_behavior"


def test_event_limit_is_incomplete_not_an_empty_slice(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(n):\n    i=0\n    while i<n:\n        i+=1\n    return i\n",
        "from demo import f\ndef test_f():\n    assert f(10)==10\n",
    )
    assert run(repo, project, out, max_events=10)["status"] == "incomplete"
    assert query_slice(out, repo, 1, "n")["nodes"] is None


def test_timeout_is_explicit_and_artifact_is_finalized(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f():\n    x=0\n    while True:\n        x+=1\n    return x\n",
        "from demo import f\ndef test_f():\n    f()\n",
    )
    assert run(repo, project, out, timeout=0.3)["status"] == "incomplete"
    assert load_trace(out, repo)[2]["trace_status"] == "incomplete"


def test_no_target_call_and_exact_parametrized_test_selection(tmp_path):
    repo, project, out = fixture(tmp_path, "def f(x):\n    return x\n", "def test_f():\n    assert True\n")
    assert run(repo, project, out)["status"] == "criterion_not_reached"


def test_defaults_and_annotations_are_not_evaluated_twice(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """counter=iter([2,99])
def f(x: int=next(counter), *, flag: bool=True) -> int:
    return x+1 if flag else x-1
""",
        """from demo import f,counter
def test_f():
    assert f()==3
    assert f(4,flag=False)==3
    assert next(counter)==99
""",
    )
    assert run(repo, project, out)["status"] == "complete_supported"


def test_trace_and_source_integrity_and_output_guard(tmp_path):
    repo, project, out = fixture(
        tmp_path, "def f(x):\n    return x\n", "from demo import f\ndef test_f():\n    assert f(2)==2\n"
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    with pytest.raises(ValueError, match="Output exists"):
        run(repo, project, out)
    graph = read(out / "ddg.json")
    graph[-1]["depends_on"] = []
    write(out / "ddg.json", graph)
    with pytest.raises(ValueError, match="artifact mismatch"):
        load_trace(out, repo)


def test_multiple_collected_cases_do_not_execute(tmp_path, monkeypatch):
    counter = tmp_path / "counter"
    monkeypatch.setenv("SLICE_TEST_COUNTER", str(counter))
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return x\n",
        "import pytest,os\nfrom pathlib import Path\nfrom demo import f\n@pytest.mark.parametrize('x',[1,2])\ndef test_f(x):\n    Path(os.environ['SLICE_TEST_COUNTER']).write_text('ran')\n    assert f(x)==x\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert not counter.exists()




def test_normal_call_in_a_callers_except_block_is_not_a_target_exception(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return x+1\n",
        "from demo import f\ndef test_f():\n    try:\n        1/0\n    except ZeroDivisionError:\n        assert f(1)==2\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"


def test_fixture_rebinding_a_pure_builtin_is_not_silently_assumed_pure(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return abs(x)\n",
        "import demo\ndef test_f(monkeypatch):\n    monkeypatch.setattr(demo,'abs',lambda x: 7,raising=False)\n    assert demo.f(-1)==7\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert read(out / "instrumented.json")["exitcode"] == 0
    assert "rebound" in " ".join(read(out / "summary.json")["issues"])


def test_actual_print_output_matches_and_unicode_source_map(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        'def f(x):\n    label="xin chÃƒÆ’Ã‚Â o"\n    print(label, x)\n    return x+1\n',
        'from demo import f\ndef test_f(capsys):\n    assert f(2)==3\n    assert capsys.readouterr().out=="xin chÃƒÆ’Ã‚Â o 2\\n"\n',
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    assert query_slice(out, repo, return_event(out), "x")["status"] == "complete_supported"


def test_module_replacing_function_bytecode_is_not_attributed_to_original_source(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return x+1\nf.__code__=(lambda x:x*2).__code__.replace(co_firstlineno=f.__code__.co_firstlineno)\n",
        "from demo import f\ndef test_f():\n    assert f(2)==4\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert "bytecode" in " ".join(read(out / "summary.json")["issues"])


def test_source_change_after_trace_is_rejected(tmp_path):
    repo, project, out = fixture(
        tmp_path, "def f(x):\n    return x\n", "from demo import f\ndef test_f():\n    assert f(2)==2\n"
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    (repo / "src/demo/__init__.py").write_text("def f(x):\n    return x+1\n")
    with pytest.raises(ValueError, match="source/environment mismatch"):
        load_trace(out, repo)


def test_source_map_is_checked_even_if_tampered_artifact_hashes_are_resealed(tmp_path):
    repo, project, out = fixture(
        tmp_path, "def f(x):\n    return x\n", "from demo import f\ndef test_f():\n    assert f(2)==2\n"
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    sites = read(out / "sites.json")
    sites["entry"]["source"] = "invented statement"
    write(out / "sites.json", sites)
    manifest = read(out / "manifest.json")
    manifest["files"]["sites.json"] = file_hash(out / "sites.json")
    manifest["fingerprint"] = digest({k: v for k, v in manifest.items() if k != "fingerprint"})
    write(out / "manifest.json", manifest)
    with pytest.raises(ValueError, match="source map"):
        load_trace(out, repo)


def test_multiline_crlf_source_and_future_annotations(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "from __future__ import annotations\ndef f(x: Undefined) -> Unknown:\n    y = (\n        x + 1\n    )\n    return y\n",
        "from demo import f\ndef test_f():\n    assert f(2)==3\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    assert query_slice(out, repo, return_event(out), "y")["nodes"]


def test_reserved_module_global_is_not_removed_when_patch_was_refused(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt"
    monkeypatch.setenv("SLICE_TEST_COUNTER", str(receipt))
    repo, project, out = fixture(
        tmp_path,
        "__specmind_slice_runtime__=42\ndef f(x):\n    return x\n",
        "from demo import f\ndef test_f():\n    assert f(2)==2\n",
    )
    (repo / "tests/conftest.py").write_text(
        "import pytest,os,demo\nfrom pathlib import Path\n@pytest.hookimpl(trylast=True)\ndef pytest_sessionfinish(session,exitstatus):\n    with Path(os.environ['SLICE_TEST_COUNTER']).open('a') as p: p.write('kept\\n' if getattr(demo,'__specmind_slice_runtime__',None)==42 else 'missing\\n')\n"
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert receipt.read_text().splitlines() == ["kept", "kept"]


def test_fixture_and_test_calls_have_distinct_phases(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def f(x):\n    return x+1\n",
        "import pytest\nfrom demo import f\n@pytest.fixture\ndef value():\n    return f(1)\ndef test_f(value):\n    assert f(value)==3\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    assert [c["phase"] for c in read(out / "calls.json")] == ["setup", "call"]




def test_complete_trace_requires_baseline_behavior_evidence(tmp_path):
    repo, project, out = fixture(
        tmp_path, "def f(x):\n    return x\n", "from demo import f\ndef test_f():\n    assert f(2)==2\n"
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    manifest = read(out / "manifest.json")
    del manifest["files"]["baseline.json"]
    manifest["fingerprint"] = digest({k: v for k, v in manifest.items() if k != "fingerprint"})
    write(out / "manifest.json", manifest)
    with pytest.raises(ValueError, match="required hashes"):
        load_trace(out, repo)


def test_reconstructed_function_with_external_globals_is_explicitly_unsupported(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "import types\ndef f(x):\n    return x+1\nf=types.FunctionType(f.__code__, {'__builtins__':__builtins__},'f')\n",
        "from demo import f\ndef test_f():\n    assert f(2)==3\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert "module-owned" in " ".join(read(out / "summary.json")["issues"])
