from copy import deepcopy
import pytest
from specmind_prep import mutant_rebind as m
from specmind_eval.common.storage import read


def setup(monkeypatch,tmp_path):
    p={'function_id':'p.api','problem_id':'p.helper','source':'def helper(x):\n    return x\n','module':'p','repo_path':'p.py'}
    old=deepcopy(p);new=deepcopy(p)
    manifests={'old':{'fingerprint':'old','files':{}},'new':{'fingerprint':'new','files':{}}}
    monkeypatch.setattr(m,'load_prepared_dataset',lambda path,repo:([old if path.name=='old' else new],{},manifests[path.name]))
    parent={'schema':'prepared-mutants-v1','complete':True,'fingerprint':'source','files':{},'generator':'fixture'}
    corpus={'p.api':{'function':'p.api','done':True,'scope':{},'mutants':[{'id':'m1','function_source':'def helper(x):\n    return 0\n','mutation_problem':{'problem_id':'p.helper','source':p['source']}}]}}
    monkeypatch.setattr(m,'load_prepared_mutants',lambda *a,**kw:(corpus,parent))
    row={'assertion_target':'p.api','analysis_target':'p.helper','mutation_targets':[{'target':'p.helper','observed_nodeids':['test_new']} ]}
    monkeypatch.setattr(m,'load_scope',lambda *a:([row],[{'problem_id':'p.helper','source':p['source']}],{'dataset_fingerprint':'new','dataset_files':{},'fingerprint':'scope','files':{}}))
    monkeypatch.setattr(m,'source_environment',lambda *a:{})
    args=[tmp_path/n for n in ['source','old','new','scope','repo','out']]
    return args,old,new,parent,corpus


def test_rebind_preserves_mutant_source_and_replaces_scope_not_old_evidence(monkeypatch,tmp_path):
    args,old,new,parent,corpus=setup(monkeypatch,tmp_path)
    before=deepcopy(corpus)
    result=m.rebind_mutants(*args)
    assert result['runtime_filter_required'] and result['unique_mutants']==1
    rows=read(args[-1]/'functions.json')
    assert rows[0]['mutants']==before['p.api']['mutants']
    assert rows[0]['scope']['mutation_targets'][0]['observed_nodeids']==['test_new']
    assert corpus==before
    manifest=read(args[-1]/'manifest.json')
    assert manifest['dataset_fingerprint']=='new'
    assert manifest['rebind']['runtime_evidence_reused'] is False
    assert manifest['runtime_filter_required'] is True


@pytest.mark.parametrize('field,value',[('source','def helper(x):\n    return x+1\n'),('_delegate_binding',{'tag':'other'})])
def test_changed_source_or_ownership_rejected(monkeypatch,tmp_path,field,value):
    args,old,new,parent,corpus=setup(monkeypatch,tmp_path)
    new[field]=value
    with pytest.raises(ValueError,match='Source or call ownership'):m.rebind_mutants(*args)
    assert not args[-1].exists()


def test_old_filtered_corpus_is_not_promoted(monkeypatch,tmp_path):
    args,old,new,parent,corpus=setup(monkeypatch,tmp_path)
    parent['neutral_filter']={'assertion':'assert True'}
    with pytest.raises(ValueError,match='unfiltered'):m.rebind_mutants(*args)
