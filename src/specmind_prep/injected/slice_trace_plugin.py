"""One-test subprocess bridge; no mutation/evaluation/LLM imports."""
import importlib
import __future__
import hashlib
import types
import json
import os
from pathlib import Path
import sys

import pytest
from specmind_prep.injected.slice_instrumentation import HELPER, Unsupported, instrument
from specmind_prep.injected.slice_runtime import Recorder, primitive
from specmind_prep.injected.slice_calls import CallerRecorder, namespace_sites

CONFIG = None
RUNTIME = None
ORIGINAL = None
TARGET = None
BASELINE = []
BASELINE_ARGUMENTS = {}
from specmind_prep.injected.slice_heap_runtime import HeapCallerRecorder, EvidenceSnapshot
EVIDENCE = EvidenceSnapshot()
BASELINE_FRAMES = {}
BASELINE_BYTES = 0
SITES = {}
REPORTS = []
ERRORS = []
PREVIOUS_PROFILE = None
ACTIVE = False
LIMITED = False
PATCHED = False
HELPER_INSTALLED = False
ORIGINALS = {}
PATCHES = []
HELPER_NAMESPACES = []


def pytest_configure(config):
    global CONFIG, RUNTIME
    CONFIG = json.loads(Path(os.environ["SPECMIND_SLICE_CONFIG"]).read_text(encoding="utf-8"))
    RUNTIME = HeapCallerRecorder(CONFIG) if CONFIG.get("heap") else CallerRecorder(CONFIG) if CONFIG.get("functions") else Recorder(CONFIG)


def resolve_function(record):
    module = importlib.import_module(record["module"])
    if type(module) is not types.ModuleType:
        raise Unsupported("Customized module binding is not modeled")
    target = module.__dict__.get(record["entry_point"])
    if type(target) is not types.FunctionType or target.__globals__ is not module.__dict__:
        raise Unsupported("A plain module-owned function binding is required")
    code = target.__code__
    if (
        Path(code.co_filename).resolve() != Path(record["source_file"]).resolve()
        or code.co_firstlineno != record["line"]
        or code.co_freevars
    ):
        raise Unsupported("Runtime binding differs from selected top-level source function")
    if hashlib.sha256(Path(record["source_file"]).read_bytes()).hexdigest() != record["source_sha256"]:
        raise Unsupported("Source changed before execution")
    compiled = compile(record["source"], record["source_file"], "exec", dont_inherit=True)
    expected = next(
        (
            c
            for c in compiled.co_consts
            if isinstance(c, types.CodeType)
            and c.co_name == record["entry_point"]
            and c.co_firstlineno == record["line"]
        ),
        None,
    )
    fields = (
        "co_name",
        "co_qualname",
        "co_code",
        "co_consts",
        "co_names",
        "co_varnames",
        "co_argcount",
        "co_posonlyargcount",
        "co_kwonlyargcount",
        "co_flags",
    )
    if expected is None or any(getattr(code, k) != getattr(expected, k) for k in fields):
        raise Unsupported("Runtime function bytecode differs from the original source snapshot")
    return target


def pytest_collection_modifyitems(session, config, items):
    global TARGET, ORIGINAL, SITES, PATCHED, HELPER_INSTALLED
    if len(items) != 1 or items[0].nodeid != CONFIG["test"]:
        ERRORS.append("Exactly the selected collected test is required")
        raise pytest.UsageError(ERRORS[-1])
    try:
        if CONFIG.get("functions"):
            prepare_functions()
            return
        TARGET = resolve_function(CONFIG)
        code = TARGET.__code__
        ORIGINAL = code
        if CONFIG["mode"] == "instrumented":
            flags = 0
            for name in __future__.all_feature_names:
                flags |= ORIGINAL.co_flags & getattr(__future__, name).compiler_flag
            replacement, SITES = instrument(
                CONFIG["source"],
                CONFIG["line"],
                CONFIG["source_file"],
                TARGET.__globals__,
                future_flags=flags,
            )
            TARGET.__globals__[HELPER] = RUNTIME
            HELPER_INSTALLED = True
            TARGET.__code__ = replacement
            PATCHED = True
    except Exception as exc:
        ERRORS.append(type(exc).__name__ + ": " + str(exc))
        raise pytest.UsageError(ERRORS[-1]) from exc


def prepare_functions():
    global SITES
    selected = {name: resolve_function(record) for name, record in CONFIG["functions"].items()}
    target = selected[CONFIG["function"]]
    replacements = {}
    for name, function in selected.items():
        record = CONFIG["functions"][name]
        ORIGINALS[function.__code__] = (name, record)
        for alias in record["call_names"]:
            if function.__globals__.get(alias) is not target:
                raise Unsupported("Selected call name does not resolve to the target: " + alias)
        if CONFIG["mode"] == "instrumented":
            flags = 0
            for feature in __future__.all_feature_names:
                flags |= function.__code__.co_flags & getattr(__future__, feature).compiler_flag
            replacement, sites = instrument(
                record["source"],
                record["line"],
                record["source_file"],
                function.__globals__,
                future_flags=flags,
                call_names=record["call_names"],
                function_id=name,
                heap=bool(CONFIG.get("heap")),
            )
            replacements[name] = replacement
            SITES.update(namespace_sites(name, sites, record["repo_path"]))
    if CONFIG["mode"] != "instrumented":
        return
    # Validate and compile everything before adding helpers or patching any function.
    for name, function in selected.items():
        namespace = function.__globals__
        if not any(namespace is previous for previous in HELPER_NAMESPACES):
            namespace[HELPER] = RUNTIME
            HELPER_NAMESPACES.append(namespace)
        PATCHES.append((function, function.__code__))
        function.__code__ = replacements[name]
    RUNTIME.target = target
    RUNTIME.target_code = replacements[CONFIG["function"]]


