"""In-memory semantic checks independent of the pytest subprocess bridge."""
import pytest
from specmind_prep.injected.slice_instrumentation import HELPER, instrument
from specmind_prep.injected.slice_runtime import Recorder, TraceLimit, primitive
from specmind_prep.slicing import build_ddg


def attach(source):
    namespace = {"__name__": "demo"}
    exec(compile(source, "demo.py", "exec"), namespace)
    original = namespace["f"].__code__
    recorder = Recorder({"function": "demo.f", "max_events": 1000, "max_bytes": 1000000})
    replacement, sites = instrument(source, original.co_firstlineno, "demo.py", namespace)
    namespace[HELPER] = recorder
    namespace["f"].__code__ = replacement
    recorder.active = True
    return namespace, recorder, sites


def test_default_expression_only_runs_at_original_definition():
    namespace, runtime, sites = attach(
        "count=0\ndef seed():\n    global count\n    count+=1\n    return 5\ndef f(x=seed()):\n    return x+1\n"
    )
    assert namespace["count"] == 1
    assert namespace["f"]() == 6
    assert namespace["count"] == 1
    assert build_ddg(runtime.events)[-1]["kind"] == "return"


def test_input_expression_is_evaluated_once_with_actual_order(monkeypatch):
    values = iter(["3", "4"])
    calls = []

    def provide():
        value = next(values)
        calls.append(value)
        return value

    monkeypatch.setattr("builtins.input", provide)
    namespace, runtime, sites = attach(
        "def f():\n    x=int(input())\n    y=int(input())\n    return x*10+y\n"
    )
    assert namespace["f"]() == 34 and calls == ["3", "4"]
    assert len([e for e in runtime.events if e["kind"] == "input"]) == 2


def test_nonprimitive_snapshot_does_not_call_user_hooks():
    class Object:
        def __repr__(self):
            raise AssertionError("repr invoked")

        def __bool__(self):
            raise AssertionError("bool invoked")

        def __eq__(self, other):
            raise AssertionError("equality invoked")

    with pytest.raises(ValueError):
        primitive(Object())


def test_byte_limit_marks_trace_incomplete():
    runtime = Recorder({"function": "demo.f", "max_events": 100, "max_bytes": 40})
    runtime.active = True
    with pytest.raises(TraceLimit):
        runtime.enter({"x": 1})
    assert runtime.limited is True


def test_primitive_snapshots_preserve_type_and_float_sign():
    assert primitive(True)["type"] == "bool"
    assert primitive(1)["type"] == "int"
    assert primitive(-0.0)["value"] != "0x0.0p+0"
    with pytest.raises(ValueError):
        primitive(float("nan"))
    with pytest.raises(ValueError):
        primitive("\ud800")
