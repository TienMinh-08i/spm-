"""Read-only prepared dynamic slice context; no tracing during evaluation."""
from pathlib import Path
from specmind_eval.common.storage import read
from specmind_eval.common.fingerprint import digest, file_hash, source_environment, require_source_environment


def load_slice_context(directory, repo, problems, budget):
    directory=Path(directory); m=read(directory/'manifest.json')
    if not m or m.get('schema')!='prepared-slice-context-v1' or not m.get('complete'):
        raise ValueError('Missing complete prepared slice context')
    if m.get('fingerprint') != digest({k:v for k,v in m.items() if k != 'fingerprint'}):
        raise ValueError('Slice context manifest mismatch')
    if set(m.get('files',{})) != {'packs.json'} or file_hash(directory/'packs.json')!=m['files']['packs.json']:
        raise ValueError('Slice context artifact mismatch')
    require_source_environment(m['source_environment'],source_environment(repo),'Slice context')
    rows=read(directory/'packs.json'); packs={}
    from specmind_eval.inputs.dataset import evaluation_key
    for p in problems:
        key=evaluation_key(p); pack=rows.get(key)
        if not pack or pack['analysis_target']!=p['problem_id']:
            raise ValueError('Slice context missing or wrong target: '+key)
        if len(pack['repository_context'])>budget:
            raise ValueError('Slice context exceeds budget: '+key)
        packs[key]=pack
    return packs,file_hash(directory/'manifest.json')
