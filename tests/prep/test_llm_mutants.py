import json
import pytest
from specmind_prep.llm_mutants import filter_response, messages


def test_filters_signature_duplicate_syntax_and_unchanged():
    variants = ['def f(x):\n return x+1\n', 'def f(y):\n return y+1\n',
                'def f(x):\n return x\n', 'def f(x):\n return x+1\n', 'def f(:\n']
    result = filter_response('def f(x):\n return x\n', json.dumps({'mutants': [
        {'function_source': v, 'reason': 'fixture'} for v in variants]}), 5)
    assert len(result['mutants']) == 1
    assert len(result['skipped']) == 4


def test_limit_and_invalid_response():
    with pytest.raises(ValueError, match='limit'):
        filter_response('def f(): pass', '{"mutants": [{}, {}]}', 1)
    with pytest.raises(ValueError, match='JSON object'):
        filter_response('def f(): pass', '{}', 1)


def test_prompt_does_not_include_tests_or_assertions():
    prompt = messages({'source': 'def f(x): return x', 'tests': ['secret_test'], 'assertion': 'secret_assert'}, 2)
    assert 'secret_test' not in str(prompt) and 'secret_assert' not in str(prompt)


def test_reuse_verified_response_without_paid_call(tmp_path, monkeypatch):
    from specmind_prep import llm_mutants as module
    from specmind_eval.common.storage import write
    from specmind_eval.common.fingerprint import digest, file_hash
    target = {'problem_id': 'p.f', 'source': 'def f(x):\n return x\n', 'repo_path': 'p.py'}
    dm = {'fingerprint': 'dataset', 'files': {}}
    parent = {'dataset_fingerprint': 'dataset', 'dataset_files': {}, 'fingerprint': 'scope', 'files': {}, 'project': {}}
    rows = [{'mutation_targets': [{'kind': 'self', 'target': 'p.f'}]}]
    monkeypatch.setattr(module, 'load_scope', lambda *a: (rows, [target], parent))
    monkeypatch.setattr(module, 'load_prepared_dataset', lambda *a: ([], {}, dm))
    monkeypatch.setattr(module, 'load_prepared_mutants', lambda *a: None)
    monkeypatch.setattr(module, 'source_environment', lambda *a: {})
    cache = tmp_path/'old'; cache.mkdir()
    slug = digest('p.f')[:16]
    write(cache/(slug+'.request.json'), {'model': 'fixture-model', 'messages': messages(target, 10), 'max_tokens': 7000})
    write(cache/(slug+'.response.json'), {'model': 'fixture-model', 'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'mutants': [{'function_source': 'def f(x):\n return x+1\n'}]})}}]})
    write(cache/'manifest.json', {'complete': True, 'generator': 'llm', 'files': {
        n: file_hash(cache/n) for n in (slug+'.request.json', slug+'.response.json')}})
    def forbidden(*a):
        pytest.fail('Unexpected paid request')
    result = module.prepare(tmp_path/'scope', tmp_path/'dataset', tmp_path/'repo', tmp_path/'out',
                            'fixture-model', max_targets=22, reuse_from=cache, call=forbidden)
    assert result['unique_mutants'] == 1
    assert result['new_requests_this_execution'] == 0
    assert result['reused_functions_this_execution'] == ['p.f']


def test_failed_target_is_pending_and_resume_never_reissues_request(tmp_path, monkeypatch):
    from specmind_prep import llm_mutants as module
    from specmind_eval.common.storage import read
    targets = [{'problem_id': 'p.'+n, 'source': f'def {n}(x):\n return x\n', 'repo_path': 'p.py'}
               for n in ('f', 'g')]
    dm = {'fingerprint': 'dataset', 'files': {}}
    parent = {'dataset_fingerprint': 'dataset', 'dataset_files': {}, 'fingerprint': 'scope', 'files': {}, 'project': {}}
    rows = [{'mutation_targets': [{'kind': 'self', 'target': t['problem_id']}]} for t in targets]
    monkeypatch.setattr(module, 'load_scope', lambda *a: (rows, targets, parent))
    monkeypatch.setattr(module, 'load_prepared_dataset', lambda *a: ([], {}, dm))
    monkeypatch.setattr(module, 'source_environment', lambda *a: {})
    calls = []
    def fake(prompt, model):
        calls.append(prompt)
        if len(calls) == 1:
            raise TimeoutError('fixture, no response saved')
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'mutants': [{'function_source': 'def g(x):\n return x+1\n'}]})}}]}
    out = tmp_path/'out'
    args = (tmp_path/'scope', tmp_path/'dataset', tmp_path/'repo', out, 'fixture')
    with pytest.raises(ValueError, match='incomplete'):
        module.prepare(*args, call=fake)
    assert len(calls) == 2
    checkpoints = read(out/'checkpoints.json')
    assert checkpoints['p.f']['status'] == 'pending' and checkpoints['p.g']['status'] == 'complete'
    assert read(out/'manifest.json')['complete'] is False
    with pytest.raises(ValueError, match='incomplete'):
        module.prepare(*args, call=lambda *a: pytest.fail('Paid retry forbidden'))
    assert read(out/'summary.json')['new_requests_this_execution'] == 0
