"""Rebound source exports cannot be used as runtime-filtered experiments."""
import pytest
from specmind_eval.common.storage import write
from specmind_eval.common.fingerprint import file_hash
from specmind_eval.inputs.prepared import load_prepared_mutants


def test_unfiltered_rebind_blocked_without_bypassing_artifact_hashes(tmp_path,monkeypatch):
    monkeypatch.setattr('specmind_eval.inputs.prepared.source_environment',lambda p:{})
    for name,data in [('summary.json',{}),('functions.json',[]),('targets_input.json',[])]:write(tmp_path/name,data)
    m={'schema':'prepared-mutants-v1','complete':True,'runtime_filter_required':True,'source_environment':{},
       'dataset_fingerprint':'dataset','dataset_files':{},'files':{f:file_hash(tmp_path/f) for f in ['summary.json','functions.json','targets_input.json']}}
    dm={'fingerprint':'dataset','files':{}};write(tmp_path/'manifest.json',m)
    with pytest.raises(ValueError,match='Runtime filtering required'):load_prepared_mutants(tmp_path,tmp_path,dm)
    assert load_prepared_mutants(tmp_path,tmp_path,dm,allow_unfiltered=True)[0]=={}
    write(tmp_path/'functions.json',[{}])
    with pytest.raises(ValueError,match='artifact mismatch'):load_prepared_mutants(tmp_path,tmp_path,dm,allow_unfiltered=True)
