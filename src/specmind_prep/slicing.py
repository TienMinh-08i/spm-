"""Occurrence-based backward dynamic slicing (Agrawal-Horgan Approach 3).

Initial model: one synchronous top-level function, primitive local variables,
structured if/while and top-level return guards, declared builtin I/O boundaries.
No heap, interprocedural effects, general CFG, forward query or Reduced DDG.
"""
from collections import Counter
from pathlib import Path
import os
import hashlib
import math
import ast
import subprocess
import sys

from specmind_eval.common.fingerprint import (
    digest,
    file_hash,
    implementation_digest,
    source_environment,
    require_source_environment,
)
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.storage import read, write
from specmind_prep.source_index import source_index
from specmind_prep.gather.gather import revision
from specmind_prep.project import validate_project
from specmind_prep.injected.slice_instrumentation import Unsupported, analyze, PURE_CALLS
from specmind_prep.injected.slice_calls import namespace_sites
from specmind_prep.slice_process import run_bounded

SCHEMA = "dynamic-slice-trace-v1"
MODEL = "primitive-local-guard-ddg-v2"
READABLE_MODELS = {"primitive-local-structured-ddg-v1", MODEL}
CALL_MODEL = "primitive-call-boundary-ddg-v3"
READABLE_MODELS.add(CALL_MODEL)
PLUGIN = "specmind_prep.injected.slice_trace_plugin"


def build_ddg(events, *, interprocedural=False):
    """Validate causal producer edges and attach dependencies to occurrence nodes."""
    nodes = []
    definitions = {}
    known = {}
    counts = Counter()
    origins, results, used_arguments, invoked_frames = {}, set(), set(), {}
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
        } | ({"argument", "invocation", "call_result"} if interprocedural else set()):
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
        local = set(event["reads"].values()) | set(event["inputs"]) | set(event["controls"])
        dependencies = set(local)
        for parent in dependencies:
            if type(parent) is not int or parent not in known or known[parent]["frame"] != frame:
                raise ValueError("Dependency must reference an earlier event in the same modeled frame")
        if any(known[c]["kind"] != "predicate" for c in event["controls"]):
            raise ValueError("Control edge must reference a predicate occurrence")
        if any(known[c]["kind"] != "input" for c in event["inputs"]):
            raise ValueError("Input edge must reference an input occurrence")
        if interprocedural:
            _call_dependencies(event, known, origins, results, used_arguments, invoked_frames)
            dependencies.update(event["links"].values())
        elif event.get("links"):
            raise ValueError("Call links require the interprocedural model")
        counts[(frame, event["site"])] += 1
        node = dict(event, occurrence=counts[(frame, event["site"])], depends_on=sorted(dependencies))
        known[ident] = node
        nodes.append(node)
        for name in event["writes"]:
            state[name] = ident
    return nodes


