import pytest
from specmind_prep.injected.slice_heap_access import HeapAccessRecorder, instrument_heap_accesses, HELPER
from specmind_prep.injected.slice_heap import UnsupportedHeap


def execute(source):
    hooks=HeapAccessRecorder()
    ns={HELPER:hooks}
    exec(instrument_heap_accesses(source),ns)
    return ns,hooks


def test_record_alias_reads_and_writes_automatically():
    ns,h=execute("def f(a):\n    b=a\n    b['x']=5\n    return a['x']\n")
    a={'x':0}
    assert ns['f'](a)==5 and a=={'x':5}
    assert [e['kind'] for e in h.events]==['heap_origin','heap_write','heap_read']
    assert h.events[-1]['depends_on']==[2]
    assert h.events[-1]['location']==h.events[1]['location']


def test_store_evaluation_order_matches_python_exactly():
    source="""def f(a, log):
    def rhs():
        log('rhs')
        return 9
    def container():
        log('container')
        return a
    def key():
        log('key')
        return 'x'
    container()[key()] = rhs()
    return a['x']
"""
    plain={};exec(source,plain)
    ns,h=execute(source)
    left=[];right=[]
    assert plain['f']({'x':0},left.append)==ns['f']({'x':0},right.append)==9
    assert left==right==['rhs','container','key']


def test_cross_function_mutation_keeps_same_origin():
    ns,h=execute("def helper(b):\n    b['x']=7\ndef f(a):\n    helper(a)\n    return a['x']\n")
    assert ns['f']({'x':0})==7
    assert len([e for e in h.events if e['kind']=='heap_origin'])==1
    assert h.events[-1]['depends_on']==[2]


def test_literals_nested_alias_and_rebinding():
    ns,h=execute("def f():\n    child={'x':0}\n    a={'child':child}\n    b=a['child']\n    b['x']=8\n    b={'x':9}\n    return a['child']['x']\n")
    assert ns['f']()==8
    last=h.events[-1]
    writer=next(e for e in h.events if e['kind']=='heap_write')
    assert last['depends_on']==[writer['id']]


def test_delete_and_exception_match_native_operation():
    ns,h=execute("def f(a):\n    del a['x']\n    return a['x']\n")
    a={'x':1}
    with pytest.raises(KeyError) as err:ns['f'](a)
    assert err.value.args==('x',) and a=={}
    assert [e['kind'] for e in h.events]==['heap_origin','heap_delete']


def test_list_assignment_and_negative_read():
    ns,h=execute("def f(a):\n    a[1]=30\n    return a[-1]\n")
    assert ns['f']([10,20])==30
    assert h.events[-1]['depends_on']==[2]


@pytest.mark.parametrize('source',["a[0] += 1","a[0] = b[0] = 2","a[0:2] = [1]","a[0]: int = 2","a=[x for x in b]"])
def test_unsupported_rewrite_rejected_before_execution(source):
    with pytest.raises(UnsupportedHeap):instrument_heap_accesses(source)


def test_read_order_and_no_duplicate_evaluation():
    ns,h=execute("def f(a, log):\n    def base():\n        log('base')\n        return a\n    def key():\n        log('key')\n        return 'x'\n    return base()[key()]\n")
    log=[]
    assert ns['f']({'x':3},log.append)==3
    assert log==['base','key']
    assert len([e for e in h.events if e['kind']=='heap_read'])==1


def test_rhs_failure_does_not_evaluate_store_target():
    ns,h=execute("def f(a, log):\n    def base():\n        log('base')\n        return a\n    def rhs():\n        log('rhs')\n        raise RuntimeError('failed')\n    base()['x']=rhs()\n")
    log=[];a={'x':0}
    with pytest.raises(RuntimeError,match='failed'):ns['f'](a,log.append)
    assert log==['rhs'] and a=={'x':0}
    assert not any(e['kind']=='heap_write' for e in h.events)


@pytest.mark.parametrize('source',["def f(__specmind_heap_access__):\n    return 1", "for a[0] in [1]:\n    pass", "def __specmind_heap_access__():\n    pass"])
def test_unobserved_store_forms_and_reserved_bindings_are_rejected(source):
    with pytest.raises(UnsupportedHeap):instrument_heap_accesses(source)
