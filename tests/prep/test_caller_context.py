import pytest
from specmind_prep import caller_context as c


def test_heuristic_without_explicit_criterion_is_disabled(tmp_path):
    with pytest.raises(ValueError, match='criteria'):
        c.prepare(tmp_path,tmp_path,tmp_path,tmp_path/'out')


def fixture_trace(monkeypatch,tmp_path):
    from specmind_eval.common.storage import write
    write(tmp_path/'criteria.json',[{'function':'api','trace':'trace','caller':'caller','event':3,'variable':'y','when':'after'}])
    monkeypatch.setattr(c,'load_prepared_dataset',lambda *a:([{'function_id':'api','problem_id':'target'}],{'api':[{'nodeid':'tests/test_demo.py::test_caller'}]}, {'fingerprint':'dataset'}))
    manifest={'model':c.CALL_MODEL,'function':'target','caller':'caller','fingerprint':'trace-fingerprint','test':'tests/test_demo.py::test_caller'}
    monkeypatch.setattr(c,'load_trace',lambda *a:([{'id':3,'function':'caller'}],{},manifest))
    monkeypatch.setattr(c,'source_environment',lambda *a:{})
    return manifest


def test_export_uses_explicit_caller_variable_and_backward_only(monkeypatch,tmp_path):
    from specmind_eval.common.storage import read
    fixture_trace(monkeypatch,tmp_path)
    def query(trace,repo,event,variable,when):
        assert (event,variable,when)==(3,'y','after')
        return {'status':'complete_supported','criterion':{'event':3,'variable':'y','when':'after'},
                'source_statements':[{'file':'a.py','line':2,'source':'y = x'}], 'limitations':['primitive-local scope']}
    monkeypatch.setattr(c,'query_slice',query)
    c.prepare(tmp_path,tmp_path/'repo',None,tmp_path/'out',tmp_path/'criteria.json')
    pack=read(tmp_path/'out/packs.json')['api']
    assert pack['metadata']['direction']=='backward'
    assert pack['metadata']['model']==c.MODEL
    assert 'a.py:2: y = x' in pack['repository_context']


def test_unverified_model_cannot_produce_context(monkeypatch,tmp_path):
    manifest=fixture_trace(monkeypatch,tmp_path)
    manifest['model']='executed-line-conservative-caller-ddg-v1'
    with pytest.raises(ValueError,match='verified'):
        c.prepare(tmp_path,tmp_path/'repo',None,tmp_path/'out',tmp_path/'criteria.json')
    assert not (tmp_path/'out').exists()

def test_real_trace_exports_only_causal_caller_context(tmp_path, monkeypatch):
    from specmind_eval.common.storage import read, write
    from specmind_prep.project import validate_project
    from specmind_prep.slicing import trace_function
    repo=tmp_path/'repo'
    (repo/'src/demo').mkdir(parents=True)
    (repo/'tests').mkdir()
    (repo/'src/demo/__init__.py').write_text('def target(x):\n    return x * 2\n\ndef caller():\n    irrelevant = 99\n    x = 2\n    x += 1\n    result = target(x)\n    return result\n')
    (repo/'tests/test_demo.py').write_text('from demo import caller\ndef test_caller():\n    assert caller() == 6\n')
    project=validate_project({'name':'demo','packages':['demo']},repo)
    trace=tmp_path/'trace'
    assert trace_function(repo,project,'tests/test_demo.py::test_caller','demo.target',trace,caller='demo.caller')['status']=='complete_supported'
    call=next(c for c in read(trace/'calls.json') if c['function']=='demo.caller')
    write(tmp_path/'criteria.json',[{'function':'api','trace':'trace','caller':'demo.caller','event':call['return_event'],'variable':'$return','when':'after'}])
    monkeypatch.setattr(c,'load_prepared_dataset',lambda *a:([{'function_id':'api','problem_id':'demo.target'}],{'api':[{'nodeid':'tests/test_demo.py::test_caller'}]}, {'fingerprint':'dataset'}))
    c.prepare(tmp_path,repo,None,tmp_path/'out',tmp_path/'criteria.json')
    text=read(tmp_path/'out/packs.json')['api']['repository_context']
    assert 'irrelevant = 99' not in text
    assert 'x += 1' in text
    assert 'x = 2' in text
    assert 'return x * 2' in text

    # Same verified execution, now without any manually selected variable/event.
    write(tmp_path/'criteria_auto.json',[{'function':'api','trace':'trace','caller':'demo.caller','mode':'auto'}])
    c.prepare(tmp_path,repo,None,tmp_path/'auto',tmp_path/'criteria_auto.json')
    auto=read(tmp_path/'auto/packs.json')['api']
    assert auto['metadata']['direction']=='backward_inputs_forward_output'
    assert len(auto['criterion'])==1
    assert set(auto['criterion'][0]['arguments'])=={'x'}
    assert 'INPUT ORIGINS: x' in auto['repository_context']
    assert 'OUTPUT USES' in auto['repository_context']
    assert 'irrelevant = 99' not in auto['repository_context']