def _call_dependencies(event, known, origins, results, used_arguments, invoked_frames):
    """Validate explicit call-boundary links; never infer provenance by value."""
    frame, kind, links = event["frame"], event["kind"], event["links"]
    if not isinstance(links, dict) or any(
        not isinstance(role, str)
        or (role not in {"invocation", "argument", "return"} and not role.startswith("result:"))
        for role in links
    ):
        raise ValueError("Invalid call dependency roles")
    if not isinstance(event.get("function"), str) or not event["function"]:
        raise ValueError("Call model requires function identities")
    for role, parent in links.items():
        if type(parent) is not int or parent not in known:
            raise ValueError("Call link must reference an earlier occurrence")
        expected = (
            "call_result"
            if role.startswith("result:")
            else {"invocation": "invocation", "argument": "argument", "return": "return"}[role]
        )
        if known[parent]["kind"] != expected:
            raise ValueError("Call dependency kind differs from its role")
    if kind == "invocation":
        arguments = event.get("arguments")
        if not isinstance(arguments, dict) or not isinstance(event.get("callee"), str):
            raise ValueError("Invocation requires explicit argument bindings")
        for name, parent in arguments.items():
            if not isinstance(name, str) or type(parent) is not int or parent not in known:
                raise ValueError("Invalid argument occurrence")
            if known[parent]["kind"] != "argument" or known[parent]["frame"] != frame:
                raise ValueError("Invocation argument belongs to another frame")
            if parent in used_arguments or not known[parent]["site"].startswith(event["site"] + ":arg:"):
                raise ValueError("Argument belongs to a different invocation occurrence")
        if len(set(arguments.values())) != len(arguments):
            raise ValueError("Argument occurrences cannot be shared across parameter bindings")
        used_arguments.update(arguments.values())
    invocation = links.get("invocation")
    external = invocation is not None and known[invocation]["frame"] != frame
    if external:
        if known[invocation]["callee"] != event["function"]:
            raise ValueError("Invocation callee identity differs from frame")
        if invocation in invoked_frames and invoked_frames[invocation] != frame:
            raise ValueError("Invocation belongs to a different callee frame")
        invoked_frames[invocation] = frame
    elif invocation is not None and kind != "call_result":
        raise ValueError("Only a return bridge can consume a same-frame invocation")
    origin = invocation if external else None
    if frame in origins and origins[frame] != origin:
        raise ValueError("Missing or changed frame invocation boundary")
    origins[frame] = origin
    if "argument" in links:
        if kind != "parameter" or not external or len(event["writes"]) != 1:
            raise ValueError("Argument link requires a callee parameter")
        if known[invocation]["arguments"].get(event["writes"][0]) != links["argument"]:
            raise ValueError("Parameter is linked to the wrong actual argument")
    elif kind == "parameter" and external:
        raise ValueError("Callee parameter is missing its argument source")
    if "return" in links:
        returned = known[links["return"]]
        if kind != "call_result" or invocation is None or known[invocation]["frame"] != frame:
            raise ValueError("Return bridge requires its caller invocation")
        if returned["links"].get("invocation") != invocation or invocation in results:
            raise ValueError("Return belongs to another or already consumed invocation")
        results.add(invocation)
    elif kind == "call_result":
        raise ValueError("Call result is missing its return source")
    for role, parent in links.items():
        if role.startswith("result:") and (role != f"result:{parent}" or known[parent]["frame"] != frame):
            raise ValueError("Call result must be consumed in its caller frame")


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


def _snapshot_calls(payload):
    source = payload["baseline_calls"] if payload["mode"] == "baseline" else payload["calls"]
    fields = ("arguments", "returned", "return", "phase") + (
        ("function",) if payload.get("functions") else ()
    )
    return [{k: c[k] for k in fields} for c in source]


def _call_specs(repo, records, caller):
    """Pure source preparation, also used to verify a sealed multi-function map."""
    specs, sites = {}, {}
    for name, record in records.items():
        if record.get("parent_name") or record.get("async"):
            raise Unsupported("Selected caller and target must be top-level synchronous functions")
        path = (repo / record["repo_path"]).resolve()
        if not path.is_relative_to(repo) or path.stat().st_size > 2_000_000:
            raise Unsupported("Invalid or oversized selected source")
        source = path.read_bytes().decode("utf-8-sig")
        nodes = [
            n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.lineno == record["line"]
        ]
        if (
            len(nodes) != 1
            or nodes[0].name != record["entry_point"]
            or name != record["module"] + "." + record["entry_point"]
        ):
            raise Unsupported("Selected function identity differs from its source")
        call_names = (
            sorted(
                {
                    n.func.id
                    for n in ast.walk(nodes[0])
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id not in PURE_CALLS | {"input", "print"}
                }
            )
            if name == caller
            else []
        )
        fn, _, parameters, mapping, _ = analyze(source, record["line"], str(path), call_names=call_names)
        specs[name] = {
            "source": source,
            "source_file": str(path),
            "repo_path": record["repo_path"],
            "source_sha256": file_hash(path),
            "module": record["module"],
            "line": record["line"],
            "entry_point": record["entry_point"],
            "parameters": parameters,
            "call_names": call_names,
            "positional": [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)],
            "posonly": [a.arg for a in fn.args.posonlyargs],
        }
        sites.update(namespace_sites(name, mapping, record["repo_path"]))
    return specs, sites


