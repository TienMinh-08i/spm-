import pytest
from specmind_prep.prototype import Sections, selected_candidates


def test_html_sections_do_not_mix_siblings():
    p = Sections()
    p.feed('<dl><dt class="sig-object" id="p.f">f(x)</dt><dd>Hello <b>world</b></dd></dl>'
           '<dl><dt class="sig-object" id="p.g">g()</dt><dd>Other</dd></dl>')
    assert p.sections == {'p.f': 'f(x) Hello world', 'p.g': 'g() Other'}


@pytest.mark.parametrize('selection,message', [
    ([{'function_id': 'p.f', 'decision': 'delegate', 'reason': 'x'}], 'Delegate target'),
    ([{'function_id': 'p.f', 'decision': 'drop', 'reason': ''}], 'reason'),
    ([{'function_id': 'p.missing', 'decision': 'keep', 'reason': 'x'}], 'Unknown'),
    ([{'function_id': 'p.f', 'decision': 'keep', 'reason': 'x'}]*2, 'Duplicate'),
])
def test_invalid_selection(selection, message):
    with pytest.raises(ValueError, match=message):
        selected_candidates([{'function_id': 'p.f', 'status': 'documented'}], selection,
                            {'p.f': {'problem_id': 'p.f'}}, {'name': 'p', 'python_path': ['src']})


def test_pending_eligibility_cannot_be_kept():
    with pytest.raises(ValueError, match='eligibility'):
        selected_candidates([{'function_id': 'p.f', 'status': 'review'}],
                            [{'function_id': 'p.f', 'decision': 'keep', 'reason': 'x'}], {}, {})


def test_larger_selection_requires_explicit_limit():
    rows = [{'function_id': f'p.f{i}', 'status': 'documented'} for i in range(22)]
    selection = [{'function_id': r['function_id'], 'decision': 'keep', 'reason': 'authorized run'} for r in rows]
    index = {r['function_id']: {'problem_id': r['function_id']} for r in rows}
    project = {'name': 'p', 'python_path': ['src']}
    with pytest.raises(ValueError, match='1 to 5'):
        selected_candidates(rows, selection, index, project)
    assert len(selected_candidates(rows, selection, index, project, limit=22)) == 22


def test_mutant_filter_rejects_signature_syntax_unchanged_and_duplicate(monkeypatch):
    import sys
    from types import SimpleNamespace
    from importlib import import_module
    variants = ['def f(x):\n return x+1\n', 'def f(y):\n return y+1\n',
                'def f(x):\n return x\n', 'def f(x):\n return x+1\n', 'def f(:\n']
    fake = SimpleNamespace(__version__='2.5.1', Context=lambda **kw: kw,
                           list_mutations=lambda ctx: list(range(len(variants))),
                           mutate=lambda ctx: (variants[ctx['mutation_id']], 1))
    monkeypatch.setitem(sys.modules, 'mutmut', fake)
    worker = import_module('specmind_prep.prototype_mutants')
    monkeypatch.setattr(worker, 'mutmut', fake)
    result = worker.export([{'problem_id': 'p.f', 'source': 'def f(x):\n return x\n'}])['p.f']
    assert result['discovered'] == 5
    assert len(result['mutants']) == 1
    assert len(result['skipped']) == 4
