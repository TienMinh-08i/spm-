"""Occurrence-based DDG validation and backward variable queries."""
from collections import Counter

def build_ddg(events):
    """Validate causal producer edges and attach dependencies to occurrence nodes."""
    nodes = []
    definitions = {}
    known = {}
    counts = Counter()
    for event in events:
        ident = event["id"]
        if type(ident) is not int or ident != len(nodes) + 1:
            raise ValueError("Events require consecutive positive IDs")
        if event["kind"] not in {
            "parameter",
            "input",
            "assignment",
            "predicate",
            "return",
            "expression",
            "call",
        }:
            raise ValueError("Unknown DDG event kind")
        frame = event["frame"]
        if type(frame) is not int or frame < 1:
            raise ValueError("Invalid frame ID")
        state = definitions.setdefault(frame, {})
        if len(event["writes"]) != len(set(event["writes"])):
            raise ValueError("Duplicate write location")
        for name, producer in event["reads"].items():
            if state.get(name) != producer:
                raise ValueError("Read is not linked to the most recent local definition")
        dependencies = set(event["reads"].values()) | set(event["inputs"]) | set(event["controls"])
        for parent in dependencies:
            if type(parent) is not int or parent not in known or known[parent]["frame"] != frame:
                raise ValueError("Dependency must reference an earlier event in the same modeled frame")
        if any(known[c]["kind"] != "predicate" for c in event["controls"]):
            raise ValueError("Control edge must reference a predicate occurrence")
        if any(known[c]["kind"] != "input" for c in event["inputs"]):
            raise ValueError("Input edge must reference an input occurrence")
        counts[(frame, event["site"])] += 1
        node = dict(event, occurrence=counts[(frame, event["site"])], depends_on=sorted(dependencies))
        known[ident] = node
        nodes.append(node)
        for name in event["writes"]:
            state[name] = ident
    return nodes


def backward_slice(nodes, event_id, variable, when="before"):
    """Slice a particular variable value at a particular execution occurrence."""
    if when not in {"before", "after"} or not isinstance(variable, str) or not variable:
        raise ValueError("Specify a variable and before/after observation")
    by_id = {n["id"]: n for n in nodes}
    if type(event_id) is not int or event_id not in by_id:
        raise ValueError("Unknown event ID; inspect events.jsonl and calls.json")
    selected = by_id[event_id]
    definitions = {}
    for node in nodes:
        if node["id"] > event_id or (node["id"] == event_id and when == "before"):
            break
        if node["frame"] == selected["frame"]:
            definitions.update({name: node["id"] for name in node["writes"]})
    if variable not in definitions:
        raise ValueError("No observed definition for this variable at the selected occurrence")
    root = definitions[variable]
    visited = set()
    pending = [root]
    while pending:
        ident = pending.pop()
        if ident not in visited:
            visited.add(ident)
            pending.extend(by_id[ident]["depends_on"])
    return {
        "event": event_id,
        "frame": selected["frame"],
        "variable": variable,
        "when": when,
        "root": root,
        "nodes": [by_id[i] for i in sorted(visited)],
    }
