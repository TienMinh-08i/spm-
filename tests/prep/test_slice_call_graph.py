"""Hand-authored call-boundary graphs independent of the recorder."""
from copy import deepcopy
import pytest
from specmind_prep.slicing import build_ddg, backward_slice


def event(ident, kind, *, frame=1, reads=None, writes=(), links=None, site="statement", **extra):
    return dict(
        id=ident,
        kind=kind,
        frame=frame,
        function="target" if frame == 2 else "caller",
        reads=reads or {},
        writes=list(writes),
        links=links or {},
        site=site,
        inputs=[],
        controls=[],
        values={},
        **extra,
    )


def trace():
    return [
        event(1, "call"),
        event(2, "assignment", writes=["a"]),
        event(3, "assignment", writes=["b"]),
        event(4, "argument", reads={"a": 2}, site="call:arg:0"),
        event(5, "argument", reads={"b": 3}, site="call:arg:1"),
        event(6, "invocation", site="call", callee="target", arguments={"x": 4, "unused": 5}),
        event(7, "parameter", frame=2, writes=["x"], links={"invocation": 6, "argument": 4}),
        event(8, "parameter", frame=2, writes=["unused"], links={"invocation": 6, "argument": 5}),
        event(9, "call", frame=2, reads={"x": 7, "unused": 8}, links={"invocation": 6}),
        event(10, "return", frame=2, reads={"x": 7}, writes=["$return"], links={"invocation": 6}),
        event(11, "call_result", links={"invocation": 6, "return": 10}),
        event(12, "assignment", writes=["r"], links={"result:11": 11}),
        event(13, "return", reads={"r": 12}, writes=["$return"]),
    ]


def test_backward_reaches_actual_definition_without_pulling_unused_argument():
    nodes = build_ddg(trace(), interprocedural=True)
    assert [n["id"] for n in backward_slice(nodes, 13, "$return", "after")["nodes"]] == [
        2,
        4,
        6,
        7,
        10,
        11,
        12,
        13,
    ]
    assert nodes[5]["depends_on"] == []  # argument list is metadata, not a blanket data edge


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_parameter",
        "future_return",
        "reused_bridge",
        "reused_arguments",
        "missing_boundary",
        "wrong_callee",
    ],
)
def test_invalid_call_boundary_provenance_is_rejected(fault):
    events = deepcopy(trace())
    if fault == "wrong_parameter":
        events[6]["links"]["argument"] = 5
    elif fault == "future_return":
        events[10]["links"]["return"] = 12
    elif fault == "reused_bridge":
        events.insert(11, event(12, "call_result", links={"invocation": 6, "return": 10}))
    elif fault == "reused_arguments":
        events.append(event(14, "invocation", site="call", callee="target", arguments={"x": 4, "unused": 5}))
    elif fault == "missing_boundary":
        del events[9]["links"]["invocation"]
    elif fault == "wrong_callee":
        events[5]["callee"] = "other_target"
    with pytest.raises(ValueError):
        build_ddg(events, interprocedural=True)


def test_legacy_builder_cannot_silently_accept_cross_frame_links():
    with pytest.raises(ValueError):
        build_ddg(trace())
