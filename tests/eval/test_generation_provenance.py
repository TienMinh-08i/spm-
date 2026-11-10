import pytest
from specmind_eval.generate.loop import generate
from specmind_eval.common.storage import read


class Ev:
    def correctness(self,assertion):return {'status':'pass'}


def cfg():return {'mode':'multiturn','max_turns':1,'threshold':.9}


def test_cached_response_cannot_be_reported_as_fresh(tmp_path):
    def cached(messages,config):return '<solution>assert True</solution>',{'cached_response':True}
    state=generate('def f(): return 1','f',Ev(),[],cfg(),tmp_path/'g.json',cached)
    assert state['attempts'][0]['response_origin']=='cached_response'
    assert 'No pre-call snapshots' in state['history'][1]['content']


def test_custom_transport_is_explicit_and_changed_prompt_cannot_resume(tmp_path):
    def custom(*a):return '<solution>assert True</solution>',{}
    p=tmp_path/'g.json'
    state=generate('def f(): return 1','f',Ev(),[],cfg(),p,custom)
    assert state['attempts'][0]['response_origin']=='custom_callable'
    with pytest.raises(ValueError,match='prompt/protocol changed'):
        generate('def f(): return 2','f',Ev(),[],cfg(),p,custom)


def test_pending_response_keeps_origin_and_does_not_call_twice(tmp_path):
    calls=[]
    def cached(*a):calls.append(1);return '<solution>assert True</solution>',{'cached_response':True}
    class Interrupted(Ev):
        def correctness(self,*a):raise RuntimeError('interrupted')
    p=tmp_path/'g.json'
    with pytest.raises(RuntimeError):generate('def f(): return 1','f',Interrupted(),[],cfg(),p,cached)
    state=generate('def f(): return 1','f',Ev(),[],cfg(),p,cached)
    assert calls==[1]
    assert state['attempts'][0]['response_origin']=='cached_response'
    assert state['attempts'][0]['response_delivery']=='pending_resume'


def test_pending_response_cannot_cross_prompt_without_state_file(tmp_path):
    from specmind_eval.common.storage import write
    calls=[]
    def cached(*a):calls.append(1);return '<solution>assert True</solution>',{'cached_response':True}
    class Interrupted(Ev):
        def correctness(self,*a):raise RuntimeError('interrupted')
    p=tmp_path/'g.json'
    with pytest.raises(RuntimeError):generate('def f(): return 1','f',Interrupted(),[],cfg(),p,cached)
    pending=p.with_name('pending_response.json');data=read(pending);data['messages_sha256']='different';write(pending,data)
    state=generate('def f(): return 1','f',Ev(),[],cfg(),p,cached)
    assert 'Pending response prompt changed' in state['error']
    assert calls==[1] and state['attempts']==[]
