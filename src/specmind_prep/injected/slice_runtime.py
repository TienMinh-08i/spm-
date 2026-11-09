"""Primitive-only runtime recorder for occurrence-based dynamic dependence traces."""
import contextlib
import builtins
import json
import math
import sys
import threading


class TraceLimit(RuntimeError):
    pass


def primitive(value):
    """Do not invoke user repr, equality, properties, or serialization hooks."""
    if type(value) not in (type(None), bool, int, float, str):
        raise ValueError("Only exact None/bool/int/finite float/str values are modeled")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("Non-finite float is outside this initial model")
    if type(value) is int and value.bit_length() > 4096:
        raise ValueError("Integer exceeds the bounded primitive snapshot")
    if type(value) is str and len(value) > 16384:
        raise ValueError("String exceeds the bounded primitive snapshot")
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Non-UTF8 string is outside this initial model") from exc
    return {"type": type(value).__name__, "value": value.hex() if type(value) is float else value}


class Recorder:
    def __init__(self, config):
        self.config = config
        self.events = []
        self.calls = []
        self.issues = []
        self.frames = {}
        self.active = False
        self.phase = "protocol"
        self.limited = False
        self.owner = threading.get_ident()
        self.encoded_bytes = 0
        self.operations = {
            name: getattr(builtins, name)
            for name in ("int", "float", "bool", "str", "abs", "min", "max", "print")
        }

    def issue(self, reason):
        if reason not in self.issues:
            self.issues.append(reason)

    def snapshot(self, value):
        try:
            return primitive(value)
        except ValueError as exc:
            self.issue(str(exc))
            return {"type": "unmodeled"}

    def emit(
        self, frame, site, kind, reads=None, inputs=(), writes=(), values=None, *, links=None, details=None
    ):
        if len(self.events) >= self.config["max_events"]:
            self.limited = True
            raise TraceLimit("Dynamic slicing event budget exceeded")
        state = self.frames[frame]
        ident = len(self.events) + 1
        event = {
            "id": ident,
            "frame": frame,
            "site": site,
            "kind": kind,
            "reads": dict(reads or {}),
            "inputs": sorted(set(inputs)),
            "controls": list(state["controls"]),
            "writes": list(writes),
            "values": values or {},
        }
        if links is not None:
            event["links"] = dict(links)
        if details:
            event.update(details)
        size = len(json.dumps(event, ensure_ascii=True).encode("utf-8"))
        if self.encoded_bytes + size > self.config.get("max_bytes", 8_000_000):
            self.limited = True
            raise TraceLimit("Dynamic slicing byte budget exceeded")
        self.encoded_bytes += size
        self.events.append(event)
        for name in writes:
            state["definitions"][name] = ident
        return ident

    def enter(self, arguments):
        if not self.active:
            return None
        if threading.get_ident() != self.owner:
            self.issue("Concurrent or worker-thread calls are unsupported")
            return None
        frame = len(self.calls) + 1
        self.frames[frame] = {"definitions": {}, "controls": [], "pending": None, "predicate": None}
        caller = sys._getframe(1).f_back
        call = {
            "frame": frame,
            "function": self.config["function"],
            "caller": {
                "function": caller.f_globals.get("__name__", "") + "." + caller.f_code.co_qualname,
                "file": caller.f_code.co_filename,
                "line": caller.f_lineno,
            },
            "phase": self.phase,
            "arguments": {name: self.snapshot(value) for name, value in arguments.items()},
            "return": None,
            "returned": False,
        }
        self.calls.append(call)
        for name, value in arguments.items():
            self.emit(frame, "entry", "parameter", writes=[name], values={name: self.snapshot(value)})
        call["event"] = self.emit(frame, "entry", "call", reads=self.frames[frame]["definitions"])
        return frame

    def begin(self, frame, site):
        if frame is not None and not self.limited:
            self.frames[frame]["pending"] = {"site": site, "reads": {}, "inputs": set()}

    def read(self, frame, name, value):
        if frame is not None and not self.limited:
            self.snapshot(value)
            state = self.frames[frame]
            producer = state["definitions"].get(name)
            if producer is None:
                self.issue("No observed local definition for " + name)
            elif state["pending"] is not None:
                state["pending"]["reads"][name] = producer
        return value

    def call(self, frame, name, function, *arguments):
        if frame is not None and name in self.operations and function is not self.operations[name]:
            self.issue("Builtin operation was rebound at execution: " + name)
        return function(*arguments)

    def input_value(self, frame, site, value):
        if frame is not None and not self.limited:
            ident = self.emit(frame, site, "input", values={"input": self.snapshot(value)})
            self.frames[frame]["pending"]["inputs"].add(ident)
        return value

    def finish(self, frame, kind, values=None):
        if frame is None or self.limited:
            return None
        state = self.frames[frame]
        pending = state["pending"]
        if pending is None:
            self.issue("Missing statement observation boundary")
            return None
        snapshots = {name: self.snapshot(value) for name, value in (values or {}).items()}
        ident = self.emit(
            frame, pending["site"], kind, pending["reads"], pending["inputs"], snapshots.keys(), snapshots
        )
        state["pending"] = None
        return ident

    def write(self, frame, values):
        self.finish(frame, "assignment", values)

    def expression(self, frame):
        self.finish(frame, "expression")

    def predicate(self, frame, value):
        if frame is None or self.limited:
            return value
        self.snapshot(value)
        ident = self.finish(frame, "predicate")
        self.frames[frame]["predicate"] = ident
        if ident is not None and type(value) in (type(None), bool, int, float, str):
            self.events[ident - 1]["outcome"] = bool(value)
        return value

    @contextlib.contextmanager
    def control(self, frame):
        if frame is None or self.limited:
            yield
            return
        state = self.frames[frame]
        ident = state["predicate"]
        state["controls"].append(ident)
        try:
            yield
        finally:
            state["controls"].pop()

    def return_value(self, frame, value):
        if frame is not None and not self.limited:
            ident = self.finish(frame, "return", {"$return": value})
            call = self.calls[frame - 1]
            call.update(returned=True, return_event=ident)
            call["return"] = self.snapshot(value)
        return value

    def raised(self, frame):
        if frame is None:
            return
        exc = sys.exc_info()[1]
        self.calls[frame - 1]["raised"] = True
        if not isinstance(exc, TraceLimit):
            self.issue(
                "Exception propagation within the instrumented function is not modeled: " + type(exc).__name__
            )

    def leave(self, frame):
        if frame is None:
            return
        call = self.calls[frame - 1]
        if not call["returned"] and not call.get("raised") and not self.limited:
            self.issue("No modeled return event")

    def result(self):
        return {
            "schema": "dynamic-execution-v1",
            "events": self.events,
            "calls": self.calls,
            "issues": self.issues,
            "limited": self.limited,
        }
