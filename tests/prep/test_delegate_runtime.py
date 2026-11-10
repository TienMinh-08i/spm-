"""Ownership tests: shared helpers and deferred factories must not leak mutations."""
from specmind_eval.common.storage import read
from specmind_eval.common.jsonl import write_jsonl
from specmind_eval.evaluate.evaluator import Evaluator
from specmind_prep.delegation import bind_delegates
from specmind_prep.trace import trace as tracing


def test_shared_deferred_helper_trace_and_mutation_are_api_specific(tmp_path, monkeypatch):
    repo = tmp_path/'repo'
    repo.mkdir()
    (repo/'p.py').write_text(
        'from functools import partial\nTA="a"\nTB="b"\n'
        'def helper(fn, tag):\n'
        '    if fn is None:\n        return partial(helper, tag=tag)\n'
        '    fn.mark = tag\n    return fn\n'
        'def a():\n    return helper(None, TA)\n'
        'def b(fn=None):\n    return helper(fn, TB)\n')
    (repo/'test_p.py').write_text(
        'from p import a,b\n'
        'def test_b():\n    b()(lambda: None)\n'
        'def test_mixed():\n'
        '    f = b()(lambda: None)\n    assert f.mark == "b"\n'
        '    a()(lambda: None)\n')
    project = {'name':'p','source_root':'.','packages':['p'], 'tests':['test_p.py'],'python_path':['.']}
    # source_index indexes package directories; use explicit records for this
    # single-module fixture, including real source locations from AST.
    import ast
    tree = ast.parse((repo/'p.py').read_text())
    index = {'p.'+n.name:dict(problem_id='p.'+n.name,module='p',parent_name=None,entry_point=n.name,
                            source=ast.unparse(n)+'\n',repo_path='p.py',line=n.lineno,code_line=n.lineno,
                            decorators=[],async_=False)
             for n in tree.body if isinstance(n,ast.FunctionDef)}
    problems = [dict(index['p.helper'],function_id='p.'+n,task_id='p/'+n,decision='delegate') for n in ('a','b')]
    bind_delegates(problems,index,repo)
    candidates = tmp_path/'candidates'
    candidates.mkdir()
    write_jsonl(candidates/'problems.jsonl',problems)
    monkeypatch.setattr(tracing,'validate_candidates',lambda *a:{'project':project,'fingerprint':'fixture',
                                                              'files':{},'repo_commit':None})
    tracing.trace(candidates,repo,tmp_path/'links',tests=['test_p.py'],timeout=30)
    evidence = read(tmp_path/'links/trace.json')
    assert evidence['links']['test_p.py::test_b'] == ['p.b']
    assert evidence['links']['test_p.py::test_mixed'] == ['p.a','p.b']
    problem = dict(problems[0],_expected_source=index['p.helper']['source'],
                   _expected_source_path=str(repo/'p.py'))
    evaluator = Evaluator(problem,[{'problem_id':'p.a','nodeid':'test_p.py::test_mixed'}],repo,tmp_path/'eval',
                          {'pytest_timeout':30,'batch_size':1,'python_path':['.'],'_evaluation_identity':'fixture'})
    assertion = 'assert fn is None or return_value.mark == tag'
    baseline = evaluator.arm(assertion)
    assert baseline['status'] == 'pass'
    mutant = {'function_source':index['p.helper']['source'].replace('fn.mark = tag', 'fn.mark = "wrong"')}
    arm = evaluator.arm(assertion,mutant)
    assert arm['status'] == 'fail'
    assert sum(c['violations'] for c in arm['cases'].values()) == 1
    # The b assertion in the pytest body runs first. Reaching a's postcondition
    # violation proves b was neither mutated nor checked against a's contract.
    assert sum(c['mutation_calls'] for c in arm['cases'].values()) == 2


def test_declared_application_state_does_not_enable_python_introspection():
    import pytest
    from specmind_eval.evaluate.policy import validate
    source = 'def hook(fn):\n    fn.__app_state__ = 1\n    return fn\n'
    assertion = 'assert return_value.__app_state__ == 1'
    with pytest.raises(ValueError, match='dunder'):
        validate(assertion,source,'hook','behavioral')
    validate(assertion,source,'hook','behavioral',state_attributes=['__app_state__'])
    with pytest.raises(ValueError, match='dunder'):
        validate('assert fn.__code__ is not None',source,'hook','behavioral',state_attributes=['__code__'])
    with pytest.raises(ValueError, match='dunder'):
        validate('assert fn.__other_state__ == 1',source,'hook','behavioral',state_attributes=['__other_state__'])
