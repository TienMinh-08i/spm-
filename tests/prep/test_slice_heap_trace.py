"""Subprocess verification of fully automatic caller/target heap instrumentation."""
from specmind_eval.common.storage import read
from specmind_prep.project import validate_project
from specmind_prep.slicing import trace_function,load_trace,HEAP_CALL_MODEL


def test_heap_trace_runs_without_manual_frames_or_hooks(tmp_path,monkeypatch):
    repo=tmp_path/'repo';(repo/'src/demo').mkdir(parents=True);(repo/'tests').mkdir()
    (repo/'src/demo/__init__.py').write_text('''def target(obj, value):
    alias = obj
    alias['count'] = value
    return obj['count']

def caller(obj):
    value = 5
    result = target(obj, value)
    used = result + obj['count']
    return used
''')
    (repo/'tests/test_demo.py').write_text("from demo import caller\ndef test_f():\n    obj={'count':0}\n    assert caller(obj)==10\n    assert obj=={'count':5}\n")
    project=validate_project({'name':'demo','packages':['demo']},repo)
    out=tmp_path/'trace'
    summary=trace_function(repo,project,'tests/test_demo.py::test_f','demo.target',out,caller='demo.caller',heap=True)
    assert summary['status']=='complete_supported',summary
    nodes,sites,manifest=load_trace(out,repo)
    assert manifest['model']==HEAP_CALL_MODEL
    store=next(n for n in nodes if n['kind']=='heap_write')
    assert any(n['kind']=='heap_read' and store['id'] in n['depends_on'] for n in nodes)
    assert len({n['frame'] for n in nodes})==2
    assert all(n['site'] in sites for n in nodes)
    baseline=read(out/'baseline.json')['baseline_calls']
    assert baseline[0]['arguments']!=baseline[0]['arguments_after']

    from specmind_prep.slice_queries import query_invocations
    automatic=query_invocations(out,repo)
    assert len(automatic['calls'])==1
    assert set(x['parameter'] for x in automatic['calls'][0]['inputs'])=={'obj','value'}
    mutations=automatic['calls'][0]['mutations']
    assert len(mutations)==1 and mutations[0]['root']==store['id']
    assert any(n['kind']=='heap_read' and n['frame']==1 for n in mutations[0]['nodes'])
    from specmind_prep import slicing_pipeline
    monkeypatch.setattr(slicing_pipeline,'load_prepared_dataset',lambda *a:([{'function_id':'demo.target','problem_id':'demo.target'}],
                         {'demo.target':[{'nodeid':'tests/test_demo.py::test_f'}]}, {'fingerprint':'fixture'}))
    summary=slicing_pipeline.prepare(tmp_path/'dataset',repo,project,tmp_path/'pipeline')
    assert summary['supported']==1
    pack=read(tmp_path/'pipeline/packs.json')['demo.target']
    assert 'INPUT ORIGINS: obj' in pack['repository_context']
    assert 'OUTPUT USES' in pack['repository_context']
    assert 'MUTATION USES:' in pack['repository_context']
    coverage=read(tmp_path/'pipeline/coverage.json')[0]
    assert coverage['verified_rddg_occurrences']==coverage['full_nodes']
    assert read(tmp_path/'pipeline/manifest.json')['complete'] is True


def test_none_return_still_has_automatic_mutation_use_context(tmp_path):
    from specmind_prep.slice_queries import query_invocations
    repo=tmp_path/'repo';(repo/'src/demo').mkdir(parents=True);(repo/'tests').mkdir()
    (repo/'src/demo/__init__.py').write_text("def target(obj):\n    obj['x']=9\n    return None\n\ndef caller(obj):\n    ignored=target(obj)\n    return obj['x']\n")
    (repo/'tests/test_demo.py').write_text("from demo import caller\ndef test_f():\n    assert caller({'x':0})==9\n")
    project=validate_project({'name':'demo','packages':['demo']},repo)
    out=tmp_path/'trace'
    assert trace_function(repo,project,'tests/test_demo.py::test_f','demo.target',out,caller='demo.caller',heap=True)['status']=='complete_supported'
    call=query_invocations(out,repo)['calls'][0]
    assert len(call['mutations'])==1
    assert any(n['kind']=='heap_read' for n in call['mutations'][0]['nodes'])


def test_returned_allocation_read_depends_on_return_bridge(tmp_path):
    from specmind_prep.slice_queries import query_invocations
    repo=tmp_path/'repo';(repo/'src/demo').mkdir(parents=True);(repo/'tests').mkdir()
    (repo/'src/demo/__init__.py').write_text("def target(value):\n    return {'x':value}\n\ndef caller():\n    return target(8)['x']\n")
    (repo/'tests/test_demo.py').write_text("from demo import caller\ndef test_f():\n    assert caller()==8\n")
    project=validate_project({'name':'demo','packages':['demo']},repo)
    out=tmp_path/'trace'
    summary=trace_function(repo,project,'tests/test_demo.py::test_f','demo.target',out,caller='demo.caller',heap=True)
    assert summary['status']=='complete_supported',summary
    call=query_invocations(out,repo)['calls'][0]
    assert any(n['kind']=='heap_read' for n in call['output']['nodes'])


def test_container_comparison_is_not_silently_marked_supported(tmp_path):
    repo=tmp_path/'repo';(repo/'src/demo').mkdir(parents=True);(repo/'tests').mkdir()
    (repo/'src/demo/__init__.py').write_text("def target(obj):\n    return obj == {'x':1}\n\ndef caller(obj):\n    return target(obj)\n")
    (repo/'tests/test_demo.py').write_text("from demo import caller\ndef test_f():\n    assert caller({'x':1})\n")
    project=validate_project({'name':'demo','packages':['demo']},repo)
    summary=trace_function(repo,project,'tests/test_demo.py::test_f','demo.target',tmp_path/'trace',caller='demo.caller',heap=True)
    assert summary['status']=='unsupported'
    assert any('Container operators' in issue for issue in summary['issues'])


def test_heap_evidence_snapshot_is_bounded_and_keeps_cycles():
    import pytest
    from specmind_prep.injected.slice_heap_runtime import EvidenceSnapshot
    from specmind_prep.injected.slice_heap import UnsupportedHeap
    cyclic={};cyclic['self']=cyclic
    snapshot=EvidenceSnapshot().snapshot(cyclic)
    ref=snapshot['root']['ref']
    assert snapshot['objects'][ref]['items']==[['self',{'ref':ref}]]
    with pytest.raises(UnsupportedHeap,match='location budget'):
        EvidenceSnapshot().snapshot([[0]*6000,[0]*6000])
