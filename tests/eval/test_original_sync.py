import ast,importlib.util,os
from pathlib import Path
import pytest
ROOT=Path(os.environ.get('SPECMIND_ORIGINAL_ROOT',Path(__file__).resolve().parents[2].parents[1]/'paper_eval_v2'))
pytestmark=pytest.mark.skipif(not (ROOT/'policy.py').is_file(),reason='Optional local original-checkout parity check')

def module(name):
    import sys
    spec=importlib.util.spec_from_file_location('flat_validation_'+name,ROOT/(name+'.py'))
    obj=importlib.util.module_from_spec(spec);sys.modules[spec.name]=obj;spec.loader.exec_module(obj)
    return obj


def test_shared_policy_matches_flat_and_packaged_semantics():
    from specmind_eval.evaluate.policy import validate
    flat=module('policy').validate
    source='def hook(fn):\n    fn.__marshmallow_hook__ = {}\n    return fn\n'
    assertion="assert return_value.__marshmallow_hook__ == {}"
    for validator in (flat,validate):
        validator(assertion,source,'hook','behavioral',state_attributes=['__marshmallow_hook__'])
        with pytest.raises(ValueError):validator(assertion,source,'hook','behavioral')
        with pytest.raises(ValueError):validator('assert return_value.__globals__',source,'hook','behavioral',state_attributes=['__globals__'])


def test_flat_runtime_rejects_delegate_identity_before_silent_merge(tmp_path):
    import json
    flat=module('dataset')
    (tmp_path/'problems.jsonl').write_text(json.dumps({'function_id':'api.a','problem_id':'helper'})+'\n')
    (tmp_path/'tests.jsonl').write_text(json.dumps({'problem_id':'api.a','nodeid':'test_a.py::test_a'})+'\n')
    spec=flat.DatasetSpec('fixture','fixture',tmp_path,'problems.jsonl','tests.jsonl')
    with pytest.raises(ValueError,match='refusing to merge'):flat.load_dataset(spec)


def test_best_selection_and_metrics_ast_stay_equal():
    active=Path(__file__).parents[2]/'src/specmind_eval/evaluate'
    for old,new in [('metrics.py','metrics.py'),('candidate.py','candidate.py')]:
        a=ast.parse((ROOT/old).read_text(encoding='utf8'));b=ast.parse((active/new).read_text(encoding='utf8'))
        a.body=[n for n in a.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
        b.body=[n for n in b.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
        assert ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)


def test_flat_runtime_also_blocks_unfiltered_rebound_corpus(tmp_path,monkeypatch):
    import json,importlib
    monkeypatch.syspath_prepend(str(ROOT.parent))
    loader=importlib.import_module('paper_eval_v2.prepare_mutants').load_prepared_mutants
    (tmp_path/'manifest.json').write_text(json.dumps({'schema':'prepared-mutants-v1','complete':True,'runtime_filter_required':True}))
    with pytest.raises(ValueError,match='Runtime filtering required'):loader(tmp_path,tmp_path,{})
