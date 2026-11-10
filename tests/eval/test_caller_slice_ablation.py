import pytest
from specmind_eval.run import run


@pytest.mark.parametrize('mode',['caller','callgraph'])
def test_abcd_caller_slice_rejects_target_helper_slice(tmp_path,monkeypatch,mode):
    repo=tmp_path/'repo';repo.mkdir()
    monkeypatch.setattr('specmind_eval.run.verify_commit',lambda *a:None)
    monkeypatch.setattr('specmind_eval.run.CallGraph.load',lambda *a:object())
    monkeypatch.setattr('specmind_eval.run.load_prepared_dataset',lambda *a:([{'function_id':'api','problem_id':'helper'}],{'api':[]},{}))
    monkeypatch.setattr('specmind_eval.run.load_prepared_mutants',lambda *a:({'api':{}},{}))
    monkeypatch.setattr('specmind_eval.context.slicing.load_slice_context',lambda *a:({'api':{'metadata':{'status':'complete_supported'}}},'hash'))
    cfg={'repo_root':str(repo),'context_mode':mode,'caller_context':'slice','call_graph_dir':'graph',
         'slice_context_dir':'slices','context_budget':8000,'prepared_dataset':'dataset','prepared_mutants':'mutants'}
    with pytest.raises(ValueError,match='Expected a caller slice'):
        run(cfg,tmp_path/'out')

@pytest.mark.parametrize('status,model',[('conservative_dynamic','executed-line-conservative-caller-ddg-v1'),('unsupported','caller-variable-ddg-v2')])
def test_abcd_rejects_heuristic_or_unsupported_caller(tmp_path,monkeypatch,status,model):
    repo=tmp_path/'repo';repo.mkdir()
    monkeypatch.setattr('specmind_eval.run.verify_commit',lambda *a:None)
    monkeypatch.setattr('specmind_eval.run.CallGraph.load',lambda *a:object())
    monkeypatch.setattr('specmind_eval.run.load_prepared_dataset',lambda *a:([{'function_id':'api','problem_id':'helper'}],{'api':[]},{}))
    monkeypatch.setattr('specmind_eval.run.load_prepared_mutants',lambda *a:({'api':{}},{}))
    monkeypatch.setattr('specmind_eval.context.slicing.load_slice_context',lambda *a:({'api':{'metadata':{'role':'caller_slice','status':status,'model':model}}},'hash'))
    cfg={'repo_root':str(repo),'context_mode':'caller','caller_context':'slice','call_graph_dir':'graph',
         'slice_context_dir':'slices','context_budget':8000,'prepared_dataset':'dataset','prepared_mutants':'mutants'}
    with pytest.raises(ValueError,match='Regenerate caller context'):
        run(cfg,tmp_path/'out')
