import pytest
from specmind_prep.injected.slice_heap import HeapMemory, UnsupportedHeap


def test_alias_and_rebinding_track_same_location():
    h=HeapMemory();a={'count':0};b=a
    assert h.register(a,1)==h.register(b,1)=='o1'
    h.write(b,'count',5,2)
    assert h.read(a,'count')==(5,{'location':'o1["count"]','producer':2})
    b={'count':9};h.register(b,3);h.write(b,'count',10,4)
    assert h.read(a,'count')[1]['producer']==2
    assert h.read(b,'count')[1]['producer']==4


def test_overwrite_and_delete_reinsert():
    h=HeapMemory();a={'x':1};h.register(a,1)
    h.write(a,'x',2,2);h.write(a,'x',3,3)
    assert h.read(a,'x')[1]['producer']==3
    h.delete(a,'x',4)
    with pytest.raises(KeyError):h.read(a,'x')
    h.write(a,'x',8,5)
    assert h.read(a,'x')[1]['producer']==5


def test_list_negative_index_alias_and_structural_mutation():
    h=HeapMemory();a=[10,20];h.register(a,1);b=a
    h.write(b,-1,30,2)
    assert h.read(a,1)==(30,{'location':'o1[1]','producer':2})
    with pytest.raises(UnsupportedHeap,match='index-shift'):h.delete(a,0,3)
    assert a==[10,30]
    a.append(40)
    with pytest.raises(UnsupportedHeap,match='Uninstrumented'):h.read(a,0)


def test_shallow_copy_shares_nested_object_not_outer_slots():
    h=HeapMemory();child={'x':1};a={'child':child};b=a.copy()
    h.register(child,1);h.register(a,2);h.register(b,3)
    av,_=h.read(a,'child');bv,_=h.read(b,'child')
    assert av is bv
    h.write(bv,'x',9,4)
    assert h.read(av,'x')[1]['producer']==4
    h.write(b,'child',None,5)
    assert h.read(a,'child')[0] is child


def test_mutation_through_helper_retains_producer():
    h=HeapMemory();obj={'x':0};h.register(obj,1)
    def helper(alias):h.write(alias,'x',7,2)
    helper(obj)
    assert h.read(obj,'x')[1]['producer']==2


def test_custom_hooks_are_not_called():
    class Hostile:
        def __repr__(self):raise AssertionError('repr')
        def __hash__(self):raise AssertionError('hash')
        def __eq__(self,other):raise AssertionError('eq')
    h=HeapMemory()
    with pytest.raises(UnsupportedHeap):h.register({'x':Hostile()},1)
    assert not h.objects
    with pytest.raises(UnsupportedHeap):h.register(Hostile(),1)


def test_failed_store_is_atomic_and_unknown_origin_rejected():
    h=HeapMemory();a=[1];h.register(a,1)
    with pytest.raises(IndexError):h.write(a,9,2,2)
    assert a==[1] and h.last_event==1
    with pytest.raises(UnsupportedHeap):h.read({},'x')
    with pytest.raises(ValueError):h.write(a,0,3,1)


def test_cycles_and_budget():
    h=HeapMemory(max_objects=1,max_locations=1);a={};a['self']=a
    h.register(a,1)
    assert h.read(a,'self')[0] is a
    with pytest.raises(UnsupportedHeap,match='budget'):h.register({},2)
    with pytest.raises(UnsupportedHeap,match='budget'):h.write(a,'extra',1,2)
    assert 'extra' not in a
