
import json, os, sys, threading
from collections import defaultdict
from pathlib import Path
import pytest

config=json.loads(Path(os.environ['SPECMIND_SCOPE_CONFIG']).read_text())
locations={(str(Path(p).resolve()).casefold(),int(line)):key for p,line,key in config['locations']}
short=set(config['short'])
allowed={k:set(v) for k,v in config['allowed'].items()}
edges=defaultdict(set)
external=defaultdict(set)
calls=defaultdict(set)
current=None
cache={}

def key(code):
    if code not in cache:
        cache[code]=locations.get((str(Path(code.co_filename).resolve()).casefold(),code.co_firstlineno))
    return cache[code]

def parent(frame):
    caller=frame.f_back
    # Comprehensions are part of the lexical function, not a second scope level.
    while caller and caller.f_code.co_name in ('<genexpr>','<listcomp>','<dictcomp>','<setcomp>'):
        caller=caller.f_back
    return caller

def profile(frame,event,arg):
    if current is None:
        return
    if event=='call':
        target=key(frame.f_code)
        if target and current in allowed.get(target,set()):
            calls[target].add(current)
        caller=parent(frame)
        origin=key(caller.f_code) if caller else None
        if origin in short and current in allowed.get(origin,set()):
            if target:
                edges[(origin,target)].add(current)
            elif not frame.f_code.co_name.startswith('<'):
                external[origin].add(frame.f_code.co_filename+':'+frame.f_code.co_qualname)
    elif event=='c_call':
        origin=key(frame.f_code)
        if origin in short and current in allowed.get(origin,set()):
            external[origin].add((getattr(arg,'__module__','') or '')+'.'+getattr(arg,'__qualname__',str(type(arg))))

@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_runtest_call(item):
    global current
    current=item.nodeid
    before=sys.getprofile()
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(before)
        current=None

def pytest_sessionfinish(session,exitstatus):
    Path(os.environ['SPECMIND_SCOPE_RESULT']).write_text(json.dumps({
        'edges':[{'caller':a,'callee':b,'nodeids':sorted(nodes)} for (a,b),nodes in edges.items()],
        'external':{k:sorted(v) for k,v in external.items()},
        'calls':{k:sorted(v) for k,v in calls.items()},'exitstatus':exitstatus}),encoding='utf-8')
