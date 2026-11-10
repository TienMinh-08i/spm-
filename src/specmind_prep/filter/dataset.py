"""Offline dataset preparation, checkpoints and read-only artifact validation."""
from pathlib import Path
from time import perf_counter

from specmind_eval.inputs.dataset import evaluation_key, load, function_code, pytest_nodeid, repo_path_for_problem
from specmind_eval.common.jsonl import read_jsonl
from specmind_prep.filter.probe import filter_tests
from specmind_eval.common.outputs import separate_output
from specmind_eval.evaluate.evaluator import Evaluator
from specmind_prep.filter.nodeids import prepare_tests
from specmind_eval.common.fingerprint import digest
from specmind_eval.common.storage import read, write, run_lock

SCHEMA = 'prepared-dataset-v1'


from specmind_eval.common.fingerprint import file_hash, source_environment
from specmind_eval.common.jsonl import write_jsonl

def prepare_dataset(problems, tests, repo, output, *, resume=False, tasks=None,
                    timeout=120, batch_size=500, force=False, project=None, lineage=None, collection_tests=None,
                    reuse_from=None):
    problems, tests, repo = (Path(p).resolve() for p in (problems, tests, repo))
    output = separate_output(output, repo, problems.parent, tests.parent)
    if timeout < 0 or batch_size < 1:
        raise ValueError('Invalid timeout or batch size')
    project = project or (read(problems.parent / "manifest.json", {}) or {}).get("project")
    candidates, _ = load(problems, tests)
    if tasks:
        wanted = set(tasks)
        candidates = [p for p in candidates if evaluation_key(p) in wanted]
        if len(candidates) != len(wanted):
            raise ValueError('Unknown or duplicate function selection')
    if not candidates:
        raise ValueError('No functions to prepare')
    env = source_environment(repo)
    reusable = {}
    reuse_identity = None
    if reuse_from:
        from specmind_eval.inputs.prepared import load_prepared_dataset
        old_problems, old_tests, old_manifest = load_prepared_dataset(reuse_from,repo)
        old = {evaluation_key(p):p for p in old_problems}
        old_results = {r['function']:r for r in read(Path(reuse_from)/'results.json')}
        fields = ('source','docstring','document','module','parent_name','entry_point','repo_path','code_line','decorators','async')
        proposed_tests = {}
        for row in read_jsonl(tests):
            proposed_tests.setdefault(row['problem_id'],[]).append(row)
        for candidate in candidates:
            key = evaluation_key(candidate)
            if (key in old and candidate.get('decision') == 'keep' and not candidate.get('_delegate_binding')
                    and all(candidate.get(f) == old[key].get(f) for f in fields)
                    and {pytest_nodeid(t) for t in proposed_tests.get(key,[])} ==
                        {d['nodeid'] for d in old_results[key].get('decisions',[])}):
                reusable[key] = old_results[key]
        reuse_identity = {'path':str(Path(reuse_from).resolve()),'fingerprint':old_manifest['fingerprint'],
                          'files':old_manifest['files']}
    implementation = {n: file_hash(Path(__file__).resolve().parents[2] / n) for n in
                      ('specmind_prep/filter/dataset.py', 'specmind_prep/filter/nodeids.py', 'specmind_prep/filter/probe.py', 'specmind_eval/evaluate/injected/postcondition_plugin.py',
                       'specmind_eval/common/call_binding.py',
                       'specmind_eval/evaluate/pytest_runner.py', 'specmind_eval/inputs/dataset.py', 'specmind_eval/evaluate/evaluator.py', 'specmind_eval/common/fingerprint.py', 'specmind_eval/common/storage.py', 'specmind_eval/common/jsonl.py', 'specmind_eval/common/outputs.py', 'specmind_eval/inputs/prepared.py')}
    fingerprint = digest({'problems': file_hash(problems), 'tests': file_hash(tests),
                          'candidates': candidates, 'environment': env, 'implementation': implementation,
                          'timeout': timeout, 'batch_size': batch_size, 'project': project, 'lineage': lineage,
                          'collection_tests': collection_tests,'reuse':reuse_identity})
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous and not resume:
            raise ValueError('Output exists; use --resume or a new directory')
        if previous and previous['fingerprint'] != fingerprint:
            raise ValueError('Preparation fingerprint mismatch; use a new directory')
        manifest = {'schema': SCHEMA, 'fingerprint': fingerprint, 'complete': False,
                    'source_environment': env, 'inputs': {'problems': str(problems), 'tests': str(tests), 'repo': str(repo)},
                    'implementation': implementation, 'settings': {'timeout': timeout, 'batch_size': batch_size},
                    'planned_functions': len(candidates), 'files': {}, 'project': project, 'lineage': lineage,
                    'collection_tests': collection_tests}
        if reuse_identity:
            manifest['reused_probes'] = reuse_identity
        write(output/'manifest.json', manifest)
        collection = output/'collection'
        if not previous:
            prepare_tests(tests, repo, collection, python_path=project.get("python_path") if project else None, test_paths=collection_tests if collection_tests is not None else project.get("tests") if project else None)
        if not (collection/'summary.json').exists():
            # Collection may have failed before its checkpoint was created.
            if collection.exists():
                raise ValueError('Incomplete collection directory; use a new output')
            prepare_tests(tests, repo, collection, python_path=project.get("python_path") if project else None, test_paths=collection_tests if collection_tests is not None else project.get("tests") if project else None)
        linked = {}
        for row in read_jsonl(collection/'tests_valid.jsonl'):
            linked.setdefault(row['problem_id'], {})[pytest_nodeid(row)] = row
        results, prepared_problems, prepared_tests = [], [], []
        config = {'pytest_timeout': timeout, 'batch_size': batch_size,
                  '_evaluation_identity': fingerprint, '_force': force, 'python_path': project.get('python_path') if project else None}
        for index, candidate in enumerate(candidates, 1):
            key = evaluation_key(candidate)
            directory = output/'functions'/digest(key)[:16]
            saved = read(directory/'result.json')
            if not saved and key in reusable and not force:
                saved = dict(reusable[key], reused_from=reuse_identity)
                write(directory/'result.json',saved)
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


def prepare_linked_dataset(candidates, links, repo, output, **kwargs):
    """Validate the stage boundary before any tests are collected or run."""
    from specmind_prep.gather.gather import validate_candidates
    from specmind_prep.trace.trace import validate_links, traced_test_selection
    candidates,links=Path(candidates).resolve(),Path(links).resolve()
    parent=validate_candidates(candidates,repo)
    link=validate_links(links,repo,candidates)
    return prepare_dataset(candidates/'problems.jsonl',links/'tests.jsonl',repo,output,
                           collection_tests=traced_test_selection(links,link),project=parent['project'],lineage={'candidate_fingerprint':parent['fingerprint'],
                           'candidate_files':parent['files'],'link_fingerprint':link['fingerprint'],'link_files':link['files']},**kwargs)
