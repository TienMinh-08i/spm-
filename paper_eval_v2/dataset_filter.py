"""Two-probe filtering for dataset preparation only; no LLM or mutants."""
from .dataset import pytest_nodeid
from paper_eval_v2.common.storage import write


def filter_tests(evaluator):
    requested = list(evaluator.tests)
    positive = evaluator.arm('assert True')
    true_cases = positive['cases']
    passed = {n for n, c in true_cases.items() if c['passed']}
    evaluator.tests = [t for t in requested if pytest_nodeid(t) in passed]
    negative = evaluator.arm('assert False') if evaluator.tests and positive['status'] in {'pass', 'fail'} else None
    false_cases = negative['cases'] if negative else {}
    rows, selected = [], []
    for test in requested:
        nodeid = pytest_nodeid(test)
        true, false = true_cases.get(nodeid, {}), false_cases.get(nodeid, {})
        observable = (nodeid in passed and false.get('violations', 0) > 0
                      and not false.get('runtime_errors', 0)
                      and false.get('reports', {}).get('call') == 'failed'
                      and all(false.get('reports', {}).get(p) == 'passed' for p in ('setup', 'teardown')))
        reason = ('selected' if observable else 'true_not_passed' if nodeid not in passed
                  else 'false_not_executed' if not false else 'postcondition_runtime_error'
                  if false.get('runtime_errors') else 'false_setup_or_teardown_failed'
                  if any(false.get('reports', {}).get(p) != 'passed' for p in ('setup', 'teardown'))
                  else 'assertion_not_reached' if not false.get('checks')
                  else 'false_did_not_fail_test' if false.get('reports', {}).get('call') != 'failed'
                  else 'failure_not_postcondition')
        rows.append({'nodeid': nodeid, 'selected': bool(observable), 'reason': reason,
                     'true': true, 'false': false})
        if observable:
            selected.append(test)
    complete = positive['status'] in {'pass', 'fail'} and (negative is None or negative['status'] in {'pass', 'fail'})
    status = 'ready' if complete and selected else 'excluded' if complete else 'pending'
    # Partial probes are debug evidence only; never publish partial selections.
    evaluator.tests = selected if status == 'ready' else []
    evaluator.eligible = [pytest_nodeid(t) for t in evaluator.tests]
    evaluator.ready = status == 'ready'
    result = {'status': status, 'done': status != 'pending', 'requested': len(requested),
              'true_pass_cases': len(passed), 'selected_cases': len(evaluator.tests),
              'eligible_nodeids': evaluator.eligible, 'true_status': positive['status'],
              'false_status': negative['status'] if negative else 'not_run',
              'reason': None if status == 'ready' else 'No observable postcondition tests' if complete else 'Probe incomplete',
              'tests': evaluator.tests, 'decisions': rows}
    errors = [e.get('message') for e in positive['detail'].get('events', []) if e.get('kind') == 'hook_install_error']
    if errors:
        result.update(status='excluded', done=True, reason='; '.join(dict.fromkeys(errors)), tests=[], selected_cases=0,
                      eligible_nodeids=[])
    write(evaluator.output / 'filter.json', result)
    return result
