"""Validated read-only occurrence DDG."""
from collections import Counter

def build_ddg(events):
    known, definitions, frames, owners, returned, used_args = {}, {}, {}, {}, set(), set()
    counts = Counter()
    nodes = []
    kinds = {
        "external_input",
        "parameter",
        "call",
        "global",
        "expression",
        "predicate",
        "argument",
        "invocation",
        "call_result",
        "operation",
        "return",
    }
    for event in events:
        ident, frame, function = event["id"], event["frame"], event["function"]
        if (
            type(ident) is not int
            or ident != len(nodes) + 1
            or type(frame) is not int
            or frame < 1
            or event["kind"] not in kinds
        ):
            raise ValueError("Invalid read-only event identity/kind")
        state = definitions.setdefault(frame, {})
        for name, parent in event["reads"].items():
            if state.get(name) != parent:
                raise ValueError("Read-only read has stale parameter source")
        local = set(event["data"]) | set(event["reads"].values()) | set(event["controls"])
        for parent in local:
            if type(parent) is not int or parent not in known or known[parent]["frame"] != frame:
                raise ValueError("Read-only local edges must be causal and same-frame")
        if any(known[p]["kind"] != "predicate" for p in event["controls"]):
            raise ValueError("Invalid expression control edge")
        links = event["links"]
        if not isinstance(links, dict) or not set(links) <= {
            "frame_invocation",
            "argument",
            "result_invocation",
            "return",
        }:
            raise ValueError("Invalid read-only call roles")
        for role, parent in links.items():
            expected = {
                "frame_invocation": "invocation",
                "argument": "argument",
                "result_invocation": "invocation",
                "return": "return",
            }[role]
            if type(parent) is not int or parent not in known or known[parent]["kind"] != expected:
                raise ValueError("Invalid read-only call producer")
        origin = links.get("frame_invocation")
        if origin is not None:
            invocation = known[origin]
            if (
                invocation["frame"] == frame
                or invocation["callee"] != function
                or owners.setdefault(origin, frame) != frame
            ):
                raise ValueError("Invalid callee-frame invocation")
        if frames.setdefault(frame, (function, origin)) != (function, origin):
            raise ValueError("Changed frame boundary")
        if event["kind"] == "invocation":
            for name, parent in event["arguments"].items():
                if (
                    parent not in known
                    or known[parent]["frame"] != frame
                    or known[parent]["kind"] != "argument"
                    or parent in used_args
                ):
                    raise ValueError("Argument reused or from another invocation")
                used_args.add(parent)
        if event["kind"] == "parameter" and origin is not None:
            if len(event["writes"]) != 1 or known[origin]["arguments"].get(event["writes"][0]) != links.get(
                "argument"
            ):
                raise ValueError("Wrong actual/formal source")
        elif "argument" in links:
            raise ValueError("Argument link requires a callee parameter")
        if event["kind"] == "call_result":
            call = links.get("result_invocation")
            ret = links.get("return")
            if (
                call is None
                or ret is None
                or known[call]["frame"] != frame
                or known[ret]["links"].get("frame_invocation") != call
                or call in returned
            ):
                raise ValueError("Wrong/reused nested return producer")
            returned.add(call)
        elif "result_invocation" in links or "return" in links:
            raise ValueError("Return links require a call result")
        counts[(frame, event["site"])] += 1
        node = dict(
            event, depends_on=sorted(local | set(links.values())), occurrence=counts[(frame, event["site"])]
        )
        known[ident] = node
        nodes.append(node)
        if len(set(event["writes"])) != len(event["writes"]):
            raise ValueError("Duplicate write")
        for name in event["writes"]:
            state[name] = ident
    return nodes
