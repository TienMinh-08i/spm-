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

CONFIG = None
RUNTIME = None
ORIGINAL = None
TARGET = None
BASELINE = []
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


def pytest_configure(config):
    global CONFIG, RUNTIME
    CONFIG = json.loads(Path(os.environ["SPECMIND_SLICE_CONFIG"]).read_text(encoding="utf-8"))
    RUNTIME = Recorder(CONFIG)


def pytest_collection_modifyitems(session, config, items):
    global TARGET, ORIGINAL, SITES, PATCHED, HELPER_INSTALLED
    if len(items) != 1 or items[0].nodeid != CONFIG["test"]:
        ERRORS.append("Exactly the selected collected test is required")
        raise pytest.UsageError(ERRORS[-1])
    try:
        module = importlib.import_module(CONFIG["module"])
        if type(module) is not types.ModuleType:
            raise Unsupported("Customized module binding is not modeled")
        TARGET = module.__dict__.get(CONFIG["entry_point"])
        if type(TARGET) is not types.FunctionType or TARGET.__globals__ is not module.__dict__:
            raise Unsupported("A plain module-owned function binding is required")
        code = TARGET.__code__
        if (
            Path(code.co_filename).resolve() != Path(CONFIG["source_file"]).resolve()
            or code.co_firstlineno != CONFIG["line"]
            or code.co_freevars
        ):
            raise Unsupported("Runtime binding differs from selected top-level source function")
        if hashlib.sha256(Path(CONFIG["source_file"]).read_bytes()).hexdigest() != CONFIG["source_sha256"]:
            raise Unsupported("Source changed before execution")
        compiled = compile(CONFIG["source"], CONFIG["source_file"], "exec", dont_inherit=True)
        expected = next(
            (
                c
                for c in compiled.co_consts
                if isinstance(c, types.CodeType)
                and c.co_name == CONFIG["entry_point"]
                and c.co_firstlineno == CONFIG["line"]
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


def profile(frame, event, arg):
    global LIMITED, BASELINE_BYTES
    if not ACTIVE or frame.f_code is not ORIGINAL:
        return
    if event == "call":
        if len(BASELINE) >= CONFIG["max_events"]:
            LIMITED = True
            sys.setprofile(None)
            return
        try:
            values = {n: primitive(frame.f_locals[n]) for n in CONFIG["parameters"]}
        except ValueError as exc:
            ERRORS.append(str(exc))
            values = {"unmodeled": True}
        BASELINE_BYTES += len(json.dumps(values, ensure_ascii=True))
        if BASELINE_BYTES > CONFIG["max_bytes"]:
            LIMITED = True
            sys.setprofile(None)
            return
        call = {"arguments": values, "returned": False, "return": None, "phase": RUNTIME.phase}
        BASELINE.append(call)
        BASELINE_FRAMES[id(frame)] = call
    elif event == "return":
        call = BASELINE_FRAMES.pop(id(frame), None)
        if call is not None:
            try:
                call.update(returned=True)
                call["return"] = primitive(arg)
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
                marker = json.dumps({
                    "mode": payload["mode"], "exitcode": payload["exitcode"],
                    "missing_result": True, "output_budget_exceeded": True,
                }).encode("utf-8")
                stream.seek(0)
                stream.truncate()
                if len(marker) <= limit:
                    stream.write(marker)
                return
            stream.write(raw)
            used += len(raw)


def pytest_sessionfinish(session, exitstatus):
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
    write_bounded_result(CONFIG["result"], payload, 3 * CONFIG["max_bytes"])
