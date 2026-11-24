"""Load the code-only slice for the public API being evaluated."""
from pathlib import Path
from specmind_eval.common.fingerprint import file_hash
from specmind_eval.common.git import verify_commit
from specmind_eval.common.storage import read
from specmind_eval.inputs.dataset import evaluation_key

def load_slice_context(directory, repo, problems, budget):
    directory = Path(directory)
    manifest = read(directory / 'manifest.json')
    if not manifest or manifest.get('schema') != 'dynapyt-code-v1' or not manifest.get('commit'):
        raise ValueError('Expected a pinned DynaPyt code context')
    verify_commit(repo, manifest['commit'])
    path = directory / 'packs.json'
    if file_hash(path) != manifest.get('sha256'):
        raise ValueError('Slice context artifact mismatch')
    rows = read(path)
    if len(rows) != manifest.get('count'):
        raise ValueError('Slice API inventory mismatch')
    selected = {}
    for problem in problems:
        key = evaluation_key(problem)
        pack = rows.get(key)
        if not pack or pack['analysis_target'] != problem['problem_id']:
            raise ValueError('Missing API or mismatched target: ' + key)
        code = pack['repository_context']
        if not code.strip() or len(code) > budget:
            raise ValueError('Missing context or exceeded budget: ' + key)
        selected[key] = pack
    return selected, file_hash(directory / 'manifest.json')
