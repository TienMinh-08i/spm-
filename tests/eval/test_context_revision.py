from collections import defaultdict
from pathlib import Path
import ast
import json
import pytest

from paper_eval_v2.context import CallGraph, build_context_pack, callee_contract
from paper_eval_v2.candidate import refresh
from paper_eval_v2.specmind import generate
from paper_eval_v2.metrics import mutation_metrics


def test_callee_excludes_body_nested_docs_and_decorators():
    source = '    @classmethod\r\n    def f(cls, x=1):\r\n        """The public contract."""\r\n        secret = 987654\r\n        return secret\r\n'
    rendered = callee_contract(source)
    assert 'The public contract.' in rendered and 'x=1' in rendered
    assert '987654' not in rendered and '@classmethod' not in rendered
    node = ast.parse(rendered).body[0]
    assert len(node.body) == 2 and node.body[1].value.value is Ellipsis
    assert callee_contract('def f():\n    def g():\n        "nested doc"\n    return g') == ''


def test_runtime_usage_deduplicates_without_creating_edges():
    nodes = {'m.f': {'id': 'm.f', 'source': 'def f(): pass'},
             'm.g': {'id': 'm.g', 'source': 'def g(): return 17'},
             'tests/t.py::test_x': {'id': 'tests/t.py::test_x', 'kind': 'test', 'source': 'def test_x(): f()'}}
    g = CallGraph(nodes, [{'caller': 'm.f', 'callee': 'm.g', 'confidence': 'high'},
                          {'caller': 'tests/t.py::test_x', 'callee': 'm.f', 'confidence': 'high'}], 'hash')
    g.runtime_usages['m.f']['tests/t.py::test_x'] = {'source': 'def test_x(): f()',
        'observed_nodeids': ['tests/t.py::test_x'], 'observed_calls': 1}
    b = build_context_pack(g, 'm.f', mode='callee')
    assert b['metadata']['status'] == 'no_documented_callees'
    assert b['callees'] == [] and b['repository_context'] == ''
    c = build_context_pack(g, 'm.f', mode='caller')
    assert len(c['callers']) == 1
    assert 'possibly indirect' in c['repository_context']
    assert c['callers'][0]['evidence']['kind'] == 'runtime_test_usage'
    assert not build_context_pack(g, 'm.f', mode='none')['repository_context']


def mutation(outcome):
    rows = [{'id': 'm', 'suite_killed': None, 'assertion_killed': outcome}]
    return {'metrics': mutation_metrics(rows), 'mutants': rows}


def test_final_complete_not_blocked_by_historical_unknown():
    state = {'generation_done': True, 'attempts': [
        {'turn': 1, 'action': 'submit', 'correctness': {'status': 'pass'}, 'mutation': mutation(None)},
        {'turn': 2, 'action': 'submit', 'correctness': {'status': 'pass'}, 'mutation': mutation(True)}]}
    refresh(state, [{}])
    assert state['done'] and state['evaluation_complete']
    assert not state['history_evaluation_complete'] and state['best']['turn'] == 2


def test_resume_preserves_original_observation_and_no_new_llm(tmp_path):
    class Ev:
        outcome = None
        def correctness(self, _): return {'status': 'pass'}
        def mutation(self, *a): return mutation(self.outcome)
    ev = Ev()
    calls = []
    def llm(*a):
        calls.append(1)
        return '<solution>assert return_value == 1</solution>', {}
    cfg = {'mode': 'multiturn', 'max_turns': 1, 'threshold': .9}
    args = ('def f(): return 1', 'f', ev, [{}], cfg, tmp_path/'g.json', llm)
    first = generate(*args)
    original_history = first['history']
    ev.outcome = True
    second = generate(*args)
    assert second['done'] and calls == [1]
    assert second['history'] == original_history
    assert second['attempts'][0]['initial_evaluation']['mutation']['metrics']['feedback']['unknown'] == 1
    assert second['evaluation_updates'][0]['retry'] == 1


def test_no_mutants_stops_only_after_passing_submission(tmp_path):
    class Ev:
        def correctness(self, _): return {'status': 'pass'}
    responses = iter(['<assert>assert return_value == 1</assert>', '<solution>assert return_value == 1</solution>'])
    def llm(*a): return next(responses), {}
    cfg = {'mode': 'multiturn', 'max_turns': 24, 'threshold': .9}
    s = generate('def f(): return 1', 'f', Ev(), [], cfg, tmp_path/'g.json', llm)
    assert s['done'] and len(s['attempts']) == 2
    assert s['stop_reason'] == 'correctness_only_submission'


def test_empty_context_has_same_prompt_as_baseline(tmp_path):
    class Ev:
        def correctness(self, _): return {'status': 'pass'}
    prompts = []
    def llm(messages, cfg):
        prompts.append(messages[1]['content'])
        return '<solution>assert return_value == 1</solution>', {}
    for mode in ['none', 'callee']:
        generate('def f(): return 1', 'f', Ev(), [],
                 {'mode': 'multiturn', 'max_turns': 1, 'threshold': .9, 'context_mode': mode},
                 tmp_path/mode/'generation.json', llm)
    assert prompts[0] == prompts[1]


def test_runtime_usage_changes_graph_identity(tmp_path):
    import hashlib
    from paper_eval_v2.common.storage import write
    write(tmp_path/'nodes.json', [{'id': 'm.f', 'source': 'def f(): pass'}])
    write(tmp_path/'edges.json', [])
    usage = [{'function_id': 'm.f', 'nodeid': 'tests/t.py::test_x', 'file': 'tests/t.py',
              'source': 'def test_x(): f()', 'observed_nodeids': ['tests/t.py::test_x'], 'observed_calls': 1}]
    def publish():
        write(tmp_path/'runtime_usage.json', usage)
        write(tmp_path/'manifest.json', {'artifact_sha256': {
            n: hashlib.sha256((tmp_path/n).read_bytes()).hexdigest()
            for n in ['nodes.json', 'edges.json', 'runtime_usage.json']}})
    publish()
    before = CallGraph.load(tmp_path).digest
    usage[0]['observed_calls'] = 2
    publish()
    assert CallGraph.load(tmp_path).digest != before
    (tmp_path/'runtime_usage.json').write_text('[]')
    with pytest.raises(ValueError, match='checksum'):
        CallGraph.load(tmp_path)
