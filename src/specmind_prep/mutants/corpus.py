"""Prepare one shared mutant corpus in Docker, then publish per-assertion mappings."""
import os
import subprocess
from pathlib import Path

from specmind_prep.scope.scope import load_scope
from specmind_eval.common.fingerprint import file_hash, source_environment
from specmind_eval.common.fingerprint import digest
from specmind_eval.common.storage import read, write, run_lock
from specmind_eval.common.outputs import separate_output


def prepare_mutants(scope,repo,output,*,resume=False,image='specmind-paper-eval-mutmut-py312:latest',timeout=1800):
    scope,repo=Path(scope).resolve(),Path(repo).resolve()
    workspace=Path(__file__).resolve().parents[2]
    output=separate_output(output,scope,repo)
    rows,targets,scope_manifest=load_scope(scope,repo)
    inspected=subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],capture_output=True,text=True,check=True,timeout=30)
    image_id=inspected.stdout.strip()
    implementation={name:file_hash(Path(__file__).resolve().parents[2] / name) for name in
                    ('specmind_prep/mutants/corpus.py','specmind_prep/mutants/worker.py','specmind_prep/mutants/mutmut_export.py','specmind_eval/evaluate/injected/mutation_runtime.py',
                     'specmind_eval/inputs/source.py','specmind_eval/inputs/dataset.py','specmind_eval/evaluate/pytest_runner.py','specmind_eval/common/storage.py','specmind_prep/filter/dataset.py','specmind_eval/common/fingerprint.py','specmind_eval/inputs/prepared.py')}
    fingerprint=digest({'scope':scope_manifest['fingerprint'],'scope_files':scope_manifest['files'],
                        'implementation':implementation,'image':image_id,'timeout':timeout})
    with run_lock(output):
        old=read(output/'manifest.json')
        if old and not resume:
            raise ValueError('Corpus exists; use --resume or new output')
        if old and old['fingerprint']!=fingerprint:
            raise ValueError('Mutant preparation fingerprint mismatch')
        manifest={'schema':'prepared-mutants-v1','fingerprint':fingerprint,'complete':False,
                  'scope_fingerprint':scope_manifest['fingerprint'],'scope_files':scope_manifest['files'],
                  'dataset_fingerprint':scope_manifest['dataset_fingerprint'],'dataset_files':scope_manifest['dataset_files'],
                  'source_environment':source_environment(repo),'image_id':image_id,'image':image,
                  'implementation':implementation,'files':{},'timeout':timeout}
        write(output/'manifest.json',manifest)
        write(output/'targets_input.json',targets)
        command=['docker','run','--rm','--mount',f'type=bind,source={workspace.as_posix()},target=/workspace,readonly',
                 '--mount',f'type=bind,source={output.as_posix()},target=/artifacts',
                 '--mount',f'type=bind,source={repo.as_posix()},target=/target_repo,readonly',
                 '-e','PYTHONPATH=/workspace','-e','PYTHONUNBUFFERED=1',image_id,
                 'python','-m','specmind_prep.mutants.worker','--input','/artifacts/targets_input.json',
                 '--repo','/target_repo',
                 '--output','/artifacts','--timeout',str(timeout)]
        with (output/'docker.log').open('a',encoding='utf-8') as log:
            process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
            for line in process.stdout:
                print(line,end='',flush=True)
                log.write(line)
                log.flush()
            if process.wait():
                raise ValueError('Docker worker failed; see docker.log and resume')
        bundles={}
        for target in targets:
            key=target['problem_id']
            record=read(output/'targets'/digest(key)[:16]/'export.json',{})
            if record.get('status')=='ready':
                bundles[key]=record
        mappings=[]
        for row in rows:
            mutations=[]
            pending=[]
            for selected in row['mutation_targets']:
                key=selected['target']
                if key not in bundles:
                    pending.append(key)
                    continue
                for mutant in bundles[key]['mutants']:
                    target=bundles[key]['target']
                    mutation_problem=dict(target,_expected_source=target['source'],
                                          _expected_source_path=str((repo/target['repo_path']).resolve()))
                    mutations.append(dict(mutant,id=key+'::'+str(mutant['id']),engine_id=mutant['id'],
                                          mutation_target=key,kind=selected['kind'],
                                          mutation_problem=mutation_problem,coverage=selected['coverage']))
            mappings.append({'function':row['assertion_target'],'body_line_count':row['body_line_count'],
                             'expansion':row['expansion'],'mutants':mutations,'pending_targets':pending,
                             'done':not pending,'scope':row})
        write(output/'functions.json',mappings)
        summary={'functions':len(rows),'ready_functions':sum(r['done'] for r in mappings),
                 'unique_targets':len(targets),'ready_targets':len(bundles),'pending_targets':len(targets)-len(bundles),
                 'unique_mutants':sum(r['exported'] for r in bundles.values()),
                 'discovered_mutants':sum(r['discovered'] for r in bundles.values()),
                 'skipped_mutants':sum(len(r['skipped']) for r in bundles.values()),
                 'function_mutant_pairs':sum(len(r['mutants']) for r in mappings),'output':str(output)}
        write(output/'summary.json',summary)
        manifest.update(complete=summary['pending_targets']==0,
                        files={name:file_hash(output/name) for name in ('functions.json','summary.json','targets_input.json')})
        write(output/'manifest.json',manifest)
    return summary
