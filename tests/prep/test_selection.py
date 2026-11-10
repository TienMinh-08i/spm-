import pytest
from specmind_prep.selection import validate_selection


def test_full_selection_preserves_evidence_and_accumulates_errors():
    original = {'function_id': 'p.f', 'status': 'documented', 'commit': 'abc',
                'document': 'Returns x', 'document_link': 'p.html#p.f', 'docstring': 'Returns x'}
    selection = [dict(original, decision='keep', reason='Chosen API', target=None)]
    index = {'p.f': {'problem_id': 'p.f'}}
    project = {'name': 'p', 'python_path': ['src']}
    assert len(validate_selection([original], selection, index, project)) == 1
    selection[0].update(document='Forged', docstring='Forged', target='p.helper')
    with pytest.raises(ValueError) as info:
        validate_selection([original], selection, index, project)
    assert 'field document' in str(info.value) and 'field docstring' in str(info.value)
    assert 'Target must be null' in str(info.value)


def test_unknown_duplicate_and_delegate_are_all_reported():
    rows = [{'function_id': 'p.f', 'status': 'documented'}]
    selection = [{'function_id': 'p.missing', 'decision': 'keep', 'reason': 'x', 'target': None},
                 {'function_id': 'p.f', 'status': 'documented', 'decision': 'delegate', 'reason': 'x', 'target': None}]
    with pytest.raises(ValueError) as info:
        validate_selection(rows, selection, {}, {})
    assert 'Unknown' in str(info.value) and 'Delegate target' in str(info.value)


def test_delegates_share_source_but_never_task_or_api_spec():
    rows = [{'function_id': f'p.{name}', 'status': 'documented', 'docstring': name,
             'document': name+' contract'} for name in ('a', 'b')]
    selection = [dict(r, decision='delegate', reason=None, target='p._helper') for r in rows]
    index = {'p._helper': {'problem_id': 'p._helper', 'source': 'def _helper(x): return x+1',
                          'entry_point': '_helper', 'module': 'p', 'line': 10}}
    result = validate_selection(rows, selection, index, {'name': 'p', 'python_path': []})
    assert [r['problem_id'] for r in result] == ['p._helper', 'p._helper']
    assert {r['task_id'] for r in result} == {'p/p.a', 'p/p.b'}
    assert {r['document'] for r in result} == {'a contract', 'b contract'}
    assert all(r['entry_point'] == '_helper' and r['line'] == 10 for r in result)


@pytest.mark.parametrize('target', [None, 'p.a', 'p.missing'])
def test_delegate_rejects_missing_self_and_unknown_target(target):
    rows = [{'function_id': 'p.a', 'status': 'documented'}]
    selection = [dict(rows[0], decision='delegate', reason=None, target=target)]
    with pytest.raises(ValueError, match='Delegate target'):
        validate_selection(rows, selection, {'p.a': {'problem_id': 'p.a'}}, {'name': 'p', 'python_path': []})


def test_final_selection_cannot_silently_omit_eligible_api():
    rows = [{'function_id': f'p.{name}', 'status': 'documented'} for name in ('a', 'b')]
    selection = [dict(rows[0], decision='keep', reason=None, target=None)]
    with pytest.raises(ValueError, match='missing=.*p.b'):
        validate_selection(rows, selection, {'p.a': {'problem_id': 'p.a'}}, {'name': 'p', 'python_path': []})


def test_eligibility_requires_explicit_complete_evidence():
    from specmind_prep.selection import reviewed_inventory
    rows = [{'function_id': 'p.a', 'status': 'documented'},
            {'function_id': 'p.b', 'status': 'missing_document'}]
    review = [{'function_id': 'p.a', 'included': True, 'reason': 'Own public docs anchor'},
              {'function_id': 'p.b', 'included': False, 'reason': 'No function anchor'}]
    assert reviewed_inventory(rows, review) == rows[:1]
    with pytest.raises(ValueError, match='every inventory'):
        reviewed_inventory(rows, review[:1])
    review[1]['included'] = True
    with pytest.raises(ValueError, match='Unresolved'):
        reviewed_inventory(rows, review)


def test_trace_does_not_collapse_delegated_api_calls(tmp_path, monkeypatch):
    from specmind_prep.trace import trace as tracing
    from specmind_eval.common.jsonl import write_jsonl
    candidates = tmp_path/'candidates'
    candidates.mkdir()
    write_jsonl(candidates/'problems.jsonl', [{'decision': 'delegate', 'problem_id': 'p.helper'}])
    monkeypatch.setattr(tracing, 'validate_candidates', lambda *a: {'project': {}})
    with pytest.raises(ValueError, match='API-specific call bindings'):
        tracing.trace(candidates, tmp_path/'repo', tmp_path/'links')
    assert not (tmp_path/'links').exists()


