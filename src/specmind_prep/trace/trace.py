"""Trace selected tests using only sealed candidate locations."""
from specmind_eval.common.fingerprint import require_source_environment
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
from specmind_eval.inputs.dataset import evaluation_key

SCHEMA='links-v1'
REQUIRED={'tests.jsonl','trace.json','trace.log','command.json','trace_config.json','summary.json'}


def traced_test_selection(directory, manifest=None, _seen=None):
    """Read the actual sealed pytest command, never a candidate's pilot list."""
    directory=Path(directory).resolve()
    seen=set() if _seen is None else set(_seen)
    if directory in seen or len(seen)>=16:raise ValueError('Cyclic/deep trace reuse provenance')
    seen.add(directory)
    manifest=manifest or read(directory/'manifest.json')
    argv=read(directory/'command.json')['argv']
    try:tail=argv[argv.index('specmind_prep.injected.call_trace_plugin')+1:]
    except ValueError as exc:raise ValueError('Unknown trace command protocol') from exc
    exclusion=tail[1] if tail[:1]==['-k'] and len(tail)>1 else None
    if tail[:1]==['-k']:tail=tail[2:]
    if not tail or any(not isinstance(x,str) or x.startswith('-') for x in tail):
        raise ValueError('Trace command has no unambiguous test selection')
    if 'test_selection' in manifest and manifest['test_selection']!=tail:
        raise ValueError('Declared trace selection differs from sealed pytest command')
    if manifest.get('reused_links'):
        parent=Path(manifest['reused_links']['path'])/'links'
        parent_manifest=read(parent/'manifest.json')
        if not parent_manifest or traced_test_selection(parent,parent_manifest,seen)!=tail:
            raise ValueError('Mixed-scope reused links require a fresh trace; refusing silent pilot narrowing')
        parent_argv=read(parent/'command.json')['argv']
        parent_tail=parent_argv[parent_argv.index('specmind_prep.injected.call_trace_plugin')+1:]
        parent_exclusion=parent_tail[1] if parent_tail[:1]==['-k'] and len(parent_tail)>1 else None
        if parent_exclusion!=exclusion:
            raise ValueError('Mixed-scope reused exclusions require a fresh trace')
    return tail


def trace(candidates,repo,output,*,tests=None,exclude=None,timeout=1800,resume=False,reuse_from=None):
    repo=Path(repo).resolve();candidates=Path(candidates).resolve()
    output=separate_output(output,repo,candidates)
    parent=validate_candidates(candidates,repo)
    project=parent['project']
    problems=read_jsonl(candidates/'problems.jsonl')
    if any(p.get('decision') == 'delegate' and not p.get('_delegate_binding') for p in problems):
        raise ValueError('Delegate selection is valid, but tracing requires API-specific call bindings and task identity; '
                         'refusing to merge helper calls across API tasks')
    tests=list(tests if tests is not None else project['tests'])
    reused_rows, reused_keys, reuse_identity = [], set(), None
    if reuse_from:
        reuse_from = Path(reuse_from).resolve()
        old_parent = validate_candidates(reuse_from/'candidates',repo)
        old_links = validate_links(reuse_from/'links',repo,reuse_from/'candidates')
        if traced_test_selection(reuse_from/'links',old_links)!=tests or read(reuse_from/'links/summary.json').get('excluded_tests',[])!=(exclude or []):
            raise ValueError('Cannot reuse links from a different test selection/exclusion scope; run a fresh trace')
        old = {evaluation_key(p):p for p in read_jsonl(reuse_from/'candidates/problems.jsonl')}
        fields = ('source','docstring','document','module','parent_name','entry_point','repo_path','code_line','decorators','async')
        reused_keys = {evaluation_key(p) for p in problems if p.get('decision') == 'keep'
                       and evaluation_key(p) in old and not p.get('_delegate_binding')
                       and all(p.get(f) == old[evaluation_key(p)].get(f) for f in fields)}
        reused_rows = [r for r in read_jsonl(reuse_from/'links/tests.jsonl') if r['problem_id'] in reused_keys]
        reuse_identity = {'path':str(reuse_from),'candidates':old_parent['fingerprint'],
                          'links':old_links['fingerprint'],'files':old_links['files']}
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
                        'tests':tests,'exclude':exclude,'timeout':timeout,'reuse':reuse_identity})
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
                  'source_environment':source_environment(repo),'repo_commit':parent['repo_commit'],
                  'test_selection':tests,'exclusions':exclude,
                  'scope_kind':'project_full' if tests==project['tests'] and not exclude else 'explicit_subset'}
        if reuse_identity:
            manifest['reused_links'] = reuse_identity
        write(output/'manifest.json',manifest)
        config={'repo':str(repo),'tasks':[{'path':str(repo/p['repo_path']), 'line':p['code_line'],
                                        'key':evaluation_key(p),'binding':p.get('_delegate_binding')}
                                       for p in problems]}
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
        chosen={evaluation_key(p) for p in problems}
        rows=[{'problem_id':key,'nodeid':node,'test_id':node,'test_path':node.split('::',1)[0],
               'framework':'pytest-profile','trace_reports':evidence['reports'].get(node,{})}
              for node,keys in sorted(evidence['links'].items()) for key in keys if key in chosen and key not in reused_keys]
        rows += reused_rows
        validate_candidates(candidates,repo)
        write_jsonl(output/'tests.jsonl',rows)
        linked={r['problem_id'] for r in rows}
        summary={'candidate_functions':len(problems),'linked_functions':len(linked),'test_links':len(rows),
                 'unique_tests':len({r['nodeid'] for r in rows}),'unlinked_functions':sorted(chosen-linked),
                 'collected_tests':len(evidence['nodeids']),'trace_exitcode':proc.returncode,'excluded_tests':exclude,'output':str(output)}
        summary['reused_functions'] = sorted(reused_keys)
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
    traced_test_selection(directory,manifest)
    require_source_environment(manifest['source_environment'], source_environment(repo), 'Link')
    if candidates is not None:
        parent=validate_candidates(candidates,repo)
        if manifest['candidate_fingerprint']!=parent['fingerprint'] or manifest['candidate_files']!=parent['files'] or manifest['project']!=parent['project']:
            raise ValueError('Links do not belong to these candidates')
    return manifest
