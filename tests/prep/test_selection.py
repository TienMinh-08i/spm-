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
    assert 'delegation is unsupported' in str(info.value)


def test_unknown_duplicate_and_delegate_are_all_reported():
    rows = [{'function_id': 'p.f', 'status': 'documented'}]
    selection = [{'function_id': 'p.missing', 'decision': 'keep', 'reason': 'x', 'target': None},
                 {'function_id': 'p.f', 'status': 'documented', 'decision': 'delegate', 'reason': 'x', 'target': None}]
    with pytest.raises(ValueError) as info:
        validate_selection(rows, selection, {}, {})
    assert 'Unknown' in str(info.value) and 'keep/drop' in str(info.value)


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
