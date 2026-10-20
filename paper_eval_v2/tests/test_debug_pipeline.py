import pytest
from paper_eval_v2.dataset import pytest_nodeid
from paper_eval_v2.policy import validate
from paper_eval_v2.specmind import generate
from paper_eval_v2.metrics import mutation_metrics
from paper_eval_v2.evaluation import Evaluator


@pytest.mark.parametrize('suffix', ['test_x[1.2]', 'C.test_x[01:23:45.123456+00:00]', 'C.Inner.test_x[a.b]'])
def test_nodeid_preserves_parameters(suffix):
    prefix, bracket, params = suffix.partition('[')
    assert pytest_nodeid({'test_id': 'tests.test_f::' + suffix}) == 'tests/test_f.py::' + prefix.replace('.', '::') + bracket + params


@pytest.mark.parametrize('assertion', [
    'import inspect\nassert inspect.signature(f)',
    'import inspect as i\nassert i.signature(f)',
    'assert f.__defaults__',
    'assert getattr(f, "__defaults__")',
    'assert return_value == f(x)',
    'x[0] = 1\nassert True',
    'assert x.append(1)',
    'assert globals()',
    'assert return_value.__class__',
    'if False:\n    assert False',
    'import math as return_value\nassert True',
])
def test_behavioral_blocks_metadata_and_mutation(assertion):
    with pytest.raises((ValueError, SyntaxError)):
        validate(assertion, 'def f(x): return x', 'f', 'behavioral')


@pytest.mark.parametrize('assertion', [
    'assert return_value == x + 1',
    'assert callable(return_value) and x is False',
    'import inspect\nassert return_value == inspect.isgenerator(x)',
    'from collections.abc import Mapping\nassert isinstance(return_value, Mapping)',
    'assert all(v >= 0 for v in return_value)',
    'expected = len(x)\nassert len(return_value) == expected',
])
def test_behavioral_accepts_pure_input_output(assertion):
    validate(assertion, 'def f(x): return x', 'f', 'behavioral')


def test_unknown_resume_reuses_response_and_updates_best(tmp_path):
    class Fake:
        unknown = True
        def correctness(self, assertion):
            return {'status': 'pass'}
        def mutation(self, assertion, mutants):
            rows = [{'id': '1', 'suite_killed': None, 'assertion_killed': None if self.unknown else True}]
            return {'metrics': mutation_metrics(rows), 'mutants': rows}
    ev = Fake()
    calls = []
    def call(*_):
        calls.append(1)
        return '<solution>assert return_value == 1</solution>', {}
    cfg = {'mode': 'multiturn', 'max_turns': 1, 'threshold': .9}
    args = ('def f(): return 1', 'f', ev, [{}], cfg, tmp_path/'generation.json', call)
    first = generate(*args)
    assert first['generation_done'] and not first['done'] and not first['evaluation_complete']
    ev.unknown = False
    second = generate(*args)
    assert second['done'] and second['best']['mutation']['metrics']['assertion']['killed'] == 1
    assert calls == [1]
    assert second['attempts'][0]['evaluation_retries'] == 1


def test_cache_normalizes_comments_but_not_tests_or_identity(tmp_path, monkeypatch):
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return 'pass', {'events': [], 'logs': ['full log']}
    monkeypatch.setattr('paper_eval_v2.evaluation.run_pytest_postcondition', run)
    config = {'pytest_timeout': 1, 'batch_size': 1, '_evaluation_identity': 'same'}
    ev = Evaluator({}, [{'test_id': 'a'}], tmp_path, tmp_path/'out', config)
    ev.arm('assert True')
    ev.arm('# changed comment\nassert True')
    assert len(calls) == 1
    changed = Evaluator({}, [{'test_id': 'b'}], tmp_path, tmp_path/'out', config)
    changed.arm('assert True')
    forced = Evaluator({}, [{'test_id': 'a'}], tmp_path, tmp_path/'out', dict(config, _force=True))
    forced.arm('assert True')
    assert len(calls) == 3
