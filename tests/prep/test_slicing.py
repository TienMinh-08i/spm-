"""Hand-written dependency oracles, including the paper's loop counterexamples."""
import pytest
from specmind_prep.slicing import build_ddg, backward_slice


def e(ident, site, *, reads=None, writes=(), controls=(), kind="assignment", frame=1):
    return {
        "id": ident,
        "site": str(site),
        "kind": kind,
        "frame": frame,
        "reads": reads or {},
        "inputs": [],
        "writes": list(writes),
        "controls": list(controls),
        "values": {},
    }


def test_overwritten_definition_is_not_in_backward_slice():
    nodes = build_ddg(
        [
            e(1, 1, writes=["x"], kind="parameter"),
            e(2, 2, reads={"x": 1}, writes=["y"]),
            e(3, 3, writes=["y"]),
            e(4, 4, reads={"y": 3}, kind="return"),
        ]
    )
    assert [n["id"] for n in backward_slice(nodes, 4, "y")["nodes"]] == [3]


def test_paper_figure7_second_iteration_does_not_reuse_first_branch():
    trace = [
        e(1, 1, writes=["n"], kind="input"),
        e(2, 2, writes=["i"]),
        e(3, 3, reads={"i": 2, "n": 1}, kind="predicate"),
        e(4, 4, writes=["x"], controls=[3], kind="input"),
        e(5, 5, reads={"x": 4}, controls=[3], kind="predicate"),
        e(6, 6, reads={"x": 4}, writes=["y"], controls=[3, 5]),
        e(7, 8, reads={"y": 6}, writes=["z"], controls=[3]),
        e(8, 10, reads={"i": 2}, writes=["i"], controls=[3]),
        e(9, 3, reads={"i": 8, "n": 1}, kind="predicate"),
        e(10, 4, writes=["x"], controls=[9], kind="input"),
        e(11, 5, reads={"x": 10}, controls=[9], kind="predicate"),
        e(12, 7, reads={"x": 10}, writes=["y"], controls=[9, 11]),
        e(13, 8, reads={"y": 12}, writes=["z"], controls=[9]),
        e(14, 9, reads={"z": 13}, kind="return"),
    ]
    nodes = build_ddg(trace)
    first = backward_slice(nodes, 7, "z", when="after")
    last = backward_slice(nodes, 14, "z")
    assert 6 in {n["id"] for n in first["nodes"]}
    assert {n["site"] for n in last["nodes"]} == {"1", "2", "3", "4", "5", "7", "8", "10"}
    assert not {4, 5, 6, 7} & {n["id"] for n in last["nodes"]}
    assert nodes[8]["occurrence"] == 2


@pytest.mark.parametrize(
    "trace",
    [
        [e(1, 1, reads={"x": 2}, writes=["y"])],
        [e(1, 1, writes=["x"]), e(2, 2, writes=["x"]), e(3, 3, reads={"x": 1})],
        [e(1, 1, writes=["x"]), e(2, 2, reads={"x": 1}, frame=2)],
        [e(1, 1, writes=["x"]), e(2, 2, controls=[1])],
    ],
)
def test_invalid_or_stale_dependencies_are_rejected(trace):
    with pytest.raises(ValueError):
        build_ddg(trace)


def test_before_and_after_are_different_observations():
    nodes = build_ddg([e(1, 1, writes=["x"]), e(2, 2, reads={"x": 1}, writes=["x"])])
    assert backward_slice(nodes, 2, "x", "before")["root"] == 1
    assert backward_slice(nodes, 2, "x", "after")["root"] == 2
    with pytest.raises(ValueError, match="Unknown event"):
        backward_slice(nodes, 3, "x")
