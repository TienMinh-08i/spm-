"""Offline dataset preparation, checkpoints and read-only artifact validation."""
from pathlib import Path
from time import perf_counter

from .dataset import load, function_code, pytest_nodeid, repo_path_for_problem
from paper_eval_v2.common.jsonl import read_jsonl
from .dataset_filter import filter_tests
from paper_eval_v2.common.outputs import separate_output
from .evaluation import Evaluator
from .prepare_tests import prepare_tests
from paper_eval_v2.common.fingerprint import digest
from paper_eval_v2.common.storage import read, write, run_lock

SCHEMA = 'prepared-dataset-v1'


from .common.fingerprint import file_hash, source_environment
from .common.jsonl import write_jsonl

def prepare_dataset(problems, tests, repo, output, *, resume=False, tasks=None,
                    timeout=120, batch_size=500, force=False):
    problems, tests, repo = (Path(p).resolve() for p in (problems, tests, repo))
    output = separate_output(output, repo, problems.parent, tests.parent)
    if timeout < 0 or batch_size < 1:
        raise ValueError('Invalid timeout or batch size')
    candidates, _ = load(problems, tests)
    if tasks:
        wanted = set(tasks)
        candidates = [p for p in candidates if (p.get('problem_id') or p['task_id']) in wanted]
        if len(candidates) != len(wanted):
            raise ValueError('Unknown or duplicate function selection')
    if not candidates:
        raise ValueError('No functions to prepare')
    env = source_environment(repo)
    implementation = {n: file_hash(Path(__file__).parent / n) for n in
                      ('prepare_dataset.py', 'prepare_tests.py', 'dataset_filter.py', 'pytest_plugin.py',
                       'execution.py', 'dataset.py', 'evaluation.py', 'common/fingerprint.py', 'common/storage.py', 'common/jsonl.py', 'common/outputs.py')}
    fingerprint = digest({'problems': file_hash(problems), 'tests': file_hash(tests),
                          'candidates': candidates, 'environment': env, 'implementation': implementation,
                          'timeout': timeout, 'batch_size': batch_size})
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous and not resume:
            raise ValueError('Output exists; use --resume or a new directory')
        if previous and previous['fingerprint'] != fingerprint:
            raise ValueError('Preparation fingerprint mismatch; use a new directory')
        manifest = {'schema': SCHEMA, 'fingerprint': fingerprint, 'complete': False,
                    'source_environment': env, 'inputs': {'problems': str(problems), 'tests': str(tests), 'repo': str(repo)},
                    'implementation': implementation, 'settings': {'timeout': timeout, 'batch_size': batch_size},
                    'planned_functions': len(candidates), 'files': {}}
        write(output/'manifest.json', manifest)
        collection = output/'collection'
        if not previous:
            prepare_tests(tests, repo, collection)
        if not (collection/'summary.json').exists():
            # Collection may have failed before its checkpoint was created.
            if collection.exists():
                raise ValueError('Incomplete collection directory; use a new output')
            prepare_tests(tests, repo, collection)
        linked = {}
        for row in read_jsonl(collection/'tests_valid.jsonl'):
            linked.setdefault(row['problem_id'], {})[pytest_nodeid(row)] = row
        results, prepared_problems, prepared_tests = [], [], []
        config = {'pytest_timeout': timeout, 'batch_size': batch_size,
                  '_evaluation_identity': fingerprint, '_force': force}
        for index, candidate in enumerate(candidates, 1):
            key = candidate.get('problem_id') or candidate['task_id']
            directory = output/'functions'/digest(key)[:16]
            saved = read(directory/'result.json')
            if saved and saved.get('done') and not force:
                result = saved
                print(f'[{index}/{len(candidates)}] reused {key}', flush=True)
            else:
                print(f'[{index}/{len(candidates)}] prepare {key}', flush=True)
                started = perf_counter()
                original = dict(candidate, _expected_source=function_code(candidate))
                path = repo_path_for_problem(original, repo)
                if not path or repo not in (repo/path).resolve().parents or not (repo/path).is_file():
                    result = {'function': key, 'status': 'excluded', 'done': True, 'tests': [],
                              'reason': 'Target source path not found', 'requested': 0, 'decisions': []}
                else:
                    original['_expected_source_path'] = str((repo/path).resolve())
                    rows = list(linked.get(key, {}).values())
                    write(directory/'input.json', {'problem': original, 'tests': rows})
                    if rows:
                        evaluator = Evaluator(original, rows, repo, directory, config)
                        result = filter_tests(evaluator)
                        result['function'] = key
                    else:
                        result = {'function': key, 'status': 'excluded', 'done': True, 'tests': [],
                                  'reason': 'No collectable linked tests', 'requested': 0, 'decisions': []}
                result['seconds'] = perf_counter()-started
                write(directory/'result.json', result)
            results.append(result)
            if result['status'] == 'ready':
                prepared_problems.append(candidate)
                prepared_tests.extend(result['tests'])
            summary = {'planned_functions': len(candidates), 'processed_functions': len(results),
                       'ready_functions': sum(r['status'] == 'ready' for r in results),
                       'excluded_functions': sum(r['status'] == 'excluded' for r in results),
                       'pending_functions': sum(not r['done'] for r in results),
                       'selected_links': len(prepared_tests),
                       'unique_tests': len({pytest_nodeid(t) for t in prepared_tests}),
                       'output': str(output)}
            write(output/'progress.json', summary)
        if source_environment(repo) != env:
            raise ValueError('Source or environment changed during preparation; use new output')
        write(output/'results.json', results)
        write_jsonl(output/'problems.jsonl', prepared_problems)
        write_jsonl(output/'tests.jsonl', prepared_tests)
        write(output/'excluded_functions.json', [{'function': r['function'], 'reason': r['reason']}
                                                 for r in results if r['status'] == 'excluded'])
        write(output/'summary.json', summary)
        manifest.update(complete=all(r['done'] for r in results),
                        files={name: file_hash(output/name) for name in
                               ('problems.jsonl', 'tests.jsonl', 'results.json', 'excluded_functions.json', 'summary.json')})
        write(output/'manifest.json', manifest)
    return summary


def load_prepared_dataset(directory, repo):
    """Read/validate only. Never runs pytest, filters tests or creates mutants."""
    directory, repo = Path(directory).resolve(), Path(repo).resolve()
    manifest = read(directory/'manifest.json')
    if not manifest or manifest.get('schema') != SCHEMA or not manifest.get('complete'):
        raise ValueError('Dataset preparation missing or incomplete')
    if source_environment(repo) != manifest['source_environment']:
        raise ValueError('Prepared dataset source/environment mismatch')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or file_hash(directory/name) != expected:
            raise ValueError('Prepared dataset artifact mismatch: '+name)
    problems, tests = load(directory/'problems.jsonl', directory/'tests.jsonl')
    results = {r['function']: r for r in read(directory/'results.json')}
    for problem in problems:
        key = problem.get('problem_id') or problem['task_id']
        record = results.get(key, {})
        if record.get('status') != 'ready' or digest(tests.get(key, [])) != digest(record.get('tests', [])):
            raise ValueError('Prepared test evidence mismatch: '+key)
    return problems, tests, manifest
