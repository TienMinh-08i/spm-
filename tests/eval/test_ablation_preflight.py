import importlib
import pytest
from specmind_eval.experiment import experiment


def test_all_arm_input_checks_finish_before_model_runs(tmp_path,monkeypatch):
    runtime=importlib.import_module('specmind_eval.run')
    monkeypatch.setattr('specmind_eval.common.git.verify_commit',lambda *a:None)
    monkeypatch.setattr('specmind_eval.context.callgraph.CallGraph.load',lambda *a:object())
    checked=[];runs=[]
    def check(config,output):
        checked.append(config['context_mode'])
        if config['context_mode']=='callgraph':raise ValueError('D caller slice missing')
        return {'status':'inputs_valid'}
    monkeypatch.setattr(runtime,'preflight',check)
    monkeypatch.setattr(runtime,'run',lambda *a:runs.append(a))
    with pytest.raises(ValueError,match='D caller slice missing'):
        experiment({'repo_root':str(tmp_path/'repo'),'call_graph_dir':'graph'},tmp_path/'out')
    assert checked==['none','callee','caller','callgraph']
    assert runs==[]
