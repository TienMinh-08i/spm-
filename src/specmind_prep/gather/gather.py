"""Source-only candidate gathering; never starts target tests or imports target code."""
import ast
import subprocess
from pathlib import Path
from specmind_eval.common.fingerprint import digest, file_hash, source_environment, implementation_digest
from specmind_eval.common.jsonl import write_jsonl
from specmind_eval.common.storage import read, write, run_lock
from specmind_eval.common.outputs import separate_output
from specmind_prep.project import infer_project, validate_project
from specmind_prep.source_index import source_index

SCHEMA = 'candidates-v1'
REQUIRED = {'problems.jsonl','summary.json'}

def revision(repo):
    proc=subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],capture_output=True,text=True,timeout=30)
    return proc.stdout.strip() if proc.returncode==0 else None

def collect_candidates(repo, project, tasks=None, documented_only=False):
    index,_=source_index(repo,project)
    candidates=[dict(row,docstring=ast.get_docstring(ast.parse(row['source']).body[0],clean=False))
                for row in index.values() if ast.get_docstring(ast.parse(row['source']).body[0])]
    if documented_only:
        from specmind_prep.extract.documented_symbols import collect_problems, collect_documented_symbols, _problem_is_documented
        old=[]
        for package in project['packages']:
            old.extend(collect_problems(repo,repo/project['source_root']/package.replace('.','/'),package))
        documented=collect_documented_symbols(repo,old,project['docs_dir'],project['packages'])
        wanted={p.problem_id for p in old if _problem_is_documented(p,documented)}
        candidates=[p for p in candidates if p['problem_id'] in wanted]
    if tasks:
        wanted=set(tasks)
        candidates=[p for p in candidates if p['problem_id'] in wanted]
        if {p['problem_id'] for p in candidates}!=wanted:
            raise ValueError('Unknown task or task without docstring')
    candidates.sort(key=lambda p:p['problem_id'])
    if not candidates:raise ValueError('No candidate functions')
    for i,p in enumerate(candidates):
        p.update(task_id=project['name']+'/'+str(i),_python_path=project['python_path'])
    return candidates

def gather(repo,output,*,project=None,tasks=None,documented_only=False,resume=False,expected_commit=None):
    repo=Path(repo).resolve()
    output=separate_output(output,repo)
    project=validate_project(project or infer_project(repo),repo,documented_only=documented_only)
    commit=revision(repo)
    expected=expected_commit or project.get('commit')
    if expected and commit!=expected:raise ValueError('Checkout commit mismatch')
    environment=source_environment(repo)
    candidates=collect_candidates(repo,project,tasks,documented_only)
    fingerprint=digest({'project':project,'commit':commit,'environment':environment,
                        'code':implementation_digest('specmind_prep.gather.gather'),
                        'tasks':tasks,'documented_only':documented_only,'candidates':candidates})
    with run_lock(output):
        previous=read(output/'manifest.json')
        if previous:
            if not resume:raise ValueError('Candidates exist; use --resume or a new directory')
            if previous.get('fingerprint')!=fingerprint:raise ValueError('Candidate fingerprint mismatch')
            if previous.get('complete'):
                validate_candidates(output,repo)
                return read(output/'summary.json')
        manifest={'schema':SCHEMA,'complete':False,'fingerprint':fingerprint,'project':project,
                  'repo':str(repo),'repo_commit':commit,'source_environment':environment}
        write(output/'manifest.json',manifest)
        write_jsonl(output/'problems.jsonl',candidates)
        summary={'candidate_functions':len(candidates),'output':str(output)}
        write(output/'summary.json',summary)
        manifest.update(complete=True,files={name:file_hash(output/name) for name in REQUIRED})
        write(output/'manifest.json',manifest)
        return summary

def validate_candidates(directory,repo):
    directory=Path(directory).resolve()
    manifest=read(directory/'manifest.json')
    if not manifest or manifest.get('schema')!=SCHEMA or not manifest.get('complete'):
        raise ValueError('Candidates missing or incomplete')
    files=manifest.get('files')
    if not isinstance(files,dict) or not REQUIRED.issubset(files):raise ValueError('Manifest missing required artifact hashes')
    for name,value in files.items():
        if Path(name).name!=name or not (directory/name).is_file() or file_hash(directory/name)!=value:
            raise ValueError('Candidate artifact mismatch: '+name)
    if manifest['source_environment']!=source_environment(repo):raise ValueError('Candidate source/environment mismatch')
    if manifest['repo_commit']!=revision(repo):raise ValueError('Candidate checkout commit mismatch')
    return manifest