def profile(frame, event, arg):
    global LIMITED, BASELINE_BYTES
    if not ACTIVE:
        return
    if CONFIG.get("functions"):
        selected = ORIGINALS.get(frame.f_code)
        if selected is None:
            return
        function_id, record = selected
    else:
        if frame.f_code is not ORIGINAL:
            return
        record = CONFIG
    if event == "call":
        if len(BASELINE) >= CONFIG["max_events"]:
            LIMITED = True
            sys.setprofile(None)
            return
        try:
            values = {n: (EVIDENCE.snapshot(frame.f_locals[n]) if CONFIG.get("heap") else primitive(frame.f_locals[n])) for n in record["parameters"]}
        except ValueError as exc:
            ERRORS.append(str(exc))
            values = {"unmodeled": True}
        BASELINE_BYTES += len(json.dumps(values, ensure_ascii=True))
        if BASELINE_BYTES > CONFIG["max_bytes"]:
            LIMITED = True
            sys.setprofile(None)
            return
        call = {"arguments": values, "returned": False, "return": None, "phase": RUNTIME.phase}
        if CONFIG.get("functions"):
            call["function"] = function_id
        BASELINE.append(call)
        BASELINE_FRAMES[id(frame)] = call
        if CONFIG.get("heap"):BASELINE_ARGUMENTS[id(frame)]={n:frame.f_locals[n] for n in record["parameters"]}
    elif event == "return":
        call = BASELINE_FRAMES.pop(id(frame), None)
        if call is not None:
            try:
                call.update(returned=True)
                call["return"] = EVIDENCE.snapshot(arg) if CONFIG.get("heap") else primitive(arg)
                if CONFIG.get("heap"):
                    call["arguments_after"]={k:EVIDENCE.snapshot(v) for k,v in BASELINE_ARGUMENTS.pop(id(frame)).items()}
                BASELINE_BYTES += len(json.dumps(call["return"], ensure_ascii=True))
                if BASELINE_BYTES > CONFIG["max_bytes"]:
                    LIMITED = True
                    sys.setprofile(None)
            except ValueError as exc:
                ERRORS.append(str(exc))
                call["return"] = {"type": "unmodeled"}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global ACTIVE, PREVIOUS_PROFILE
    if ERRORS:
        yield
        return
    ACTIVE = True
    RUNTIME.active = True
    if CONFIG["mode"] == "baseline":
        PREVIOUS_PROFILE = sys.getprofile()
        if PREVIOUS_PROFILE is not None:
            ERRORS.append("Existing profiler is unsupported")
        else:
            sys.setprofile(profile)
    try:
        yield
    finally:
        ACTIVE = False
        RUNTIME.active = False
        if CONFIG["mode"] == "baseline" and PREVIOUS_PROFILE is None:
            sys.setprofile(None)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    RUNTIME.phase = "setup"
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    RUNTIME.phase = "call"
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    RUNTIME.phase = "teardown"
    yield


def pytest_runtest_logreport(report):
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    REPORTS.append(
        {
            "nodeid": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "stdout": report.capstdout,
            "stderr": report.capstderr,
            "crash": {"file": str(crash.path), "line": crash.lineno, "message": crash.message}
            if crash
            else None,
        }
    )


def write_bounded_result(path, payload, limit):
    """Stream JSON without first constructing a second full payload string."""
    with Path(path).open("xb") as stream:
        used = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(payload):
            raw = chunk.encode("utf-8")
            if used + len(raw) > limit:
                marker = json.dumps(
                    {
                        "mode": payload["mode"],
                        "exitcode": payload["exitcode"],
                        "missing_result": True,
                        "output_budget_exceeded": True,
                    }
                ).encode("utf-8")
                stream.seek(0)
                stream.truncate()
                if len(marker) <= limit:
                    stream.write(marker)
                return
            stream.write(raw)
            used += len(raw)


def pytest_sessionfinish(session, exitstatus):
    for function, original in reversed(PATCHES):
        function.__code__ = original
    for namespace in HELPER_NAMESPACES:
        if namespace.get(HELPER) is RUNTIME:
            namespace.pop(HELPER)
    if PATCHED:
        TARGET.__code__ = ORIGINAL
    if HELPER_INSTALLED and TARGET.__globals__.get(HELPER) is RUNTIME:
        TARGET.__globals__.pop(HELPER)
    baseline = BASELINE
    payload = RUNTIME.result() if RUNTIME is not None else {"events": [], "calls": [], "issues": []}
    payload.update(
        mode=CONFIG["mode"],
        baseline_calls=baseline,
        reports=REPORTS,
        errors=ERRORS,
        exitcode=int(exitstatus),
        sites=SITES,
        baseline_limited=LIMITED,
    )
    if CONFIG.get("functions"):
        payload["functions"] = list(CONFIG["functions"])
    write_bounded_result(CONFIG["result"], payload, 3 * CONFIG["max_bytes"])
