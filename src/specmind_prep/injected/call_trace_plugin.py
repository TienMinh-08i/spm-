"""Collect exact function/test call links in pytest's call phase."""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
import pytest
from specmind_eval.common.call_binding import matches_call

config = json.loads(Path(os.environ["MM_PREP_TRACE_CONFIG"]).read_text(encoding="utf8"))
locations = defaultdict(list)
tasks = config.get('tasks', [{'path':p,'line':line,'key':key} for p,line,key in config.get('locations',[])])
for task in tasks:
    locations[(os.path.normcase(str(Path(task['path']).resolve())), int(task['line']))].append(task)
cache = {}
links = defaultdict(set)
reports = {}
nodeids = []
current = None


def key(code):
    if code not in cache:
        cache[code] = locations.get(
            (os.path.normcase(str(Path(code.co_filename).resolve())), code.co_firstlineno)
        )
    return cache[code]


def profile(frame, event, arg):
    if current is not None and event == "call":
        for task in key(frame.f_code) or []:
            if not task.get('binding') or matches_call(task['binding'], frame.f_locals, frame.f_back, config['repo']):
                links[current].add(task['key'])


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    global current
    before = sys.getprofile()
    current = item.nodeid
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(before)
        current = None


def pytest_collection_finish(session):
    nodeids.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    reports.setdefault(report.nodeid, {})[report.when] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    path = Path(os.environ["MM_PREP_TRACE_RESULT"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "nodeids": nodeids,
                "links": {n: sorted(ids) for n, ids in links.items()},
                "reports": reports,
                "exitstatus": exitstatus,
                "complete": (not session.shouldstop and not session.shouldfail
                             and all("teardown" in reports.get(n, {}) for n in nodeids)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf8",
    )
