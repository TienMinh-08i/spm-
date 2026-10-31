import json
import pytest

from paper_eval_v2.prepare_dataset import prepare_dataset
from paper_eval_v2.inputs.prepared import load_prepared_dataset
from paper_eval_v2.common.storage import read


@pytest.fixture
def inputs(tmp_path):
    repo = tmp_path/'repo'
    repo.mkdir()
    source = 'def f(x=0):\n    if x < 0:\n        raise ValueError("bad")\n    return x+1\n'
    (repo/'target.py').write_text(source)
    (repo/'test_f.py').write_text(
        'import pytest\nfrom target import f\n'
        'def test_good():\n    assert f() == 1\n'
        'def test_uncalled():\n    assert True\n'
        'def test_raises():\n    with pytest.raises(ValueError):\n        f(-1)\n'
        'def test_swallowed():\n    try:\n        f()\n    except AssertionError:\n        pass\n'
        'def test_bad():\n    assert f() == 999\n'
    )
    data = tmp_path/'raw'
    data.mkdir()
    problem = {'problem_id': 'target.f', 'module': 'target', 'entry_point': 'f', 'source': source}
    (data/'problems.jsonl').write_text(json.dumps(problem)+'\n')
    rows = [{'problem_id': 'target.f', 'nodeid': 'test_f.py::test_'+name}
            for name in ['good', 'uncalled', 'raises', 'swallowed', 'bad', 'missing']]
    (data/'tests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return data/'problems.jsonl', data/'tests.jsonl', repo, tmp_path/'prepared'


def test_offline_prepare_resume_validate_and_tampering(inputs, monkeypatch):
    summary = prepare_dataset(*inputs)
    assert summary['ready_functions'] == 1 and summary['selected_links'] == 1
    output, repo = inputs[-1], inputs[-2]
    collection = read(output/'collection/summary.json')
    assert collection['invalid_links'] == 1
    record = read(output/'results.json')[0]
    assert record['true_pass_cases'] == 4
    decisions = {r['nodeid']: r['reason'] for r in record['decisions']}
    assert decisions['test_f.py::test_good'] == 'selected'
    assert decisions['test_f.py::test_bad'] == 'true_not_passed'
    assert decisions['test_f.py::test_swallowed'] == 'false_did_not_fail_test'
    def forbidden(*a, **k):
        pytest.fail('Read-only loader/resume ran pytest')
    monkeypatch.setattr('paper_eval_v2.evaluation.run_pytest_postcondition', forbidden)
    monkeypatch.setattr('paper_eval_v2.prepare_dataset.prepare_tests', forbidden)
    assert prepare_dataset(*inputs, resume=True)['selected_links'] == 1
    problems, tests, manifest = load_prepared_dataset(output, repo)
    assert len(problems) == 1 and len(tests['target.f']) == 1 and manifest['complete']
    with pytest.raises(ValueError, match='Output exists'):
        prepare_dataset(*inputs)
    with pytest.raises(ValueError, match='fingerprint mismatch'):
        prepare_dataset(*inputs, resume=True, batch_size=2)
    (output/'tests.jsonl').write_text('')
    with pytest.raises(ValueError, match='artifact mismatch'):
        load_prepared_dataset(output, repo)


def test_loader_rejects_incomplete_and_source_change(inputs):
    prepare_dataset(*inputs)
    output, repo = inputs[-1], inputs[-2]
    (repo/'target.py').write_text('def f(): return 999\n')
    with pytest.raises(ValueError, match='source/environment mismatch'):
        load_prepared_dataset(output, repo)


def test_pending_probe_does_not_publish_partial_dataset(inputs, monkeypatch):
    def timeout(*a, **kw):
        return 'timeout', {'events': [], 'logs': ['TIMEOUT']}
    monkeypatch.setattr('paper_eval_v2.evaluation.run_pytest_postcondition', timeout)
    summary = prepare_dataset(*inputs)
    assert summary['pending_functions'] == 1 and summary['selected_links'] == 0
    assert not read(inputs[-1]/'manifest.json')['complete']
    with pytest.raises(ValueError, match='incomplete'):
        load_prepared_dataset(inputs[-1], inputs[-2])


def test_output_cannot_overwrite_raw_dataset(inputs):
    with pytest.raises(ValueError, match='separate'):
        prepare_dataset(*inputs[:-1], inputs[0].parent/'nested')


def test_overloads_and_import_setup_calls_do_not_break_false_probe(inputs):
    repo = inputs[-2]
    source = (repo/'target.py').read_text()
    (repo/'target.py').write_text(
        'from typing import overload\n@overload\ndef f(x: int = 0) -> int: ...\n'
        '@overload\ndef f(x: str) -> str: ...\n'+source)
    (repo/'test_f.py').write_text(
        'import pytest\nfrom target import f\nf()\n'
        '@pytest.fixture(autouse=True)\ndef setup():\n    f()\n'
        'def test_good():\n    assert f() == 1\n')
    summary = prepare_dataset(*inputs)
    assert summary['ready_functions'] == 1 and summary['selected_links'] == 1
    result = read(inputs[-1]/'results.json')[0]
    assert result['decisions'][0]['true']['checks'] == 1
    assert result['decisions'][0]['false']['violations'] == 1
