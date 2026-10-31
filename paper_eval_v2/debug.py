"""Narrow, offline debug entry points. Never imports or calls the LLM client."""
from pathlib import Path
from .candidate import evaluate
from .dataset import function_code, pytest_nodeid
from paper_eval_v2.common.jsonl import read_jsonl
from .evaluation import Evaluator
from .metrics import mutation_metrics
from paper_eval_v2.common.fingerprint import digest
from paper_eval_v2.common.storage import read, write, run_lock




from .common.outputs import separate_output

def nodeids(tests, output):
    rows = [{'test_id': r.get('test_id'), 'nodeid': pytest_nodeid(r)} for r in read_jsonl(Path(tests))]
    if Path(output).resolve() == Path(tests).resolve():
        raise ValueError('Do not overwrite the source dataset')
    write(output, rows)
    return {'rows': len(rows), 'output': str(output)}


def replay(run, function, output, *, action='attempt', attempt=None, assertion_file=None,
           mutant=None, nodeid=None, force=False):
    run = Path(run).resolve()
    manifest = read(run / 'manifest.json')
    if not manifest or 'config' not in manifest:
        raise ValueError('Missing v2 manifest')
    config = dict(manifest['config'])
    repo = Path(config['repo_root'])
    output = separate_output(output, run, repo)
    directory = run / 'functions' / digest(function)[:16]
    data, bundle = read(directory / 'input.json'), read(directory / 'mutants.json')
    if not data:
        raise ValueError('No saved input for ' + function)
    if action == 'metrics':
        result = read(directory / 'result.json', {})
        if not result.get('mutation'):
            raise ValueError('No saved mutant evidence')
        metrics = mutation_metrics(result['mutation']['mutants'])
        with run_lock(output):
            write(output / 'metrics.json', metrics)
        return metrics
    state = read(directory / 'generation.json', {})
    selected = None
    if attempt is not None:
        selected = next((a for a in state.get('attempts', []) if a['turn'] == attempt), None)
        if selected is None:
            raise ValueError('Unknown attempt')
    else:
        selected = state.get('best') or state.get('latest')
    assertion = Path(assertion_file).read_text(encoding='utf-8') if assertion_file else (selected or {}).get('assertion')
    if not assertion:
        raise ValueError('Provide --assertion-file or a saved --attempt with an assertion')
    mutants = (bundle or {}).get('mutants', [])
    if action == 'mutant' and mutant is None:
        raise ValueError('debug mutant requires --mutant')
    if mutant is not None:
        mutants = [m for m in mutants if str(m['id']) == str(mutant)]
        if len(mutants) != 1:
            raise ValueError('Unknown or ambiguous mutant ID')
    tests = data['tests']
    if nodeid:
        tests = [t for t in tests if pytest_nodeid(t) == nodeid]
        if not tests:
            raise ValueError('Nodeid not in saved linked tests')
    config.update(_force=force, _read_cache=[directory / 'arms'])
    with run_lock(output):
        ev = Evaluator(data['problem'], tests, repo, output, config)
        signature = digest({'input': data, 'tests': tests, 'mutants': mutants, 'assertion': assertion,
                            'identity': ev.cache_identity, 'policy': config.get('assertion_policy', 'legacy')})
        previous = read(output / 'debug.json')
        if previous and previous['fingerprint'] != signature:
            raise ValueError('Debug output already belongs to a different request; choose a new output')
        write(output / 'debug.json', {'fingerprint': signature, 'source_run': str(run), 'function': function,
                                     'attempt': attempt, 'force': force, 'partial_test_scope': bool(nodeid),
                                     'mutant': mutant, 'evaluation_identity': ev.cache_identity})
        if config.get("prepared_dataset"):
            ev.eligible = [pytest_nodeid(t) for t in tests]
            ev.ready = bool(tests)
            preflight = {"status": "pass", "origin": "prepared-dataset", "eligible_nodeids": ev.eligible, "reason": None}
        else:
            preflight = ev.preflight()
        correctness, mutation = ({'status': 'unavailable', 'reason': preflight['reason']}, None)
        if preflight['status'] == 'pass':
            correctness, mutation = evaluate(assertion, function_code(data['problem']),
                                            data['problem']['entry_point'], ev, mutants, config)
        result = {'function': function, 'assertion': assertion, 'preflight': preflight,
                  'correctness': correctness, 'mutation': mutation, 'partial_test_scope': bool(nodeid)}
        write(output / 'result.json', result)
    return {'correctness': correctness['status'], 'mutation': (mutation or {}).get('metrics'),
            'output': str(output), 'partial_test_scope': bool(nodeid)}
