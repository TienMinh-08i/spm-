import hashlib
import json

import pytest

from specmind_eval.context.callgraph import CallGraph, build_context_pack
from specmind_eval.report import token_usage


def test_modes_budget_and_direct_neighbors():
    nodes = {
        "m.f": {"id": "m.f", "module": "m", "source": "def f(): return g()"},
        "m.g": {"id": "m.g", "module": "m", "source": "def g():\n    \"\"\"Return one.\"\"\"\n    return 1"},
        "tests/test_m.py::test_f": {
            "id": "tests/test_m.py::test_f",
            "kind": "test",
            "source": "def test_f(): assert f() == 1",
        },
    }
    graph = CallGraph(
        nodes,
        [
            {"caller": "m.f", "callee": "m.g", "confidence": "high"},
            {"caller": "tests/test_m.py::test_f", "callee": "m.f", "confidence": "high"},
        ],
        "graph",
    )
    assert not build_context_pack(graph, "m.f", mode="none")["repository_context"]
    for mode, counts in [("callee", (1, 0)), ("caller", (0, 1)), ("callgraph", (1, 1))]:
        pack = build_context_pack(graph, "m.f", mode=mode)
        assert (len(pack["callees"]), len(pack["callers"])) == counts
        assert len(pack["repository_context"]) == pack["metadata"]["actual_chars"] <= 8000
    assert build_context_pack(graph, "m.f", budget=1)["metadata"]["status"] == "budget_too_small"


def test_graph_checksum_and_checkout_guards(tmp_path, monkeypatch):
    graph_dir, repo = tmp_path / "graph", tmp_path / "repo"
    graph_dir.mkdir()
    repo.mkdir()
    (repo / "f.py").write_bytes(b"def f():\n    pass\n")
    nodes = [{"id": "f", "source": "def f(): pass"}]
    for name, data in [("nodes.json", nodes), ("edges.json", [])]:
        (graph_dir / name).write_text(json.dumps(data))
    manifest = {
        "git_commit": "test",
        "source_files": {"f.py": hashlib.sha256(b"def f():\r\n    pass\r\n").hexdigest()},
        "artifact_sha256": {
            n: hashlib.sha256((graph_dir / n).read_bytes()).hexdigest() for n in ["nodes.json", "edges.json"]
        },
    }
    (graph_dir / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr("specmind_eval.context.callgraph.verify_commit", lambda *a: None)
    assert CallGraph.load(graph_dir, repo).nodes["f"]
    (repo / "f.py").write_text("def f(): return 2")
    with pytest.raises(ValueError, match="version mismatch"):
        CallGraph.load(graph_dir, repo)
    (graph_dir / "edges.json").write_text("[{}]")
    with pytest.raises(ValueError, match="checksum mismatch"):
        CallGraph.load(graph_dir, repo)


def test_missing_tokens_are_not_reported_as_zero():
    assert token_usage({"attempts": [{"usage": {}}]})["tokens"] is None
    assert (
        token_usage({"attempts": [{"usage": {"total_tokens": 3}}, {"usage": {"total_tokens": 5}}]})["tokens"]
        == 8
    )
