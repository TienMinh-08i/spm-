"""Bind verified source pools to a ready dataset's self-only task scope."""
from pathlib import Path

from specmind_eval.common.fingerprint import digest, file_hash, source_environment
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.storage import read, write
from specmind_eval.inputs.prepared import load_prepared_dataset, load_prepared_mutants
from specmind_prep.llm_mutants import load_target_pool
from specmind_prep.scope.scope import load_scope


def bind_pools(scope, dataset, repo, pools, output):
    repo, dataset, scope = [Path(p).resolve() for p in (repo,dataset,scope)]
    output = separate_output(output,repo,dataset,scope,*[Path(p).resolve() for p in pools])
    if output.exists():
        raise ValueError('Corpus output exists; choose a new directory')
    rows, targets, parent = load_scope(scope,repo)
    _, _, dm = load_prepared_dataset(dataset,repo)
    if parent['dataset_fingerprint'] != dm['fingerprint'] or parent['dataset_files'] != dm['files']:
        raise ValueError('Scope does not belong to dataset')
    baseline = {p['problem_id']:p for p in targets}
    sources, provenance = {}, []
    for pool in pools:
        functions, _, pm = load_target_pool(pool,repo)
        pool_targets = {p['problem_id']:p for p in read(Path(pool)/'targets_input.json')}
        for key,row in functions.items():
            if key not in baseline:
                continue
            if pool_targets[key]['source'] != baseline[key]['source']:
                raise ValueError('Pool baseline differs from ready scope: '+key)
            if key in sources:
                raise ValueError('Overlapping source pools: '+key)
            sources[key] = row
        provenance.append({'path':str(Path(pool).resolve()),'fingerprint':pm['fingerprint'],'files':pm['files']})
    if set(sources) != set(baseline):
        raise ValueError('Missing source pools: '+str(sorted(set(baseline)-set(sources))))
    functions = []
    for row in rows:
        key = row['mutation_targets'][0]['target']
        problem = dict(baseline[key], _expected_source=baseline[key]['source'],
                       _expected_source_path=str(repo/baseline[key]['repo_path']))
        mutants = [dict(m,method='M2',coverage='observed',mutation_problem=problem) for m in sources[key]['mutants']]
        functions.append({'function':row['assertion_target'],'analysis_target':key,'done':True,
                          'pending_targets':[],'expansion':False,'scope':row,'mutants':mutants})
    manifest = {'schema':'prepared-mutants-v1','complete':False,'generator':'llm','method':'M2',
                'dataset_fingerprint':dm['fingerprint'],'dataset_files':dm['files'],
                'scope_fingerprint':parent['fingerprint'],'scope_files':parent['files'],
                'source_environment':source_environment(repo),'project':parent['project'],'source_pools':provenance}
    manifest['fingerprint'] = digest(manifest)
    summary = {'generator':'llm','method':'M2','functions':len(functions),'unique_targets':len(targets),
               'unique_mutants':len({m['id'] for r in functions for m in r['mutants']}),
               'task_mutant_links':sum(len(r['mutants']) for r in functions),'executed_mutants':0,
               'per_function':{r['function']:len(r['mutants']) for r in functions}}
    write(output/'manifest.json',manifest)
    write(output/'targets_input.json',targets)
    write(output/'functions.json',functions)
    write(output/'summary.json',summary)
    manifest.update(complete=True,files={n:file_hash(output/n) for n in ('targets_input.json','functions.json','summary.json')})
    write(output/'manifest.json',manifest)
    load_prepared_mutants(output,repo,dm)
    return summary
