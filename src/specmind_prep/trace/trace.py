"""Trace selected tests using only sealed candidate locations."""
import os
import subprocess
import sys
from pathlib import Path
from specmind_eval.common.fingerprint import digest,file_hash,source_environment,implementation_digest
from specmind_eval.common.jsonl import read_jsonl,write_jsonl
from specmind_eval.common.storage import read,write,run_lock
from specmind_eval.common.outputs import separate_output
from specmind_eval.evaluate.pytest_runner import pythonpath_for
from specmind_prep.gather.gather import validate_candidates

SCHEMA='links-v1'
REQUIRED={'tests.jsonl','trace.json','trace.log','command.json','trace_config.json','summary.json'}

def trace(candidates,repo,output,*,tests=None,exclude=None,timeout=1800,resume=False):
    repo=Path(repo).resolve();candidates=Path(candidates).resolve()
    output=separate_output(output,repo,candidates)
    parent=validate_candidates(candidates,repo)
    project=parent['project']
    problems=read_jsonl(candidates/'problems.jsonl')
    if timeout<=0:raise ValueError('Trace timeout must be positive')
    tests=list(tests if tests is not None else project['tests'])
    if not tests:raise ValueError('Select at least one test path')
    for test in tests:
        path=(repo/test.split('::',1)[0]).resolve()
        if test.startswith('-') or not path.is_relative_to(repo):raise ValueError('Test path must stay inside checkout; pytest options are not allowed')
    exclude=[] if exclude is None else exclude
    if not isinstance(exclude,list) or any(not isinstance(e,dict) or not e.get('pattern') or not e.get('reason') for e in exclude):
        raise ValueError('Every exclusion needs pattern and reason')
    fingerprint=digest({'candidates':parent['fingerprint'],'candidate_files':parent['files'],
                        'environment':source_environment(repo),'code':implementation_digest('specmind_prep.trace.trace','specmind_prep.injected.call_trace_plugin'),
                        'tests':tests,'exclude':exclude,'timeout':timeout})
    with run_lock(output):
        previous=read(output/'manifest.json')
        if previous:
            if not resume:raise ValueError('Links exist; use --resume or a new directory')
            if previous.get('fingerprint')!=fingerprint:raise ValueError('Link fingerprint mismatch; use a new links directory')
            if previous.get('complete'):
                validate_links(output,repo,candidates)
                return read(output/'summary.json')
        manifest={'schema':SCHEMA,'complete':False,'fingerprint':fingerprint,'project':project,
                  'candidate_fingerprint':parent['fingerprint'],'candidate_files':parent['files'],
                  'source_environment':source_environment(repo),'repo_commit':parent['repo_commit']}
        write(output/'manifest.json',manifest)
        config={'locations':[[str(repo/p['repo_path']),p['code_line'],p['problem_id']] for p in problems]}
        write(output/'trace_config.json',config)
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[2]),pythonpath_for(repo,project['python_path'])]),
                 PYTHONUTF8='1',PYTHONIOENCODING='utf8',MM_PREP_TRACE_CONFIG=str(output/'trace_config.json'),MM_PREP_TRACE_RESULT=str(output/'trace.json'))
        env.pop('PYTEST_ADDOPTS',None)
        command=[sys.executable,'-m','pytest','-q','--tb=short','-p','specmind_prep.injected.call_trace_plugin']
        if exclude:command+=['-k',' and '.join('not ('+e['pattern']+')' for e in exclude)]
        command+=tests
        write(output/'command.json',{'argv':command,'cwd':str(repo)})
        (output/'trace.json').unlink(missing_ok=True)
        try:
            with (output/'trace.log').open('w',encoding='utf8') as log:
                proc=subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        except subprocess.TimeoutExpired as exc:raise ValueError('Trace timed out; candidates remain complete; choose a new links output to change settings') from exc
        evidence=read(output/'trace.json',{})
        if proc.returncode not in (0,1) or not evidence.get('complete'):
            raise ValueError('Trace stopped before all selected tests completed; inspect trace.log')
        chosen={p['problem_id'] for p in problems}
        rows=[{'problem_id':key,'nodeid':node,'test_id':node,'test_path':node.split('::',1)[0],
               'framework':'pytest-profile','trace_reports':evidence['reports'].get(node,{})}
              for node,keys in sorted(evidence['links'].items()) for key in keys if key in chosen]
        validate_candidates(candidates,repo)
        write_jsonl(output/'tests.jsonl',rows)
        linked={r['problem_id'] for r in rows}
        summary={'candidate_functions':len(problems),'linked_functions':len(linked),'test_links':len(rows),
                 'unique_tests':len({r['nodeid'] for r in rows}),'unlinked_functions':sorted(chosen-linked),
                 'collected_tests':len(evidence['nodeids']),'trace_exitcode':proc.returncode,'excluded_tests':exclude,'output':str(output)}
        write(output/'summary.json',summary)
        manifest.update(complete=True,files={name:file_hash(output/name) for name in REQUIRED})
        write(output/'manifest.json',manifest)
        return summary

def validate_links(directory,repo,candidates=None):
    directory=Path(directory).resolve();manifest=read(directory/'manifest.json')
    if not manifest or manifest.get('schema')!=SCHEMA or not manifest.get('complete'):raise ValueError('Links missing or incomplete')
    files=manifest.get('files')
    if not isinstance(files,dict) or not REQUIRED.issubset(files):raise ValueError('Manifest missing required artifact hashes')
    for name,value in files.items():
        if Path(name).name!=name or not (directory/name).is_file() or file_hash(directory/name)!=value:raise ValueError('Link artifact mismatch: '+name)
    if manifest['source_environment']!=source_environment(repo):raise ValueError('Link source/environment mismatch')
    if candidates is not None:
        parent=validate_candidates(candidates,repo)
        if manifest['candidate_fingerprint']!=parent['fingerprint'] or manifest['candidate_files']!=parent['files'] or manifest['project']!=parent['project']:
            raise ValueError('Links do not belong to these candidates')
    return manifest
