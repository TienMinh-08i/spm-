"""Observe actual invocation stacks for prepared slicing tasks; never infer edges."""
import json
import os
import sys
from pathlib import Path
import pytest
from specmind_eval.common.call_binding import matches_call

CONFIG = json.loads(Path(os.environ["SPECMIND_DISCOVERY_CONFIG"]).read_text(encoding="utf8"))
REPO = Path(CONFIG["repo"]).resolve()
LOCATIONS = {}
for task in CONFIG["tasks"]:
    LOCATIONS.setdefault((os.path.normcase(str((REPO / task["file"]).resolve())), task["line"]), []).append(task)
CALLS = []
REPORTS = []
CURRENT = None
LIMITED = False
CACHE = {}


def location(code):
    if code not in CACHE:
        p = Path(code.co_filename).resolve()
        CACHE[code] = (os.path.normcase(str(p)), p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) and p.is_file() else None)
    return CACHE[code]


def profile(frame, event, arg):
    global LIMITED
    if CURRENT is None or event != "call" or LIMITED:
        return
    path, relative = location(frame.f_code)
    for task in LOCATIONS.get((path, frame.f_code.co_firstlineno), []):
        if task.get("binding") and not matches_call(task["binding"], frame.f_locals, frame.f_back, REPO):
            continue
        if len(CALLS) >= CONFIG.get("max_calls", 5000):
            LIMITED = True
            return
        parents = []
        parent = frame.f_back
        while parent is not None:
            _, rel = location(parent.f_code)
            if rel:
                parents.append({"file": rel, "line": parent.f_lineno,
                                "definition_line": parent.f_code.co_firstlineno,
                                "qualname": parent.f_code.co_qualname})
            parent = parent.f_back
        CALLS.append({"task": task["task"], "role": task["role"], "test": CURRENT,
                      "file": relative, "definition_line": frame.f_code.co_firstlineno,
                      "qualname": frame.f_code.co_qualname, "parents": parents})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    global CURRENT
    previous = sys.getprofile()
    if previous is not None:
        raise RuntimeError("Discovery requires an unused profiler")
    CURRENT = item.nodeid
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(previous)
        CURRENT = None


def pytest_runtest_logreport(report):
    REPORTS.append({"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome})


def pytest_sessionfinish(session, exitstatus):
    result = {"calls": CALLS, "reports": REPORTS, "limited": LIMITED, "exitcode": int(exitstatus),
              "complete": not LIMITED and not session.shouldstop and not session.shouldfail}
    Path(os.environ["SPECMIND_DISCOVERY_RESULT"]).write_text(json.dumps(result, indent=2), encoding="utf8")