def _execute(config, output, mode, timeout, repo, project):
    path = output / (mode + ".config.json")
    config = dict(config, mode=mode, result=str(output / (mode + ".json")))
    write(path, config)
    src = str(Path(__file__).resolve().parents[1])
    env = dict(
        os.environ,
        SPECMIND_SLICE_CONFIG=str(path),
        PYTHONUTF8="1",
        PYTHONNOUSERSITE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONPATH=os.pathsep.join([src, *[str(repo / p) for p in project["python_path"]]]),
    )
    env.pop("PYTEST_ADDOPTS", None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-p",
        PLUGIN,
        "--rootdir",
        str(repo),
        config["test"],
    ]
    log_path = output / (mode + ".log")
    execution = run_bounded(
        command,
        cwd=repo,
        env=env,
        log_path=log_path,
        result_path=config["result"],
        timeout=timeout,
        max_bytes=config["max_bytes"],
    )
    if execution.get("timeout"):
        return dict(execution, mode=mode)
    if execution.get("output_budget_exceeded") or execution.get("output_read_error"):
        return dict(execution, missing_result=True, mode=mode)
    if Path(config["result"]).is_file() and Path(config["result"]).stat().st_size > 3 * config["max_bytes"]:
        return {"missing_result": True, "mode": mode, "output_budget_exceeded": True}
    try:
        payload = read(Path(config["result"]))
    except (ValueError, UnicodeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("exitcode") != execution["exitcode"]:
        return {"missing_result": True, "mode": mode, "exitcode": execution["exitcode"]}
    return payload


def trace_function(repo, project, test, target, output, *, timeout=60, max_events=20000, caller=None):
    repo = Path(repo).resolve()
    project = validate_project(project, repo)
    output = separate_output(output, repo)
    if type(max_events) is not int or not 1 <= max_events <= 200000:
        raise ValueError("max_events must be between 1 and 200000")
    if type(timeout) not in (int, float):
        raise ValueError("A positive finite timeout is required")
    try:
        timeout = float(timeout)
    except OverflowError as exc:
        raise ValueError("A positive finite timeout is required") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("A positive finite timeout is required")
    if not isinstance(test, str) or "::" not in test or test.startswith("-"):
        raise ValueError("An exact pytest nodeid is required")
    test_file = (repo / test.split("::", 1)[0]).resolve()
    test_roots = [(repo / p).resolve() for p in project["tests"]]
    if (
        not test_file.is_file()
        or not test_file.is_relative_to(repo)
        or not any(test_file == p or (p.is_dir() and test_file.is_relative_to(p)) for p in test_roots)
    ):
        raise ValueError("Selected test must be within configured test roots")
    git_root = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30
    )
    commit = (
        revision(repo)
        if git_root.returncode == 0 and Path(git_root.stdout.strip()).resolve() == repo
        else None
    )
    if project.get("commit") and commit != project["commit"]:
        raise ValueError("Source commit mismatch")
    test = test_file.relative_to(repo).as_posix() + "::" + test.split("::", 1)[1]
    index, _ = source_index(repo, project)
    if target not in index:
        raise ValueError("Unknown function: " + target)
    if caller is not None and (caller not in index or caller == target):
        raise ValueError("Select a distinct known caller function")
    record = index[target]
    path = repo / record["repo_path"]
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError("Source file exceeds initial trace budget")
    source = raw.decode("utf-8-sig")
    if output.exists():
        raise ValueError("Output exists; choose a new trace directory")
    output.mkdir(parents=True)
    environment = source_environment(repo)
    manifest = {
        "schema": SCHEMA,
        "complete": False,
        "model": CALL_MODEL if caller else MODEL,
        "repo_commit": commit,
        "project": project,
        "repo": str(repo),
        "source_environment": environment,
        "function": target,
        "test": test,
        "function_location": {"file": record["repo_path"], "line": record["line"]},
        "source_files": {
            record["repo_path"]: hashlib.sha256(raw).hexdigest(),
            test_file.relative_to(repo).as_posix(): file_hash(test_file),
        },
        "implementation": implementation_digest("specmind_prep.slicing", PLUGIN),
        "limits": {"timeout_per_execution": timeout, "max_events": max_events, "max_bytes": 8_000_000},
        "scope": {
            "kind": "intraprocedural",
            "inputs": "external primitive argument/input() origins",
            "control": "structured if/while with normalized top-level return guards",
            "heap": False,
            "forward": False,
            "reduced_ddg": False,
        },
    }
    if caller:
        selected = {
            name: {
                k: index[name].get(k)
                for k in ("repo_path", "line", "module", "entry_point", "parent_name", "async")
            }
            for name in (caller, target)
        }
        manifest.update(caller=caller, functions=selected)
        manifest["source_files"].update(
            {r["repo_path"]: file_hash(repo / r["repo_path"]) for r in selected.values()}
        )
        manifest["scope"].update(
            kind="selected-caller-target",
            functions=[caller, target],
            inputs="external selected-caller arguments; explicit actual/formal and return links",
        )
    write(output / "manifest.json", manifest)
    status, issues, events, calls, sites = "unsupported", [], [], [], {}
    try:
        if record.get("parent_name") or record.get("async"):
            raise Unsupported("Methods and async functions require a later model")
        functions = None
        if caller:
            functions, sites = _call_specs(repo, selected, caller)
            parameters = functions[target]["parameters"]
        else:
            _, _, parameters, sites, _ = analyze(source, record["line"], str(path))
        config = {
            "source": source,
            "source_file": str(path),
            "line": record["line"],
            "module": record["module"],
            "entry_point": record["entry_point"],
            "function": target,
            "test": test,
            "parameters": parameters,
            "source_sha256": manifest["source_files"][record["repo_path"]],
            "max_events": max_events,
            "max_bytes": 8_000_000,
        }
        if caller:
            config.update(functions=functions, caller=caller)
        baseline = _execute(config, output, "baseline", timeout, repo, project)
        if baseline.get("timeout") or baseline.get("missing_result"):
            status = "incomplete"
            issues = [
                "Baseline output budget exceeded"
                if baseline.get("output_budget_exceeded")
                else "Baseline execution timed out or did not finalize"
            ]
        elif baseline.get("baseline_limited"):
            status = "incomplete"
            issues = ["Baseline call budget exceeded"]
        elif baseline.get("errors"):
            status = "unsupported"
            issues = baseline["errors"]
        elif baseline.get("exitcode") not in {0, 1}:
            status = "incomplete"
            issues = ["Baseline pytest execution did not finish normally"]
        else:
            instrumented = _execute(config, output, "instrumented", timeout, repo, project)
            if (
                instrumented.get("timeout")
                or instrumented.get("missing_result")
                or instrumented.get("limited")
            ):
                status = "incomplete"
                issues = [
                    "Instrumented output budget exceeded"
                    if instrumented.get("output_budget_exceeded")
                    else "Instrumented execution incomplete or event budget exceeded"
                ]
            elif instrumented.get("errors") or instrumented.get("issues"):
                status = "unsupported"
                issues = instrumented.get("errors", []) + instrumented.get("issues", [])
            elif instrumented.get("exitcode") not in {0, 1}:
                status = "incomplete"
                issues = ["Instrumented pytest execution did not finish normally"]
            elif (
                baseline["exitcode"] != instrumented["exitcode"]
                or baseline["reports"] != instrumented["reports"]
                or _snapshot_calls(baseline) != _snapshot_calls(instrumented)
            ):
                status = "instrumentation_changed_behavior"
                issues = ["Observed calls, returns, output or pytest outcomes differ"]
            elif not any(c.get("function", target) == target for c in instrumented["calls"]):
                status = "criterion_not_reached"
                issues = ["Selected test did not call the function"]
            else:
                status = "complete_supported"
            events = instrumented.get("events", [])
            calls = instrumented.get("calls", [])
            sites = instrumented.get("sites", sites)
        write(
            output / "execution.json",
            {
                "baseline_exitcode": baseline.get("exitcode"),
                "instrumented_exitcode": instrumented.get("exitcode") if "instrumented" in locals() else None,
                "behavior_check": "pass"
                if status in {"complete_supported", "criterion_not_reached"}
                else "not_verified",
            },
        )
    except Unsupported as exc:
        issues = [str(exc)]
        status = "unsupported"
    if source_environment(repo) != environment:
        status = "incomplete"
        issues.append("Source/environment changed during tracing")
    if any(file_hash(repo / name) != h for name, h in manifest["source_files"].items()):
        status = "incomplete"
        issues.append("Selected source/test changed during tracing")
    graph = build_ddg(events, interprocedural=bool(caller)) if status == "complete_supported" else []
    # Source spans are generated by the same source AST, not from executed source rewrites.
    if not caller:
        for site in sites.values():
            site["file"] = record["repo_path"]
    write(output / "events.json", events)
    write(output / "ddg.json", graph)
    write(output / "calls.json", calls)
    write(output / "sites.json", sites)
    summary = {
        "status": status,
        "issues": issues,
        "function": target,
        "test": test,
        "calls": len(calls),
        "events": len(events),
        "model": manifest["model"],
        "output": str(output),
    }
    write(output / "summary.json", summary)
    names = ["events.json", "ddg.json", "calls.json", "sites.json", "summary.json"]
    names.extend(
        p.name for p in sorted(output.glob("*.json")) if p.name not in names and p.name != "manifest.json"
    )
    names.extend(p.name for p in sorted(output.glob("*.log")))
    manifest.update(complete=True, trace_status=status, files={n: file_hash(output / n) for n in names})
    manifest["fingerprint"] = digest({k: v for k, v in manifest.items() if k != "fingerprint"})
    write(output / "manifest.json", manifest)
    return summary


