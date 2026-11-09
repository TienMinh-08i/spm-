"""Independent graph oracles and real subprocess tests of the read-only scope."""
from copy import deepcopy

import pytest

from specmind_eval.common.storage import read, write
from specmind_eval.common.fingerprint import digest, file_hash
from specmind_prep.project import validate_project
from specmind_prep.slicing import trace_function, query_slice, backward_slice, load_trace
from specmind_prep.slice_readonly import build_ddg
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


def fixture(tmp_path, source, test):
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/demo/__init__.py").write_text(source, encoding="utf-8")
    (repo / "tests/test_ro.py").write_text(test, encoding="utf-8")
    return repo, validate_project({"name": "demo", "packages": ["demo"]}, repo), tmp_path / "trace"


def run(repo, project, out, helpers=(), **kw):
    return trace_function(
        repo,
        project,
        "tests/test_ro.py::test_f",
        "demo.root",
        out,
        readonly_helpers=["demo." + name for name in helpers],
        **kw,
    )


def root_slices(out, repo):
    return [
        query_slice(out, repo, c["return_event"], "$return", when="after")
        for c in read(out / "calls.json")
        if c["function"] == "demo.root"
    ]


def test_three_frames_and_short_circuit_never_mix_invocation_roles(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        """def leaf(obj):
    return not obj
def helper(obj):
    return obj or leaf(obj)
def root(obj):
    return obj and helper(False)
""",
        "from demo import root\ndef test_f():\n    assert root(True) is True\n    assert root(False) is False\n",
    )
    assert run(repo, p, out, ["helper", "leaf"])["status"] == "complete_supported"
    calls = read(out / "calls.json")
    assert [c["function"] for c in calls] == ["demo.root", "demo.helper", "demo.leaf", "demo.root"]
    first, second = root_slices(out, repo)
    assert {n["function"] for n in first["nodes"]} == {"demo.root", "demo.helper", "demo.leaf"}
    assert {n["frame"] for n in second["nodes"]} == {4}
    assert all(
        n["links"].get("frame_invocation") != n["links"].get("result_invocation")
        for n in first["nodes"]
        if n["kind"] == "call_result"
    )


def test_conditional_call_argument_includes_flag_control(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        "def helper(obj):\n    return True\ndef root(obj):\n    return obj and helper(False)\n",
        "from demo import root\ndef test_f():\n    assert root(True) is True\n",
    )
    assert run(repo, p, out, ["helper"])["status"] == "complete_supported"
    call = next(c for c in read(out / "calls.json") if c["function"] == "demo.helper")
    result = query_slice(out, repo, call["event"], "obj")
    assert any(n["kind"] == "predicate" for n in result["nodes"])
    assert any(n["kind"] == "external_input" for n in result["nodes"])


@pytest.mark.parametrize(
    "value,expected", [("[1,'a',{}]", True), ("('a',2.3)", True), ("{'a':1}", False), ("b'abc'", False)]
)
def test_real_operation_shape_and_input_source(tmp_path, value, expected):
    repo, p, out = fixture(
        tmp_path,
        """import inspect
from collections.abc import Mapping
def generator(obj):
    return inspect.isgeneratorfunction(obj) or inspect.isgenerator(obj)
def helper(obj):
    return (hasattr(obj,'__iter__') and not hasattr(obj,'strip')) or generator(obj)
def root(obj):
    return helper(obj) and not isinstance(obj,Mapping)
""",
        f"from demo import root\ndef test_f():\n    assert root({value}) is {expected}\n",
    )
    assert run(repo, p, out, ["helper", "generator"])["status"] == "complete_supported"
    result = root_slices(out, repo)[0]
    operations = [n["operation"] for n in result["nodes"] if n["kind"] == "operation"]
    assert operations == (
        ["hasattr", "hasattr", "inspect.isgeneratorfunction", "inspect.isgenerator"]
        if value.startswith("b'")
        else ["hasattr", "hasattr", "isinstance"]
    )
    external = [s for s in result["source_statements"] if s["kind"] == "external_input"]
    assert len(external) == 1 and external[0]["source"] == value


def test_identity_tokens_do_not_retain_input_or_nested_finalizers(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        "def root(obj):\n    return hasattr(obj,'__iter__')\n",
        """import gc
from demo import root
seen=[]
class Item:
    def __del__(self):
        seen.append('gone')
def test_f():
    assert root([Item()]) is True
    gc.collect()
    assert seen==['gone']
    assert root([]) is True
""",
    )
    assert run(repo, p, out)["status"] == "complete_supported"
    roots = [c for c in read(out / "calls.json") if c["function"] == "demo.root"]
    assert [c["arguments"]["obj"]["token"] for c in roots] == [1, 2]


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


