"""Main recorder integration: shared IDs/frames and validated heap edges."""
import copy
import pytest
from specmind_prep.injected.slice_runtime import Recorder
from specmind_prep.injected.slice_heap_access import HeapAccessRecorder
from specmind_prep.slicing import build_ddg
from specmind_prep.slice_queries import closure


def recorder():
    r=Recorder({'function':'demo','max_events':1000,'max_bytes':1000000})
    r.active=True
    return r,HeapAccessRecorder(recorder=r)


def test_rhs_control_and_heap_writer_share_main_graph():
    r,h=recorder();frame=r.enter({'value':5,'flag':True});obj={'x':0}
    with h.bind(frame):
        h.input(obj,'input')
        r.begin(frame,'if');r.predicate(frame,r.read(frame,'flag',True))
        with r.control(frame):
            r.begin(frame,'store')
            h.write(r.read(frame,'value',5),obj,'x','store')
        r.begin(frame,'return')
        result=h.read(obj,'x','load')
        r.return_value(frame,result)
    nodes=build_ddg(r.events,heap=True)
    write=next(n for n in nodes if n['kind']=='heap_write')
    pred=next(n for n in nodes if n['kind']=='predicate')
    parameter=next(n for n in nodes if 'value' in n['writes'])
    assert {pred['id'],parameter['id']}<=set(write['depends_on'])
    returned=nodes[-1]
    selected=closure(nodes,[returned['id']],direction='backward')
    assert write['id'] in {n['id'] for n in selected}
    assert [n['id'] for n in nodes]==list(range(1,len(nodes)+1))
    assert returned['controls']==[]  # exited if block


def test_cross_frame_alias_resolves_last_heap_writer():
    r,h=recorder();caller=r.enter({});obj={'x':0}
    with h.bind(caller):
        h.input(obj,'caller_input')
        helper=r.enter({'value':7})
        with h.bind(helper):
            r.begin(helper,'helper_store')
            h.write(r.read(helper,'value',7),obj,'x','helper_store')
        assert h.frame==caller
        r.begin(caller,'caller_read');assert h.read(obj,'x','caller_read')==7
        r.return_value(caller,7)
    nodes=build_ddg(r.events,heap=True)
    write=next(n for n in nodes if n['kind']=='heap_write')
    read=next(n for n in nodes if n['kind']=='heap_read')
    assert write['frame']!=read['frame']
    assert write['id'] in read['depends_on']


def test_rhs_heap_read_and_forward_use_cross_store():
    r,h=recorder();frame=r.enter({});obj={'x':2,'y':0}
    with h.bind(frame):
        h.input(obj,'input')
        r.begin(frame,'store');value=h.read(obj,'x','rhs');h.write(value,obj,'y','store')
        r.begin(frame,'return');r.return_value(frame,h.read(obj,'y','load'))
    nodes=build_ddg(r.events,heap=True)
    read=next(n for n in nodes if n['kind']=='heap_read')
    write=next(n for n in nodes if n['kind']=='heap_write')
    assert read['id'] in write['depends_on']
    assert nodes[-1]['id'] in {n['id'] for n in closure(nodes,[read['id']],direction='forward')}
    with pytest.raises(ValueError):build_ddg(r.events)


def test_stale_heap_producer_and_deleted_value_rejected():
    r,h=recorder();frame=r.enter({});obj={'x':0}
    with h.bind(frame):
        h.input(obj,'input');h.write(5,obj,'x','store');h.read(obj,'x','load')
    original=copy.deepcopy(r.events)
    read=next(n for n in r.events if n['kind']=='heap_read')
    origin=next(n for n in r.events if n['kind']=='heap_origin')
    read['heap_reads']={k:origin['id'] for k in read['heap_reads']}
    with pytest.raises(ValueError,match='latest live'):build_ddg(r.events,heap=True)
    original[-2]['kind']='heap_delete';original[-2]['heap_deleted']=True
    with pytest.raises(ValueError,match='latest live'):build_ddg(original,heap=True)


def test_unbound_access_cannot_mutate_object():
    r,h=recorder();obj={'x':0}
    with pytest.raises(ValueError,match='bound'):h.write(5,obj,'x','store')
    assert obj=={'x':0} and r.events==[]


def test_new_key_write_keeps_allocation_origin_and_budget_is_checked_first():
    r,h=recorder();frame=r.enter({});obj={}
    with h.bind(frame):
        h.input(obj,'input');h.write(5,obj,'new','store')
    nodes=build_ddg(r.events,heap=True)
    origin=next(n for n in nodes if n['kind']=='heap_origin')
    assert origin['id'] in nodes[-1]['depends_on']
    r.config['max_events']=len(r.events)
    with h.bind(frame),pytest.raises(ValueError,match='budget'):h.write(9,obj,'new','store2')
    assert obj=={'new':5} and r.limited


def test_malformed_heap_payload_is_rejected():
    r,h=recorder();frame=r.enter({});obj={'x':1}
    with h.bind(frame):h.input(obj,'input');h.write(2,obj,'x','store')
    r.events[-1]['heap_writes']=[{}]
    with pytest.raises(ValueError,match='Invalid heap'):build_ddg(r.events,heap=True)
