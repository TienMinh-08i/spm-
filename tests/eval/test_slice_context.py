import pytest
from specmind_eval.context.slicing import load_slice_context
from specmind_eval.common.storage import write
from specmind_eval.common.fingerprint import file_hash

def test_code_context_checks_target_budget_and_hash(tmp_path, monkeypatch):
    monkeypatch.setattr('specmind_eval.context.slicing.verify_commit', lambda repo, commit: None)
    artifact = tmp_path / 'packs.json'
    write(artifact, {'api': {'analysis_target':'helper','repository_context':'x = data[key]'}})
    write(tmp_path/'manifest.json', {'schema':'dynapyt-code-v1','commit':'revision','count':1,'sha256':file_hash(artifact)})
    problem={'function_id':'api','problem_id':'helper'}
    assert load_slice_context(tmp_path,tmp_path,[problem],100)[0]['api']['repository_context']=='x = data[key]'
    with pytest.raises(ValueError,match='budget'):
        load_slice_context(tmp_path,tmp_path,[problem],4)
    with pytest.raises(ValueError,match='target'):
        load_slice_context(tmp_path,tmp_path,[dict(problem,problem_id='other')],100)
    write(artifact,{})
    with pytest.raises(ValueError,match='artifact mismatch'):
        load_slice_context(tmp_path,tmp_path,[problem],100)