def load_trace(directory, repo):
    directory, repo = Path(directory).resolve(), Path(repo).resolve()
    manifest = read(directory / "manifest.json")
    if not manifest or manifest.get("schema") != SCHEMA or not manifest.get("complete"):
        raise ValueError("Missing or unfinished dynamic trace artifact")
    if manifest.get("model") not in READABLE_MODELS:
        raise ValueError("Unsupported dynamic trace model")
    files = manifest.get("files")
    required = {"events.json", "ddg.json", "calls.json", "sites.json", "summary.json"}
    if manifest.get("trace_status") == "complete_supported":
        required |= {
            "execution.json",
            "baseline.json",
            "instrumented.json",
            "baseline.config.json",
            "instrumented.config.json",
            "baseline.log",
            "instrumented.log",
        }
    if not isinstance(files, dict) or not required <= files.keys():
        raise ValueError("Trace manifest missing required hashes")
    for name, h in files.items():
        if Path(name).name != name or not (directory / name).is_file() or file_hash(directory / name) != h:
            raise ValueError("Dynamic trace artifact mismatch: " + name)
    if manifest.get("fingerprint") != digest({k: v for k, v in manifest.items() if k != "fingerprint"}):
        raise ValueError("Dynamic trace manifest fingerprint mismatch")
    require_source_environment(manifest["source_environment"], source_environment(repo), "Dynamic trace")
    if manifest.get("repo_commit") and revision(repo) != manifest["repo_commit"]:
        raise ValueError("Dynamic trace checkout commit mismatch")
    for name, h in manifest["source_files"].items():
        p = (repo / name).resolve()
        if not p.is_relative_to(repo) or not p.is_file() or file_hash(p) != h:
            raise ValueError("Dynamic trace/source mismatch")
    location = manifest["function_location"]
    selected = (repo / location["file"]).resolve()
    if not selected.is_relative_to(repo) or location["file"] not in manifest["source_files"]:
        raise ValueError("Invalid trace source location")
    sites = read(directory / "sites.json")
    if manifest["trace_status"] == "complete_supported":
        if manifest["model"] == CALL_MODEL:
            records = manifest.get("functions", {})
            if set(records) != {manifest.get("caller"), manifest["function"]}:
                raise ValueError("Invalid selected caller/target set")
            if any(r["repo_path"] not in manifest["source_files"] for r in records.values()):
                raise ValueError("Selected function source is missing from trace hashes")
            target = records[manifest["function"]]
            if target["repo_path"] != location["file"] or target["line"] != location["line"]:
                raise ValueError("Target location differs from the selected function set")
            expected_functions, expected_sites = _call_specs(repo, records, manifest["caller"])
            for mode in ("baseline", "instrumented"):
                config = read(directory / (mode + ".config.json"))
                if (
                    config.get("functions") != expected_functions
                    or config.get("caller") != manifest["caller"]
                ):
                    raise ValueError("Call configuration differs from selected source functions")
        else:
            _, _, _, expected_sites, _ = analyze(
                selected.read_bytes().decode("utf-8-sig"), location["line"], str(selected)
            )
            for site in expected_sites.values():
                site["file"] = location["file"]
        if sites != expected_sites:
            raise ValueError("Trace source map differs from original source AST")
    nodes = read(directory / "ddg.json")
    if manifest["trace_status"] == "complete_supported":
        original = read(directory / "baseline.json")
        observed = read(directory / "instrumented.json")
        if (
            original["mode"] != "baseline"
            or observed["mode"] != "instrumented"
            or original["exitcode"] not in {0, 1}
            or original["exitcode"] != observed["exitcode"]
            or original["reports"] != observed["reports"]
            or _snapshot_calls(original) != _snapshot_calls(observed)
            or original.get("errors")
            or original.get("baseline_limited")
            or observed.get("errors")
            or observed.get("issues")
            or observed.get("limited")
        ):
            raise ValueError("Trace behavior-verification evidence is invalid")
        events = read(directory / "events.json")
        if events != observed["events"] or read(directory / "calls.json") != observed["calls"]:
            raise ValueError("Trace data differs from subprocess evidence")
        if nodes != build_ddg(events, interprocedural=manifest["model"] == CALL_MODEL):
            raise ValueError("DDG differs from recorded execution")
        frames = {c["frame"] for c in observed["calls"]}
        if not frames or frames != {n["frame"] for n in nodes}:
            raise ValueError("DDG invocation/frame evidence mismatch")
        if manifest["model"] == CALL_MODEL:
            if (
                len(frames) != len(observed["calls"])
                or original.get("functions") != list(records)
                or observed.get("functions") != list(records)
            ):
                raise ValueError("Selected function/invocation evidence mismatch")
            for call in observed["calls"]:
                if {n["function"] for n in nodes if n["frame"] == call["frame"]} != {call["function"]}:
                    raise ValueError("Frame function identity differs from recorded calls")
    return nodes, read(directory / "sites.json"), manifest


