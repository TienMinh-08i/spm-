"""Regression against pilot narrowing at the trace -> prepare boundary."""
import pytest
from specmind_eval.common.storage import read,write
from specmind_prep.trace.trace import trace,traced_test_selection
from specmind_prep.gather.gather import gather
from specmind_prep.filter.dataset import prepare_linked_dataset


def test_filter_uses_executed_full_trace_scope_not_candidate_pilot(tmp_path):
    repo=tmp_path/'repo';(repo/'src/demo').mkdir(parents=True);(repo/'tests').mkdir()
    (repo/'src/demo/__init__.py').write_text('def f(x):\n    """Increment."""\n    return x+1\n')
    (repo/'tests/test_f.py').write_text('from demo import f\ndef test_a():\n    assert f(1)==2\ndef test_b():\n    assert f(3)==4\n')
    project={'name':'demo','source_root':'src','packages':['demo'],'tests':['tests']}
    candidates=tmp_path/'candidates';gather(repo,candidates,project=project)
    m=read(candidates/'manifest.json');m['test_selection']=['tests/test_f.py::test_a'];write(candidates/'manifest.json',m)
    links=tmp_path/'links';trace(candidates,repo,links,timeout=30)
    assert traced_test_selection(links)==['tests']
    assert read(links/'manifest.json')['scope_kind']=='project_full'
    dataset=tmp_path/'dataset'
    result=prepare_linked_dataset(candidates,links,repo,dataset,timeout=30)
    assert result['selected_links']==2
    assert read(dataset/'manifest.json')['collection_tests']==['tests']


def test_manifest_cannot_claim_full_trace_over_pilot_command(tmp_path):
    write(tmp_path/'command.json',{'argv':['python','-m','pytest','-p','specmind_prep.injected.call_trace_plugin','tests/test_a.py::test_a']})
    with pytest.raises(ValueError,match='differs'):traced_test_selection(tmp_path,{'test_selection':['tests']})


def test_mixed_scope_reuse_cannot_silently_narrow_collection(tmp_path):
    parent=tmp_path/'old/links';parent.mkdir(parents=True)
    write(parent/'command.json',{'argv':['python','-p','specmind_prep.injected.call_trace_plugin','tests/test_a.py::test_a']})
    write(parent/'manifest.json',{'complete':True})
    current=tmp_path/'current';current.mkdir()
    write(current/'command.json',{'argv':['python','-p','specmind_prep.injected.call_trace_plugin','tests']})
    with pytest.raises(ValueError,match='Mixed-scope'):
        traced_test_selection(current,{'reused_links':{'path':str(tmp_path/'old')}})


def test_same_paths_with_different_reused_exclusions_are_rejected(tmp_path):
    parent=tmp_path/'old/links';parent.mkdir(parents=True)
    write(parent/'command.json',{'argv':['python','-p','specmind_prep.injected.call_trace_plugin','-k','not test_a','tests']})
    write(parent/'manifest.json',{'complete':True})
    current=tmp_path/'current';current.mkdir()
    write(current/'command.json',{'argv':['python','-p','specmind_prep.injected.call_trace_plugin','tests']})
    with pytest.raises(ValueError,match='exclusions'):
        traced_test_selection(current,{'reused_links':{'path':str(tmp_path/'old')}})