def test_reviewed_selection_seals_policy_and_revalidates(tmp_path, monkeypatch):
    from specmind_prep import selection as module
    from specmind_eval.common.jsonl import write_jsonl
    from specmind_eval.common.storage import read
    repo, docs = tmp_path/'repo', tmp_path/'docs'
    repo.mkdir()
    docs.mkdir()
    rows = [{'function_id': 'p.api', 'status': 'documented', 'docstring': 'API contract'}]
    index = {'p._helper': {'problem_id': 'p._helper', 'source': 'def _helper(x): return x+1'},
             'p.api': {'source':'def api(x): return _helper(x)', 'repo_path':'p.py', 'code_line':1}}
    (repo/'p.py').write_text('def api(x): return _helper(x)')
    project = {'name': 'p', 'commit': 'abc', 'python_path': []}
    monkeypatch.setattr(module, 'load_project', lambda *a, **k: project)
    monkeypatch.setattr(module, 'revision', lambda *a: 'abc')
    monkeypatch.setattr(module, 'validate_docs', lambda *a: {'output_digest': 'docs'})
    monkeypatch.setattr(module, 'collect_inventory', lambda *a: (rows, index))
    monkeypatch.setattr(module, 'source_environment', lambda *a: {})
    selected, eligibility = tmp_path/'selection.jsonl', tmp_path/'eligibility.jsonl'
    write_jsonl(selected, [dict(rows[0], decision='delegate', reason=None, target='p._helper')])
    write_jsonl(eligibility, [{'function_id': 'p.api', 'included': True, 'reason': 'Own public anchor'}])
    output = tmp_path/'candidates'
    summary = module.prepare_selection(repo, tmp_path/'project.json', docs, selected, output,
                                       eligibility_path=eligibility)
    assert summary['decisions'] == {'keep': 0, 'delegate': 1, 'drop': 0}
    manifest = read(output/'manifest.json')
    module.validate_selection_artifact(output, repo, manifest)
    manifest['selection_complete'] = False
    with pytest.raises(ValueError, match='fingerprint'):
        module.validate_selection_artifact(output, repo, manifest)


def test_collection_selection_is_separate_from_project_facts(tmp_path, monkeypatch):
    from specmind_prep.filter import dataset as module
    from specmind_prep.gather import gather as gathering
    from specmind_prep.trace import trace as tracing
    project = {'name': 'p', 'tests': ['tests'], 'python_path': ['src']}
    selected = ['tests/test_p.py::test_f']
    parent = {'project': project, 'test_selection': selected, 'fingerprint': 'candidate', 'files': {}}
    link = {'fingerprint': 'links', 'files': {}}
    monkeypatch.setattr(gathering, 'validate_candidates', lambda *a: parent)
    monkeypatch.setattr(tracing, 'validate_links', lambda *a: link)
    from specmind_eval.common.storage import write
    write(tmp_path/'links/command.json', {'argv':['python','-p','specmind_prep.injected.call_trace_plugin',*selected]})
    link['test_selection']=selected
    captured = {}
    def prepare(*a, **kwargs):
        captured.update(kwargs)
        return {}
    monkeypatch.setattr(module, 'prepare_dataset', prepare)
    module.prepare_linked_dataset(tmp_path/'candidates', tmp_path/'links', tmp_path/'repo', tmp_path/'dataset')
    assert captured['project']['tests'] == ['tests']
    assert captured['collection_tests'] == selected
    assert captured['lineage']['candidate_fingerprint'] == 'candidate'


def test_validate_cli_rejects_individually_valid_but_unrelated_dataset(tmp_path, monkeypatch, capsys):
    from specmind_prep.cli import main
    from specmind_prep.gather import gather as gathering
    from specmind_eval.inputs import prepared
    monkeypatch.setattr(gathering, 'validate_candidates', lambda *a: {'fingerprint': 'right', 'files': {}})
    monkeypatch.setattr(prepared, 'load_prepared_dataset', lambda *a: ([], {}, {
        'lineage': {'candidate_fingerprint': 'wrong', 'candidate_files': {}}}))
    assert main(['validate', '--repo', str(tmp_path/'repo'), '--candidates', str(tmp_path/'candidates'),
                 '--dataset', str(tmp_path/'dataset')]) == 1
    assert 'does not belong' in capsys.readouterr().err


def test_drop_rows_do_not_consume_kept_function_limit():
    rows = [{'function_id': f'p.f{i}', 'status': 'documented'} for i in range(6)]
    selection = [dict(r, decision='keep' if i == 0 else 'drop', reason='Explicit decision', target=None)
                 for i, r in enumerate(rows)]
    index = {'p.f0': {'problem_id': 'p.f0'}}
    assert len(validate_selection(rows, selection, index, {'name': 'p', 'python_path': []}, limit=1)) == 1
