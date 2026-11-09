from copy import deepcopy
import pytest
from specmind_prep.slice_readonly import build_ddg
from specmind_prep.slicing import backward_slice
from specmind_prep.injected.slice_runtime import primitive


def event(i, kind, *, frame=1, data=(), reads=None, writes=(), controls=(), links=None, **more):
    return dict(
        id=i,
        frame=frame,
        function="r" if frame == 1 else "h",
        site=str(i),
        kind=kind,
        data=list(data),
        reads=reads or {},
        writes=list(writes),
        controls=list(controls),
        links=links or {},
        inputs=[],
        values={},
        **more,
    )


def oracle():
    return [
        event(1, "external_input"),
        event(2, "parameter", data=[1], writes=["obj"]),
        event(3, "expression", reads={"obj": 2}),
        event(4, "predicate", data=[3]),
        event(5, "argument", data=[3], controls=[4]),
        event(6, "invocation", controls=[4], callee="h", arguments={"obj": 5}),
        event(7, "parameter", frame=2, writes=["obj"], links={"frame_invocation": 6, "argument": 5}),
        event(8, "expression", frame=2, links={"frame_invocation": 6}),
        event(9, "return", frame=2, data=[8], writes=["$return"], links={"frame_invocation": 6}),
        event(10, "call_result", controls=[4], links={"result_invocation": 6, "return": 9}),
        event(11, "expression", data=[3, 10], controls=[4]),
        event(12, "return", data=[11], writes=["$return"]),
    ]


def test_graph_oracle_keeps_control_and_drops_unused_helper_parameter():
    nodes = build_ddg(oracle())
    assert [n["id"] for n in backward_slice(nodes, 12, "$return", "after")["nodes"]] == [
        1,
        2,
        3,
        4,
        6,
        8,
        9,
        10,
        11,
        12,
    ]
    assert [n["id"] for n in backward_slice(nodes, 7, "obj", "after")["nodes"]] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    "fault",
    ["future_data", "wrong_local_frame", "non_predicate", "wrong_parameter", "wrong_return", "reused_return"],
)
def test_graph_rejects_invalid_producers(fault):
    events = deepcopy(oracle())
    if fault == "future_data":
        events[2]["data"] = [12]
    elif fault == "wrong_local_frame":
        events[7]["data"] = [3]
    elif fault == "non_predicate":
        events[4]["controls"] = [3]
    elif fault == "wrong_parameter":
        events[6]["links"]["argument"] = 3
    elif fault == "wrong_return":
        events[9]["links"]["return"] = 8
    else:
        events.append(event(13, "call_result", links={"result_invocation": 6, "return": 9}))
    with pytest.raises(ValueError):
        build_ddg(events)


def test_metaclass_hooks_are_not_called_by_primitive_or_readonly_snapshot():
    from specmind_prep.injected.slice_readonly import Runtime

    class Meta(type):
        def __eq__(cls, other):
            raise AssertionError("Logger executed metaclass equality")

    class Item(metaclass=Meta):
        def __repr__(self):
            raise AssertionError("Logger executed repr")

    item = Item()
    with pytest.raises(ValueError):
        primitive(item)
    runtime = Runtime({"max_events": 100, "max_bytes": 10000, "functions": {}})
    assert runtime.snapshot(item) == {"kind": "unsupported"}


def test_abc_method_patch_is_detected_even_without_registry_change(monkeypatch):
    import abc
    from collections.abc import Mapping
    from specmind_prep.injected.slice_readonly import Runtime

    runtime = Runtime({"max_events": 100, "max_bytes": 10000, "functions": {}})
    runtime.abc_token = abc.get_cache_token()
    monkeypatch.setattr(Mapping, "__subclasshook__", classmethod(lambda cls, other: True))
    assert abc.get_cache_token() == runtime.abc_token
    runtime.guard()
    assert runtime.issues


@pytest.mark.parametrize(
    "source",
    [
        "def root(obj):\n    obj[0]=1\n    return True\n",
        "def root(obj):\n    return obj.strip()\n",
        "def root(obj):\n    return arbitrary(obj)\n",
    ],
)
def test_readonly_analyzer_refuses_writes_and_unknown_protocols(source):
    from specmind_prep.injected.slice_readonly import analyze
    from specmind_prep.injected.slice_instrumentation import Unsupported

    with pytest.raises(Unsupported):
        analyze(source, 1, "sample.py", "demo.root", {})


def test_expression_instrumentation_evaluates_operations_once_without_subprocess(monkeypatch):
    import abc, types
    from specmind_prep.injected.slice_readonly import Runtime, instrument, HELPER, OPERATIONS

    original = OPERATIONS["hasattr"]
    seen = []

    def counted(obj, name):
        seen.append(name)
        return original(obj, name)

    monkeypatch.setitem(OPERATIONS, "hasattr", counted)
    source = "def root(obj):\n    return hasattr(obj,'__iter__') and not hasattr(obj,'strip')\n"
    namespace = {"hasattr": counted}
    exec(compile(source, "sample.py", "exec"), namespace)
    fn = namespace["root"]
    config = {
        "function": "demo.root",
        "max_events": 200,
        "max_bytes": 100000,
        "test_file": "outside.py",
        "external_sites": {},
        "functions": {
            "demo.root": {"bindings": {"hasattr": "hasattr"}, "parameter": "obj", "source_file": "sample.py"}
        },
    }
    runtime = Runtime(config)
    runtime.abc_token = abc.get_cache_token()
    runtime.active = True
    runtime.globals = {"demo.root": namespace}
    runtime.bindings = {"demo.root": fn}
    runtime.original_functions = {"demo.root": types.FunctionType(fn.__code__, namespace)}
    code, _ = instrument(source, 1, "sample.py", "demo.root", {"hasattr": "hasattr"})
    fn.__code__ = code
    runtime.codes = {"demo.root": code}
    namespace[HELPER] = runtime
    assert fn([]) is True and fn(b"abc") is False
    assert seen == ["__iter__", "strip", "__iter__", "strip"]
    nodes = build_ddg(runtime.events)
    for call in runtime.calls:
        result = backward_slice(nodes, call["return_event"], "$return", "after")
        assert {n["frame"] for n in result["nodes"]} == {call["frame"]}


def test_external_origin_never_selects_another_call_on_the_same_line():
    from specmind_prep.injected.slice_readonly import external_sites

    sites = external_sites("root(obj=b'abc'); bool(1)\n", "tests/test_x.py")
    assert not any(1 in s["unambiguous_lines"] for s in sites.values())
    sites = external_sites("root(\n    make(1)\n)\n", "tests/test_x.py")
    assert not any(2 in s["unambiguous_lines"] for s in sites.values())
