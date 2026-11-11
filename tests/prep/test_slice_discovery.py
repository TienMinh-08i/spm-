from specmind_eval.common.storage import read
from specmind_prep import slice_discovery


def test_observed_shared_helper_keeps_api_identity_and_real_source_frames(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/demo/__init__.py").write_text("def helper(x, kind):\n    return x\ndef api_a(x):\n    return helper(x, 'a')\ndef api_b(x):\n    return helper(x, 'b')\n")
    nodeid = "tests/test_demo.py::test_f"
    (repo / "tests/test_demo.py").write_text("from demo import api_a, api_b\ndef test_f():\n    assert api_a(1) == 1\n    assert api_b(2) == 2\n")
    problems = [{"function_id": "demo.api_" + tag, "problem_id": "demo.helper",
                 "repo_path": "src/demo/__init__.py", "code_line": 1,
                 "_delegate_binding": {"api_path": "src/demo/__init__.py", "api_code_line": line,
                                       "constants": {"kind": tag}, "argument_map": {"x": "x"},
                                       "require_ancestor": False}}
                for tag, line in [("a", 3), ("b", 5)]]
    monkeypatch.setattr(slice_discovery, "load_prepared_dataset", lambda *args:
                        (problems, {p["function_id"]: [{"nodeid": nodeid}] for p in problems}, {"fingerprint": "test"}))
    out = tmp_path / "discovery"
    summary = slice_discovery.discover(tmp_path / "dataset", repo, {"name": "demo", "packages": ["demo"]}, out)
    assert summary["execution_complete"] and summary["observed_targets"] == 2
    assert summary["context_generated"] is False
    for row in read(out / "coverage.json"):
        calls = [c for c in row["calls"] if c["role"] == "target"]
        assert len(calls) == 1
        assert calls[0]["parents"][0]["qualname"] == row["task"].split(".")[-1]
        assert all((repo / p["file"]).is_file() for c in row["calls"] for p in c["parents"])
