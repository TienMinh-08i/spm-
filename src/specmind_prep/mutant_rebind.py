"""Rebind unchanged source mutants to a freshly validated dataset/scope.

This invalidates runtime-filter evidence. It never promotes an old filtered
corpus or rewrites its manifest to pretend it was checked on the new tests.
"""
from pathlib import Path
from copy import deepcopy
from specmind_eval.common.storage import read,write
from specmind_eval.common.fingerprint import file_hash,digest,source_environment
from specmind_eval.inputs.prepared import load_prepared_dataset,load_prepared_mutants
from specmind_prep.scope.scope import load_scope


def rebind_mutants(source,old_dataset,dataset,scope,repo,output):
    source,old_dataset,dataset,scope,repo,output=map(lambda p:Path(p).resolve(),(source,old_dataset,dataset,scope,repo,output))
    if output.exists() or any(output==p or p in output.parents for p in (repo,source,old_dataset,dataset,scope)):
        raise ValueError('Choose a new output outside source/input directories')
    before,_,old=load_prepared_dataset(old_dataset,repo)
    after,_,dm=load_prepared_dataset(dataset,repo)
    corpus,parent=load_prepared_mutants(source,repo,old)
    if parent.get('neutral_filter'):
        raise ValueError('Rebind the unfiltered source corpus; old exclusions must be reconsidered')
    rows,targets,sm=load_scope(scope,repo)
    if sm['dataset_fingerprint']!=dm['fingerprint'] or sm['dataset_files']!=dm['files']:
        raise ValueError('Scope belongs to a different dataset')
    by_old={p['function_id']:p for p in before};by_new={p['function_id']:p for p in after}
    if set(by_old)!=set(by_new) or set(corpus)!=set(by_new):
        raise ValueError('Task sets differ; explicit source-pool binding required')
    fields=('problem_id','source','module','parent_name','entry_point','repo_path','line','code_line','decorators','async','_delegate_binding')
    for key in by_new:
        if any(by_old[key].get(f)!=by_new[key].get(f) for f in fields):
            raise ValueError('Source or call ownership changed: '+key)
    by_target={p['problem_id']:p for p in targets};by_scope={r['assertion_target']:r for r in rows}
    functions=[]
    for key,row in corpus.items():
        target=by_new[key]['problem_id'];new=deepcopy(row)
        if [r['target'] for r in by_scope[key]['mutation_targets']]!=[target]:raise ValueError('Not a self-only scope')
        for mutant in new['mutants']:
            mp=mutant['mutation_problem']
            if mp['problem_id']!=target or mp['source']!=by_target[target]['source']:
                raise ValueError('Mutant baseline differs from freshly selected target: '+key)
        new['scope']=by_scope[key]
        new['analysis_target']=target
        functions.append(new)
    summary={'functions':len(functions),'unique_targets':len(targets),
             'unique_mutants':len({m['id'] for r in functions for m in r['mutants']}),
             'task_mutant_links':sum(len(r['mutants']) for r in functions),
             'executed_mutants':0,'runtime_filter_required':True,
             'per_function':{r['function']:len(r['mutants']) for r in functions}}
    manifest={k:v for k,v in parent.items() if k in ('schema','generator','method','project')}
    manifest.update(complete=True,dataset_fingerprint=dm['fingerprint'],dataset_files=dm['files'],
                    scope_fingerprint=sm['fingerprint'],scope_files=sm['files'],source_environment=source_environment(repo),
                    rebind={'source':str(source),'fingerprint':parent['fingerprint'],'files':parent['files'],
                            'old_dataset_fingerprint':old['fingerprint'],'runtime_evidence_reused':False},
                    runtime_filter_required=True)
    output.mkdir(parents=True)
    for name,value in [('functions.json',functions),('targets_input.json',targets),('summary.json',summary)]:write(output/name,value)
    manifest['files']={f:file_hash(output/f) for f in ('functions.json','targets_input.json','summary.json')}
    manifest['fingerprint']=digest(manifest)
    write(output/'manifest.json',manifest)
    load_prepared_mutants(output,repo,dm,allow_unfiltered=True)
    return summary
