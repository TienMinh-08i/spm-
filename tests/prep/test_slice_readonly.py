from copy import deepcopy
import pytest
from specmind_prep.slice_readonly import build_ddg
from specmind_prep.slicing import backward_slice


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
