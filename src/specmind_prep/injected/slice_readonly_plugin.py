"""Exact-test bridge for the read-only expression DDG, with native baseline."""
import abc
import __future__
import inspect
import json
import os
from pathlib import Path
import sys
import types

import pytest

from specmind_prep.injected.slice_readonly import Runtime, HELPER, instrument, OPERATIONS
from specmind_prep.injected.slice_trace_plugin import resolve_function, write_bounded_result
from specmind_prep.injected.slice_instrumentation import Unsupported

CONFIG = None
RUNTIME = None
ORIGINALS = {}
PATCHES = []
NAMESPACES = []
BASELINE = []
FRAMES = {}
REPORTS = []
ERRORS = []
SITES = {}
ACTIVE = False
PREVIOUS = None
LIMITED = False


def pytest_configure(config):
    global CONFIG, RUNTIME
    CONFIG = json.loads(Path(os.environ["SPECMIND_SLICE_CONFIG"]).read_text(encoding="utf-8"))
    RUNTIME = Runtime(CONFIG)


def pytest_collection_modifyitems(items):
    if len(items) != 1 or items[0].nodeid != CONFIG["test"]:
        raise pytest.UsageError("Exactly the selected read-only test is required")
    try:
        import hashlib

        if (
            hashlib.sha256(Path(inspect.__file__).read_bytes()).hexdigest()
            != CONFIG["trusted"]["inspect_sha256"]
        ):
            raise Unsupported("Inspect source differs from the trusted operation summary")
        selected = {name: resolve_function(record) for name, record in CONFIG["functions"].items()}
        replacements = {}
        for name, function in selected.items():
            record = CONFIG["functions"][name]
            if HELPER in function.__globals__:
                raise Unsupported("Read-only helper name already exists")
            RUNTIME.globals[name] = function.__globals__
            RUNTIME.bindings[name] = function
            ORIGINALS[function.__code__] = name
            RUNTIME.original_functions[name] = types.FunctionType(
                function.__code__, function.__globals__, function.__name__
            )
            for alias, binding in record["bindings"].items():
                if "." not in alias:
                    import builtins

                    actual = function.__globals__.get(alias, builtins.__dict__.get(alias))
                else:
                    module, attribute = alias.split(".")
                    if function.__globals__.get(module) is not inspect:
                        raise Unsupported("Unknown operation module")
                    actual = inspect.__dict__.get(attribute)
                if actual is not selected.get(binding, OPERATIONS.get(binding)):
                    raise Unsupported("Read-only call binding differs: " + alias)
            if CONFIG["mode"] == "instrumented":
                flags = 0
                for feature in __future__.all_feature_names:
                    flags |= function.__code__.co_flags & getattr(__future__, feature).compiler_flag
                replacement, sites = instrument(
                    record["source"],
                    record["line"],
                    record["source_file"],
                    name,
                    record["bindings"],
                    future_flags=flags,
                )
                for value in sites.values():
                    value["file"] = record["repo_path"]
                SITES.update(sites)
                replacements[name] = replacement
        if CONFIG["mode"] == "instrumented":
            for name, function in selected.items():
                namespace = function.__globals__
                if not any(namespace is n for n in NAMESPACES):
                    namespace[HELPER] = RUNTIME
                    NAMESPACES.append(namespace)
                PATCHES.append((function, function.__code__))
                function.__code__ = replacements[name]
        RUNTIME.codes = {name: function.__code__ for name, function in selected.items()}
        SITES.update(CONFIG["external_sites"])
    except Exception as exc:
        ERRORS.append(type(exc).__name__ + ": " + str(exc))
        raise pytest.UsageError(ERRORS[-1]) from exc


def profile(frame, event, value):
    global LIMITED
    if not ACTIVE:
        return
    name = ORIGINALS.get(frame.f_code)
    if name is None:
        return
    if event == "call":
        if name != CONFIG["function"] and id(frame.f_back) not in FRAMES:
            return
        if len(BASELINE) >= CONFIG["max_events"]:
            LIMITED = True
            sys.setprofile(None)
            return
        parameter = CONFIG["functions"][name]["parameter"]
        if name == CONFIG["function"]:
            RUNTIME.root_start(frame.f_locals[parameter])
        call = {
            "function": name,
            "arguments": {parameter: RUNTIME.snapshot(frame.f_locals[parameter])},
            "phase": RUNTIME.phase,
            "returned": False,
            "return": None,
        }
        BASELINE.append(call)
        FRAMES[id(frame)] = call
    elif event == "return":
        call = FRAMES.pop(id(frame), None)
        if call:
            if type(value) is not bool:
                RUNTIME.issue("Read-only functions must return bool")
            call.update(returned=True, **{"return": RUNTIME.snapshot(value)})
            if name == CONFIG["function"]:
                RUNTIME.root_id = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global ACTIVE, PREVIOUS
    RUNTIME.abc_token = abc.get_cache_token()
    ACTIVE = True
    RUNTIME.active = True
    PREVIOUS = sys.getprofile()
    if CONFIG["mode"] == "baseline":
        if PREVIOUS is not None:
            ERRORS.append("Existing profiler is unsupported")
        else:
            sys.setprofile(profile)
    try:
        yield
    finally:
        if CONFIG["mode"] == "baseline" and PREVIOUS is None:
            sys.setprofile(None)
        ACTIVE = False
        RUNTIME.active = False
        RUNTIME.guard()


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


def pytest_sessionfinish(session, exitstatus):
    for function, code in reversed(PATCHES):
        function.__code__ = code
    for namespace in NAMESPACES:
        if namespace.get(HELPER) is RUNTIME:
            namespace.pop(HELPER)
    payload = RUNTIME.result()
    payload.update(
        mode=CONFIG["mode"],
        baseline_calls=BASELINE,
        reports=REPORTS,
        exitcode=int(exitstatus),
        functions=list(CONFIG["functions"]),
        sites=SITES,
        baseline_limited=LIMITED,
        errors=ERRORS + (RUNTIME.issues if CONFIG["mode"] == "baseline" else []),
    )
    write_bounded_result(CONFIG["result"], payload, 3 * CONFIG["max_bytes"])
