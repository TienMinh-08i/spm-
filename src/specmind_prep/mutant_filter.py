"""Offline neutral runtime filtering, independently for each task-mutant link."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import Counter

from specmind_eval.common.fingerprint import digest, file_hash, engine_digest
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.storage import read, write, run_lock
from specmind_eval.evaluate.evaluator import Evaluator
from specmind_eval.inputs.dataset import evaluation_key, pytest_nodeid
from specmind_eval.inputs.prepared import load_prepared_dataset, load_prepared_mutants


def classify(arm, nodeids):
    events = arm.get('detail', {}).get('events', [])
    cases = [arm['cases'].get(n) for n in nodeids]
    if any(e.get('kind') == 'hook_install_error' for e in events):
        return 'installation_error'
    if arm['status'] == 'timeout':
        return 'timeout'
    if arm['status'] == 'collection_error':
        return 'collection_error'
    if arm['status'] not in {'pass', 'fail'}:
        return 'execution_error'
    if any(not c or any(p not in c['reports'] for p in ('setup', 'call', 'teardown'))
           or any(c['reports'][p] != 'passed' for p in ('setup', 'teardown')) for c in cases):
        return 'incomplete_test_execution'
    if any(c.get('mutation_errors') or c.get('runtime_errors') or c.get('violations') for c in cases):
        return 'instrumentation_error'
    if not any(c.get('mutation_calls') for c in cases):
        return 'mutation_not_reached'
    if not any(c.get('checks') for c in cases):
        return ('target_exception_before_assertion' if any(c.get('target_errors') for c in cases)
                else 'assertion_not_reached')
    # Existing test assertions may fail: that is a useful mutant, not an exclusion.
    return 'keep'


def filter_mutants(dataset, mutants, repo, output, *, timeout=120, workers=4, resume=False, batch_size=500):
    if timeout <= 0 or workers < 1 or batch_size < 1:
        raise ValueError('timeout and workers must be positive')
    dataset, mutants, repo = [Path(p).resolve() for p in (dataset, mutants, repo)]
    output = separate_output(output, dataset, mutants, repo)
    problems, tests, dm = load_prepared_dataset(dataset, repo)
    corpus, parent = load_prepared_mutants(mutants, repo, dm, allow_unfiltered=True)
    if set(corpus) != {evaluation_key(p) for p in problems}:
        raise ValueError('Corpus task set differs from dataset')
    config = {'pytest_timeout': timeout, 'batch_size': batch_size,
              'python_path': dm.get('project', {}).get('python_path')}
    identity = digest({'dataset': dm, 'parent': parent, 'filter': file_hash(__file__),
                       'engine': engine_digest(), 'config': config})
    config['_evaluation_identity'] = identity
    if output.exists() and not resume:
        raise ValueError('Output exists; use --resume or a new directory')
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous and previous.get('fingerprint') != identity:
            raise ValueError('Resume identity mismatch')
        if previous and previous.get('complete'):
            load_prepared_mutants(output, repo, dm)
            return read(output/'summary.json')
        manifest = {k:v for k,v in parent.items() if k not in {'files','fingerprint','complete'}}
        manifest.update(complete=False, fingerprint=identity,
                        neutral_filter={'assertion':'assert True', 'version':1, 'timeout':timeout,
                                        'scope':'task-mutant', 'source':str(mutants),
                                        'source_fingerprint':parent['fingerprint'], 'source_files':parent['files']})
        write(output/'manifest.json', manifest)

        def task(problem):
            key = evaluation_key(problem)
            directory = output/'evidence'/digest(key)[:16]
            saved = read(directory/'decision.json')
            if saved:
                if saved.get('identity') != identity:
                    raise ValueError('Decision identity mismatch')
                return saved
            problem = dict(problem)
            problem['_expected_source'] = problem['source']
            problem['_expected_source_path'] = str(repo/problem['repo_path'])
            evaluator = Evaluator(problem, tests[key], repo, directory, config)
            nodeids = [pytest_nodeid(t) for t in tests[key]]
            baseline = evaluator.arm('assert True')
            if baseline['status'] != 'pass' or any(not baseline['cases'].get(n,{}).get('checks') for n in nodeids):
                raise ValueError('Neutral baseline failed; no filtering decision: '+key)
            decisions = []
            for mutant in corpus[key]['mutants']:
                arm = evaluator.arm('assert True', mutant)
                reason = classify(arm, nodeids)
                decisions.append({'id':mutant['id'], 'keep':reason=='keep', 'reason':reason,
                                  'status':arm['status'], 'arm':evaluator.arm_key('assert True',mutant),
                                  'target_errors':dict(Counter(e.get('error','unknown') for e in arm['detail'].get('events',[])
                                                              if e.get('kind')=='target_runtime_error'))})
            result = {'function':key,'identity':identity,'decisions':decisions}
            write(directory/'decision.json',result)
            print(f"{key}: kept {sum(d['keep'] for d in decisions)}/{len(decisions)}",flush=True)
            return result

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(task, problems))
        functions, excluded = [], []
        for result in results:
            key = result['function']; kept = {d['id'] for d in result['decisions'] if d['keep']}
            functions.append(dict(corpus[key], mutants=[m for m in corpus[key]['mutants'] if m['id'] in kept]))
            excluded.extend(dict(d,function=key) for d in result['decisions'] if not d['keep'])
        summary = {'functions':len(functions),'input_task_mutant_links':sum(len(r['mutants']) for r in corpus.values()),
                   'kept_task_mutant_links':sum(len(r['mutants']) for r in functions),
                   'excluded_task_mutant_links':len(excluded),
                   'unique_mutants':len({m['id'] for r in functions for m in r['mutants']}),
                   'exclusion_reasons':dict(Counter(d['reason'] for d in excluded)),
                   'per_function':{r['function']:len(r['mutants']) for r in functions}}
        for name,value in [('functions.json',functions),('targets_input.json',read(mutants/'targets_input.json')),
                           ('summary.json',summary),('excluded_mutants.json',excluded),('decisions.json',results)]:
            write(output/name,value)
        manifest['runtime_filter_required'] = False
        manifest.update(complete=True,files={n:file_hash(output/n) for n in
                        ('functions.json','targets_input.json','summary.json','excluded_mutants.json','decisions.json')})
        write(output/'manifest.json',manifest)
        load_prepared_mutants(output,repo,dm)
        return summary
