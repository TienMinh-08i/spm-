"""Explicit actual/formal and return links across selected Python frames."""
import sys
import threading
import types
from specmind_prep.injected.slice_heap import instance_dict, UnsupportedHeap

from specmind_prep.injected.slice_runtime import Recorder


class MethodBoundary:
    def __init__(self, function, receiver):
        self.function = function
        self.receiver = receiver


def namespace_sites(function, sites, repo_path):
    return {
        function + "|" + key: dict(value, file=repo_path, statement=function + "|" + value["statement"])
        for key, value in sites.items()
    }


class CallerRecorder(Recorder):
    def __init__(self, config):
        super().__init__(config)
        self.target = None
        self.target_code = None
        self.targets = {}
        self.invocations = []

    def emit(
        self, frame, site, kind, reads=None, inputs=(), writes=(), values=None, *, links=None, details=None
    ):
        state = self.frames[frame]
        links = dict(links or {})
        if state.get("invocation") is not None:
            links["frame_invocation" if kind == "call_result" else "invocation"] = state["invocation"]
        if kind in {"assignment", "predicate", "return", "expression"}:
            links.update(state["pending"].get("links", {}))
        return super().emit(
            frame,
            state["function"] + "|" + site,
            kind,
            reads,
            inputs,
            writes,
            values,
            links=links,
            details=dict(details or {}, function=state["function"]),
        )

    def enter(self, arguments, function):
        if not self.active:
            return None
        if threading.get_ident() != self.owner:
            self.issue("Concurrent or worker-thread calls are unsupported")
            return None
        token = self.invocations[-1] if self.invocations and function == self.invocations[-1].get("callee", self.config["function"]) else None
        if function != self.config.get("caller") and token is None:
            self.issue("Target entered outside the selected caller boundary")
        frame = len(self.calls) + 1
        invocation = token["event"] if token else None
        self.frames[frame] = {
            "definitions": {},
            "controls": [],
            "pending": None,
            "predicate": None,
            "function": function,
            "invocation": invocation,
            "argument_stack": [],
            "argument_heap_stack": [],
            "argument_link_stack": [],
        }
        native_caller = sys._getframe(1).f_back
        caller = (
            token["caller"]
            if token
            else {
                "function": native_caller.f_globals.get("__name__", "")
                + "."
                + native_caller.f_code.co_qualname,
                "file": native_caller.f_code.co_filename,
                "line": native_caller.f_lineno,
            }
        )
        call = {
            "frame": frame,
            "function": function,
            "caller": caller,
            "parent_frame": token["frame"] if token else None,
            "invocation": invocation,
            "phase": self.phase,
            "arguments": {n: self.snapshot(v) for n, v in arguments.items()},
            "return": None,
            "returned": False,
        }
        self.calls.append(call)
        if token:
            token["callee_frame"] = frame
        for name, value in arguments.items():
            links = {"argument": token["arguments"][name]} if token and name in token["arguments"] else {}
            self.emit(
                frame, "entry", "parameter", writes=[name], values={name: self.snapshot(value)}, links=links
            )
        call["event"] = self.emit(frame, "entry", "call", reads=self.frames[frame]["definitions"])
        return frame

    def begin(self, frame, site):
        super().begin(frame, site)
        if frame is not None and not self.limited:
            self.frames[frame]["pending"]["links"] = {}

    def read(self, frame, name, value):
        if frame is not None and self.frames[frame]["argument_stack"]:
            self.snapshot(value)
            state = self.frames[frame]
            producer = state["definitions"].get(name)
            if producer is None:
                self.issue("No observed argument definition for " + name)
            else:
                state["argument_stack"][-1][name] = producer
            return value
        return super().read(frame, name, value)

    def argument_begin(self, frame):
        if frame is not None:
            self.frames[frame]["argument_stack"].append({})
            self.frames[frame]["argument_heap_stack"].append(set())
            self.frames[frame]["argument_link_stack"].append({})

    def argument_value(self, frame, site, value):
        if frame is None:
            return value, None
        reads = self.frames[frame]["argument_stack"].pop()
        heap_reads = self.frames[frame]["argument_heap_stack"].pop()
        links = self.frames[frame]["argument_link_stack"].pop()
        ident = self.emit(frame, site, "argument", reads=reads, links=links, values={"$argument": self.snapshot(value)},
                          details={"heap_dependencies": sorted(heap_reads)} if heap_reads else None)
        return value, ident

    def method_target(self, frame, receiver, name):
        obj, event = receiver
        if frame is None:
            return MethodBoundary(getattr(obj, name), receiver)
        storage = instance_dict(obj)
        if name in storage:
            raise UnsupportedHeap("Instance-shadowed callable requires dynamic binding support")
        descriptor = next((type.__getattribute__(base,"__dict__")[name]
                           for base in type.__getattribute__(type(obj),"__mro__")
                           if name in type.__getattribute__(base,"__dict__")), None)
        if type(descriptor) is not types.FunctionType:
            raise UnsupportedHeap("Non-function descriptor call requires an effect model")
        return MethodBoundary(descriptor.__get__(obj,type(obj)), receiver)

    def call_target(self, frame, site, function, positional, keywords):
        receiver = None
        if type(function) is MethodBoundary:
            receiver = function.receiver
            function = function.function
        args = [pair[0] for pair in positional]
        kwargs = {name: pair[0] for name, pair in keywords.items()}
        if frame is None:
            return function(*args, **kwargs)
        implementation = function.__func__ if type(function) is types.MethodType else function
        selected = next(((name, code) for name, (fn, code) in self.targets.items() if fn is implementation), None)
        if selected is None and implementation is self.target:
            selected = (self.config["function"], self.target_code)
        if selected is None or implementation.__code__ is not selected[1]:
            self.issue("Traced callable binding changed at execution")
            return function(*args, **kwargs)
        callee = selected[0]
        record = self.config["functions"][callee]
        names, positional_names = record["parameters"], record["positional"]
        bound_positional = [receiver, *positional] if receiver is not None else positional
        bindings = {n: pair[1] for n, pair in zip(positional_names, bound_positional)}
        valid = len(bound_positional) <= len(positional_names)
        for name, pair in keywords.items():
            if name in bindings or name in record["posonly"] or name not in names:
                valid = False
            bindings[name] = pair[1]
        if not valid or set(bindings) != set(names):
            self.issue("All target parameters must be supplied explicitly without unpacking")
            return function(*args, **kwargs)
        ident = self.emit(
            frame, site, "invocation", details={"callee": callee, "arguments": bindings}
        )
        source = self.config["functions"][self.frames[frame]["function"]]
        token = {
            "event": ident,
            "callee": callee,
            "frame": frame,
            "arguments": bindings,
            "caller": {
                "function": self.frames[frame]["function"],
                "file": source["source_file"],
                "line": int(site.split(":", 1)[0]),
            },
        }
        self.invocations.append(token)
        try:
            value = function(*args, **kwargs)
        finally:
            self.invocations.pop()
        if "callee_frame" not in token:
            self.issue("No observed callee frame for invocation")
            return value
        call = self.calls[token["callee_frame"] - 1]
        if not call.get("returned"):
            self.issue("No observed return producer for invocation")
            return value
        bridge = self.emit(
            frame,
            site,
            "call_result",
            values={"$result": self.snapshot(value)},
            links={"invocation": ident, "return": call["return_event"]},
        )
        state = self.frames[frame]
        destination = state["argument_link_stack"][-1] if state["argument_link_stack"] else state["pending"]["links"]
        destination[f"result:{bridge}"] = bridge
        return value
