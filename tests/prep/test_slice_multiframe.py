"""Real subprocess oracles for caller/API/helper frame boundaries."""
import pytest
from specmind_prep.project import validate_project
from specmind_prep.slicing import trace_function, load_trace
from specmind_prep.slice_queries import query_invocations


@pytest.mark.parametrize("heap", [False, True])
def test_three_frames_keep_actual_return_and_mutation_dependencies(tmp_path, heap):
    source = ("def helper(x):\n    x['v'] = x['v'] + 1\n    return x['v']\n" if heap else
              "def helper(x):\n    return x + 1\n")
    source += "def api(x):\n    value = helper(x)\n    return value * 2\ndef caller():\n"
    source += "    seed = {'v': 3}\n" if heap else "    seed = 3\n"
    source += "    result = api(seed)\n    return result + 1\n"
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/demo/__init__.py").write_text(source)
    (repo / "tests/test_demo.py").write_text("from demo import caller\ndef test_f():\n    assert caller() == 9\n")
    project = validate_project({"name": "demo", "packages": ["demo"]}, repo)
    out = tmp_path / "trace"
    summary = trace_function(repo, project, "tests/test_demo.py::test_f", "demo.helper", out,
                             caller="demo.caller", helpers=["demo.api"], heap=heap)
    assert summary["status"] == "complete_supported", summary
    nodes, _, manifest = load_trace(out, repo)
    assert {n["function"] for n in nodes} == {"demo.caller", "demo.api", "demo.helper"}
    call = query_invocations(out, repo)["calls"][0]
    assert any("seed" in n["writes"] for n in call["inputs"][0]["nodes"])
    assert any(n["function"] == "demo.caller" and n["kind"] == "return" for n in call["output"]["nodes"])
    bridge = next(n for n in nodes if n["kind"] == "call_result" and n["function"] == "demo.api")
    assert "frame_invocation" in bridge["links"]
    if heap:
        assert len(call["mutations"]) == 1


def test_plain_object_alias_attribute_mutation_has_forward_uses(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    source = "class Box:\n    pass\ndef helper(obj):\n    alias = obj\n    alias.count = obj.count + 1\n    return obj.count\ndef api(obj):\n    value = helper(obj)\n    return value\ndef caller(obj):\n    value = api(obj)\n    return obj.count + value\n"
    (repo / "src/demo/__init__.py").write_text(source)
    (repo / "tests/test_demo.py").write_text("from demo import caller, Box\ndef test_f():\n    obj = Box()\n    obj.count = 3\n    assert caller(obj) == 8\n    assert obj.count == 4\n")
    project = validate_project({"name": "demo", "packages": ["demo"]}, repo)
    out = tmp_path / "trace"
    summary = trace_function(repo, project, "tests/test_demo.py::test_f", "demo.helper", out,
                             caller="demo.caller", helpers=["demo.api"], heap=True)
    assert summary["status"] == "complete_supported", summary
    call = query_invocations(out, repo)["calls"][0]
    assert len(call["mutations"]) == 1
    assert any(n["function"] == "demo.caller" and n["kind"] == "heap_read" for n in call["mutations"][0]["nodes"])


def test_attribute_descriptor_is_rejected_without_executing_user_code():
    from specmind_prep.injected.slice_heap import HeapMemory, UnsupportedHeap
    class Box:
        @property
        def value(self):
            raise AssertionError("descriptor must not be evaluated")
        @value.setter
        def value(self, item):
            raise AssertionError("descriptor must not be evaluated")
    obj = Box()
    obj.__dict__["value"] = 1
    heap = HeapMemory()
    heap.register(obj, 1)
    with pytest.raises(UnsupportedHeap, match="descriptor"):
        heap.read(obj, "value")
    with pytest.raises(UnsupportedHeap, match="descriptor"):
        heap.write(obj, "value", 2, 2)
    assert obj.__dict__["value"] == 1


def test_instance_method_call_links_receiver_and_attribute_writes(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    source = "class Box:\n    def target(self, x):\n        self.value = x + 1\n        return self.value\ndef caller(obj):\n    seed = 4\n    result = obj.target(seed)\n    return result + obj.value\n"
    (repo / "src/demo/__init__.py").write_text(source)
    (repo / "tests/test_demo.py").write_text("from demo import caller, Box\ndef test_f():\n    obj = Box()\n    obj.value = 0\n    assert caller(obj) == 10\n")
    project = validate_project({"name": "demo", "packages": ["demo"]}, repo)
    out = tmp_path / "trace"
    summary = trace_function(repo, project, "tests/test_demo.py::test_f", "demo.Box.target", out,
                             caller="demo.caller", heap=True)
    assert summary["status"] == "complete_supported", summary
    call = query_invocations(out, repo)["calls"][0]
    assert {a["parameter"] for a in call["inputs"]} == {"self", "x"}
    assert len(call["mutations"]) == 1
    assert any(n["function"] == "demo.caller" and n["kind"] == "heap_read" for n in call["mutations"][0]["nodes"])
