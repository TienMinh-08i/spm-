import pytest
from specmind_eval.context.slicing import load_slice_context
from specmind_eval.common.storage import write
from specmind_eval.common.fingerprint import digest,file_hash


def test_verified_context_target_budget_and_tampering(tmp_path,monkeypatch):
    monkeypatch.setattr('specmind_eval.context.slicing.source_environment',lambda _: {})
    packs={'api':{'analysis_target':'helper','repository_context':'slice','metadata':{'actual_chars':5}}}
    write(tmp_path/'packs.json',packs)
    m={'schema':'prepared-slice-context-v1','complete':True,'source_environment':{},'files':{'packs.json':file_hash(tmp_path/'packs.json')}}
    m['fingerprint']=digest(m);write(tmp_path/'manifest.json',m)
    problems=[{'function_id':'api','problem_id':'helper'}]
    assert load_slice_context(tmp_path,tmp_path,problems,5)[0]['api']['repository_context']=='slice'
    with pytest.raises(ValueError,match='budget'):load_slice_context(tmp_path,tmp_path,problems,4)
    with pytest.raises(ValueError,match='target'):load_slice_context(tmp_path,tmp_path,[{'function_id':'api','problem_id':'other'}],5)
    write(tmp_path/'packs.json',{})
    with pytest.raises(ValueError,match='artifact mismatch'):load_slice_context(tmp_path,tmp_path,problems,5)
