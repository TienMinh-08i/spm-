import os,json
from pathlib import Path
import pytest
wanted=set(json.loads(Path(os.environ['SLICE_TESTS']).read_text()))
phases={}
def pytest_collection_modifyitems(config,items):
    skipped=[i for i in items if i.nodeid not in wanted]
    items[:]=[i for i in items if i.nodeid in wanted]
    config.hook.pytest_deselected(items=skipped)
    if {i.nodeid for i in items}!=wanted:raise pytest.UsageError('Missing linked nodeid(s)')
def pytest_runtest_logreport(report):
    phases.setdefault(report.nodeid,{})[report.when]=report.outcome
def pytest_sessionfinish(session,exitstatus):
    passed=sorted(k for k,v in phases.items() if v==dict(setup='passed',call='passed',teardown='passed'))
    (Path(os.environ['SLICE_OUT'])/'passed.json').write_text(json.dumps(passed))
