"""Compatibility facade for Python callers using a combined raw directory.

The primary CLI uses gather/trace artifacts. Existing raw inputs are never migrated.
"""
import shutil
from pathlib import Path
from specmind_eval.common.fingerprint import digest,file_hash,source_environment,implementation_digest
from specmind_eval.common.storage import read,write,run_lock
from specmind_eval.common.outputs import separate_output
from specmind_prep.project import validate_project,infer_project
from specmind_prep.gather.gather import gather
from specmind_prep.trace.trace import trace

RAW_FILES={'problems.jsonl','tests.jsonl','trace.json','command.json','summary.json'}

def extract(repo,output,*,tasks=None,tests=None,exclude=None,documented_only=False,resume=False,timeout=1800,expected_commit=None,project=None):
    repo=Path(repo).resolve();output=separate_output(output,repo)
    project=validate_project(project or infer_project(repo),repo,documented_only=documented_only)
    fingerprint=digest({'project':project,'tasks':tasks,'tests':tests,'exclude':exclude,'documented_only':documented_only,
                        'timeout':timeout,'expected_commit':expected_commit,'environment':source_environment(repo),
                        'code':implementation_digest('specmind_prep.extract.extract','specmind_prep.injected.call_trace_plugin')})
    with run_lock(output):
        old=read(output/'manifest.json')
        if old:
            if not resume:raise ValueError('Raw output exists; use --resume or a new directory')
            if old.get('fingerprint')!=fingerprint:raise ValueError('Raw fingerprint mismatch; use a new directory')
            if old.get('complete'):
                from specmind_prep.cli import check_raw
                check_raw(output)
                return read(output/'summary.json')
        manifest={'schema':'raw-v2','complete':False,'fingerprint':fingerprint,'project':project,
                  'repo':str(repo),'source_environment':source_environment(repo)}
        write(output/'manifest.json',manifest)
        gather(repo,output/'candidates',project=project,tasks=tasks,documented_only=documented_only,resume=resume,expected_commit=expected_commit)
        shutil.copyfile(output/'candidates/problems.jsonl',output/'problems.jsonl')
        try:
            summary=trace(output/'candidates',repo,output/'links',tests=tests,exclude=exclude,timeout=timeout,resume=resume)
        finally:
            for name in ('trace.json','trace.log','trace_config.json','command.json'):
                if (output/'links'/name).is_file():shutil.copyfile(output/'links'/name,output/name)
        summary=dict(summary,output=str(output))
        shutil.copyfile(output/'links/tests.jsonl',output/'tests.jsonl')
        write(output/'summary.json',summary)
        manifest.update(complete=True,files={name:file_hash(output/name) for name in RAW_FILES})
        write(output/'manifest.json',manifest)
        return summary
