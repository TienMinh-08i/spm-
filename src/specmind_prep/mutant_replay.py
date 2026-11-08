"""Replay one prepared mutant against one linked test, with a neutral assertion.

This checks installation/execution evidence; it never computes an assertion kill score.
"""
import argparse
import json
from pathlib import Path

from specmind_eval.common.fingerprint import digest, file_hash
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.storage import write
from specmind_eval.evaluate.evaluator import Evaluator
from specmind_eval.inputs.dataset import pytest_nodeid
from specmind_eval.inputs.prepared import load_prepared_dataset, load_prepared_mutants


def classify(arm):
    events = arm['detail'].get('events', [])
    cases = arm['cases'].values()
    if any(e.get('kind') == 'hook_install_error' for e in events):
        return 'installation_error'
    if arm['status'] == 'timeout':
        return 'timeout'
    if not sum(c['mutation_calls'] for c in cases):
        return 'not_reached'
    if arm['status'] == 'pass':
        return 'neutral_pass'
    if arm['status'] == 'fail':
        return 'test_failed_after_mutation'
    return 'execution_error'


def replay(dataset, mutants, repo, output, function, *, mutant_id=None, nodeid=None, timeout=60):
    dataset, mutants, repo = [Path(p).resolve() for p in (dataset, mutants, repo)]
    output = separate_output(output, dataset, mutants, repo)
    problems, tests, dm = load_prepared_dataset(dataset, repo)
    corpus, mm = load_prepared_mutants(mutants, repo, dm)
    problem = next((p for p in problems if p['problem_id'] == function), None)
    if problem is None or function not in corpus:
        raise ValueError('Unknown prepared function: '+function)
    pool = corpus[function]['mutants']
    mutant = next((m for m in pool if mutant_id is None or m['id'] == mutant_id), None)
    if mutant is None:
        raise ValueError('No matching prepared mutant')
    test = next((t for t in tests[function] if nodeid is None or pytest_nodeid(t) == nodeid), None)
    if test is None:
        raise ValueError('No matching linked test')
    if output.exists():
        raise ValueError('Replay output exists; choose a new directory')
    output.mkdir(parents=True)
    config = {'pytest_timeout': timeout, 'batch_size': 1,
              'python_path': dm.get('project', {}).get('python_path'),
              '_evaluation_identity': digest({'dataset': dm['fingerprint'], 'mutants': mm['fingerprint'],
                                              'replay_code': file_hash(__file__)})}
    evaluator = Evaluator(problem, [test], repo, output, config)
    baseline = evaluator.arm('assert True')
    covered = any(c['checks'] and c['passed'] for c in baseline['cases'].values())
    result = {'function': function, 'mutant_id': mutant['id'], 'nodeid': pytest_nodeid(test),
              'dataset_fingerprint': dm['fingerprint'], 'mutants_fingerprint': mm['fingerprint'],
              'baseline_status': baseline['status'], 'assertion': 'assert True',
              'meaning': 'Neutral execution evidence only; no assertion mutation score.'}
    if baseline['status'] != 'pass' or not covered:
        result.update(status='baseline_unavailable', mutation_calls=0)
    else:
        arm = evaluator.arm('assert True', mutant)
        result.update(status=classify(arm), mutant_status=arm['status'],
                      mutation_calls=sum(c['mutation_calls'] for c in arm['cases'].values()),
                      mutation_errors=sum(c['mutation_errors'] for c in arm['cases'].values()),
                      assertion_violations=sum(c['violations'] for c in arm['cases'].values()))
    write(output/'report.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'mutants', 'repo', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--function', required=True)
    parser.add_argument('--mutant-id')
    parser.add_argument('--nodeid')
    parser.add_argument('--timeout', type=int, default=60)
    print(json.dumps(replay(**vars(parser.parse_args())), indent=2))