def query_slice(trace, repo, event, variable, *, when="before"):
    nodes, sites, manifest = load_trace(trace, repo)
    result = {
        "schema": "dynamic-backward-slice-v1",
        "method": "Agrawal-Horgan Approach 3 DDG",
        "trace_fingerprint": manifest["fingerprint"],
        "scope": manifest["scope"],
        "direction": "backward",
    }
    if manifest["trace_status"] != "complete_supported":
        return dict(
            result,
            status=manifest["trace_status"],
            nodes=None,
            issues=read(Path(trace) / "summary.json")["issues"],
        )
    sliced = backward_slice(nodes, event, variable, when)
    statements = {
        sites[n["site"]]["statement"]: sites[sites[n["site"]]["statement"]] for n in sliced["nodes"]
    }
    return dict(
        result,
        status="complete_supported",
        criterion={k: v for k, v in sliced.items() if k != "nodes"},
        nodes=sliced["nodes"],
        source_statements=sorted(statements.values(), key=lambda s: (s["file"], s["line"], s["column"])),
        limitations=[
            "Selected caller/target primitive-local model; caller inputs are external boundaries."
            if manifest["model"] == CALL_MODEL
            else "Single function and primitive-local model; caller inputs are external boundaries.",
            "Not an independently executable program or whole-Python semantic slice.",
        ],
    )
