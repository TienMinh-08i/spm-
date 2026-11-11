"""Behavior and dependency oracles for acyclic early/implicit returns."""
import pytest
from specmind_prep.injected.slice_instrumentation import HELPER, instrument, analyze, Unsupported
from specmind_prep.injected.slice_runtime import Recorder
from specmind_prep.slicing import build_ddg
from specmind_prep.slice_queries import closure


def execute(source, arguments):
    baseline = {}
    exec(source, baseline)
    expected = baseline["f"](*arguments)
    namespace = {}
    exec(source, namespace)
    recorder = Recorder({"function": "demo.f", "max_events": 1000})
    code, sites = instrument(source, 1, "demo.py", namespace)
    namespace[HELPER] = recorder
    namespace["f"].__code__ = code
    recorder.active = True
    assert namespace["f"](*arguments) == expected
    assert recorder.issues == []
    nodes = build_ddg(recorder.events)
    root = recorder.calls[0]["return_event"]
    selected = closure(nodes, [root], direction="backward")
    return recorder, sites, selected


@pytest.mark.parametrize("outer,inner,expected_lines", [
    (True, True, {1, 2, 3, 4, 5}),
    (True, False, {1, 2, 4, 6}),
    (False, True, {1, 2, 7, 8}),
])
def test_nested_return_paths_keep_control_and_discard_unrelated_writes(outer, inner, expected_lines):
    source = "def f(x, outer, inner):\n    if outer:\n        unused = x + 9\n        if inner:\n            return unused\n        return x\n    y = x * 2\n    return y\n"
    _, sites, nodes = execute(source, (3, outer, inner))
    assert {sites[n["site"]]["line"] for n in nodes} == expected_lines


def test_return_in_else_does_not_evaluate_continuation_on_exit_path():
    source = "def f(x, flag):\n    if flag:\n        x = x + 1\n    else:\n        return 7\n    y = 10 / x\n    return y\n"
    execute(source, (0, False))
    _, sites, nodes = execute(source, (1, True))
    assert {sites[n["site"]]["line"] for n in nodes} == {1, 2, 3, 6, 7}


@pytest.mark.parametrize("flag", [True, False])
def test_implicit_return_records_real_fallthrough_and_explicit_provenance(flag):
    source = "def f(x, flag):\n    if flag:\n        return x\n    unused = x + 2\n"
    runtime, sites, nodes = execute(source, (4, flag))
    assert len([e for e in runtime.events if e["kind"] == "return"]) == 1
    if not flag:
        assert sites[nodes[-1]["site"]]["source"] == "<implicit return None>"
        assert any(n["kind"] == "predicate" for n in nodes)
        assert not any("unused" in n["writes"] for n in nodes)


def test_loop_return_remains_rejected_until_exit_dependencies_are_modeled():
    with pytest.raises(Unsupported, match="CFG"):
        analyze("def f(x):\n    while x:\n        return x\n    return 0\n", 1, "demo.py")


@pytest.mark.parametrize("heap,nested,source,test_body", [
    (False, True, "def target(x):\n    return x + 1\ndef caller():\n    seed = 4\n    result = target(target(seed))\n    return result * 2\n", "assert caller() == 12"),
    (True, False, "def target(obj):\n    obj['x'] = 9\ndef caller():\n    obj = {'x': 0}\n    ignored = target(obj)\n    return obj['x']\n", "assert caller() == 9"),
    (True, True, "def target(obj):\n    obj['x'] = obj['x'] + 1\n    return obj\ndef caller():\n    seed = {'x': 0}\n    result = target(target(seed))\n    return result['x']\n", "assert caller() == 2"),
])
def test_new_flow_crosses_real_subprocess_and_validated_context(tmp_path, heap, nested, source, test_body):
    from specmind_prep.slicing import trace_function, load_trace
    from specmind_prep.project import validate_project
    from specmind_prep.slice_queries import query_invocations
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/demo/__init__.py").write_text(source)
    (repo / "tests/test_demo.py").write_text("from demo import caller\ndef test_f():\n    " + test_body + "\n")
    project = validate_project({"name": "demo", "packages": ["demo"]}, repo)
    out = tmp_path / "trace"
    result = trace_function(repo, project, "tests/test_demo.py::test_f", "demo.target", out,
                            caller="demo.caller", heap=heap)
    assert result["status"] == "complete_supported", result
    nodes, sites, manifest = load_trace(out, repo)
    calls = query_invocations(out, repo)["calls"]
    if not nested:
        assert len(calls) == 1
        assert any(n["kind"] == "heap_read" for n in calls[0]["mutations"][0]["nodes"])
    else:
        assert len(calls) == 2
        inner_return = calls[0]["output"]["root"]
        outer_argument = calls[1]["inputs"][0]
        assert inner_return in {n["id"] for n in outer_argument["nodes"]}
        assert outer_argument["root"] in {n["id"] for n in calls[0]["output"]["nodes"]}
        seed = next(n for n in nodes if "seed" in n["writes"])
        assert seed["id"] in {n["id"] for n in outer_argument["nodes"]}


def test_local_annotation_does_not_evaluate_annotation_expression():
    source = "def f(x):\n    y: nonexistent_type() = x + 1\n    return y\n"
    _, sites, nodes = execute(source, (4,))
    assert {sites[n["site"]]["line"] for n in nodes} == {1, 2, 3}


def test_annotation_without_value_keeps_unbound_local_behavior():
    source = "def f():\n    x: int\n    return x\n"
    namespace = {"x": 42}
    exec(source, namespace)
    code, _ = instrument(source, 1, "demo.py", namespace)
    runtime = Recorder({"function": "demo.f", "max_events": 100})
    namespace[HELPER] = runtime
    namespace["f"].__code__ = code
    runtime.active = True
    with pytest.raises(UnboundLocalError):
        namespace["f"]()


def test_module_constant_is_an_explicit_external_input_origin():
    source = "def f(x):\n    return x + OFFSET\nOFFSET = 7\n"
    _, sites, nodes = execute(source, (2,))
    origin = next(n for n in nodes if n["kind"] == "input")
    assert origin["values"]["$global:OFFSET"]["value"] == 7
    assert sites[origin["site"]]["source"] == "OFFSET"
