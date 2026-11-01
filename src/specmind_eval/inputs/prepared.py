"""Read-only prepared artifact loaders; validation behavior is unchanged."""
from pathlib import Path
from specmind_eval.inputs.dataset import load
from specmind_eval.common.storage import read
from specmind_eval.common.fingerprint import digest, file_hash, source_environment

SCHEMA = 'prepared-dataset-v1'


def load_prepared_dataset(directory, repo):
    """Read/validate only. Never runs pytest, filters tests or creates mutants."""
    directory, repo = Path(directory).resolve(), Path(repo).resolve()
    manifest = read(directory/'manifest.json')
    if not manifest or manifest.get('schema') != SCHEMA or not manifest.get('complete'):
        raise ValueError('Dataset preparation missing or incomplete')
    required = {'excluded_functions.json', 'problems.jsonl', 'tests.jsonl', 'summary.json', 'results.json'}
    if not isinstance(manifest.get("files"), dict) or not required.issubset(manifest["files"]):
        raise ValueError("Manifest missing required artifact hashes")
    if source_environment(repo) != manifest['source_environment']:
        raise ValueError('Prepared dataset source/environment mismatch')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or file_hash(directory/name) != expected:
            raise ValueError('Prepared dataset artifact mismatch: '+name)
    problems, tests = load(directory/'problems.jsonl', directory/'tests.jsonl')
    results = {r['function']: r for r in read(directory/'results.json')}
    for problem in problems:
        key = problem.get('problem_id') or problem['task_id']
        record = results.get(key, {})
        if record.get('status') != 'ready' or digest(tests.get(key, [])) != digest(record.get('tests', [])):
            raise ValueError('Prepared test evidence mismatch: '+key)
    return problems, tests, manifest


def load_prepared_mutants(output,repo,dataset_manifest):
    """Read/verify only, no Docker, generation or discovery in main loop."""
    output=Path(output).resolve()
    manifest=read(output/'manifest.json')
    if not manifest or manifest.get('schema')!='prepared-mutants-v1' or not manifest.get('complete'):
        raise ValueError('Prepared mutants missing or incomplete')
    required = {'summary.json', 'functions.json', 'targets_input.json'}
    if not isinstance(manifest.get("files"), dict) or not required.issubset(manifest["files"]):
        raise ValueError("Manifest missing required artifact hashes")
    if manifest['source_environment']!=source_environment(repo):
        raise ValueError('Prepared mutants source/environment mismatch')
    if manifest['dataset_fingerprint']!=dataset_manifest['fingerprint'] or manifest['dataset_files']!=dataset_manifest['files']:
        raise ValueError('Prepared mutants do not belong to this dataset')
    for name,value in manifest['files'].items():
        if Path(name).name!=name or file_hash(output/name)!=value:
            raise ValueError('Prepared mutant artifact mismatch: '+name)
    rows=read(output/'functions.json')
    return {r['function']:r for r in rows},manifest