@pytest.mark.parametrize(
    "setup",
    [
        "import demo; demo.Mapping=object",
        "import demo; demo.helper=lambda obj: True",
        "import abc;from collections.abc import Mapping;Mapping.register(list)",
    ],
)
def test_changed_bindings_or_registry_are_not_accepted(tmp_path, setup):
    repo, p, out = fixture(
        tmp_path,
        "from collections.abc import Mapping\ndef helper(obj):\n    return hasattr(obj,'strip')\ndef root(obj):\n    return helper(obj)\n",
        "from demo import root\ndef test_f():\n    " + setup + "\n    root([])\n",
    )
    assert run(repo, p, out, ["helper"])["status"] == "unsupported"


def test_unsupported_container_truth_and_custom_subclass(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        "def root(obj):\n    return not obj\n",
        "from demo import root\ndef test_f():\n    assert root([]) is True\n",
    )
    assert run(repo, p, out)["status"] == "unsupported"


def test_custom_subclass_is_refused_without_extra_attribute_reads(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        "def root(obj):\n    return hasattr(obj,'strip')\n",
        """from demo import root
seen=[]
class L(list):
    def __getattribute__(self,name):
        seen.append(name)
        return super().__getattribute__(name)
def test_f():
    assert root(L()) is False
    assert seen==['strip']
""",
    )
    assert run(repo, p, out)["status"] == "unsupported"
    assert read(out / "baseline.json")["exitcode"] == 0
    assert not (out / "instrumented.json").exists()


def test_budget_stops_and_query_never_reruns(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        "def root(obj):\n    return hasattr(obj,'strip')\n",
        "from demo import root\ndef test_f():\n    assert root(b'abc') is True\n",
    )
    assert run(repo, p, out, max_events=2)["status"] == "incomplete"
    assert query_slice(out, repo, 1, "obj")["nodes"] is None


def test_resealed_bad_source_map_is_refused(tmp_path):
    repo, p, out = fixture(
        tmp_path,
        "def root(obj):\n    return hasattr(obj,'strip')\n",
        "from demo import root\ndef test_f():\n    assert root(b'abc') is True\n",
    )
    assert run(repo, p, out)["status"] == "complete_supported"
    sites = read(out / "sites.json")
    key = next(iter(sites))
    sites[key]["source"] = "fabricated"
    write(out / "sites.json", sites)
    manifest = read(out / "manifest.json")
    manifest["files"]["sites.json"] = file_hash(out / "sites.json")
    manifest["fingerprint"] = digest({k: v for k, v in manifest.items() if k != "fingerprint"})
    write(out / "manifest.json", manifest)
    with pytest.raises(ValueError, match="source map"):
        load_trace(out, repo)


def test_external_origin_never_selects_another_call_on_the_same_line():
    from specmind_prep.injected.slice_readonly import external_sites

    sites = external_sites("root(obj=b'abc'); bool(1)\n", "tests/test_x.py")
    assert not any(1 in s["unambiguous_lines"] for s in sites.values())
    sites = external_sites("root(\n    make(1)\n)\n", "tests/test_x.py")
    assert not any(2 in s["unambiguous_lines"] for s in sites.values())


def test_readonly_cli_and_return_query(tmp_path, capsys):
    from specmind_prep.cli import main

    repo, p, out = fixture(
        tmp_path,
        "def root(obj):\n    return hasattr(obj,'strip')\n",
        "from demo import root\ndef test_f():\n    assert root(b'abc') is True\n",
    )
    config = tmp_path / "project.json"
    write(config, p)
    assert (
        main(
            [
                "slice-trace",
                "--repo",
                str(repo),
                "--project",
                str(config),
                "--test",
                "tests/test_ro.py::test_f",
                "--target",
                "demo.root",
                "--readonly-helpers",
                "--output",
                str(out),
            ]
        )
        == 0
    )
    call = read(out / "calls.json")[0]
    query = tmp_path / "slice.json"
    assert (
        main(
            [
                "slice-call",
                "--repo",
                str(repo),
                "--trace",
                str(out),
                "--event",
                str(call["return_event"]),
                "--variable",
                "$return",
                "--when",
                "after",
                "--output",
                str(query),
            ]
        )
        == 0
    )
    assert read(query)["scope"]["object_contents_traced"] is False
    assert [n["operation"] for n in read(query)["nodes"] if n["kind"] == "operation"] == ["hasattr"]
    capsys.readouterr()
