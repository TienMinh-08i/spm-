"""Offline self-only mutation scope, sealed against a prepared dataset."""
import ast
from pathlib import Path

from specmind_eval.inputs.dataset import function_code, pytest_nodeid
from specmind_eval.inputs.prepared import load_prepared_dataset
from specmind_eval.common.fingerprint import file_hash, source_environment
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.fingerprint import digest
from specmind_eval.common.storage import read, write, run_lock


def body_line_count(source):
    node = ast.parse(source).body[0]
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body.pop(0)
    return len('\n'.join(ast.unparse(n) for n in body).splitlines())




def prepare_scope(dataset, repo, output, *, resume=False, timeout=300):
    """Seal exactly one mutation target per dataset function; run no tests."""
    dataset, repo = Path(dataset).resolve(), Path(repo).resolve()
    output = separate_output(output, dataset, repo)
    problems, tests, parent = load_prepared_dataset(dataset, repo)
    rows = [{'assertion_target': p['problem_id'], 'expansion': False,
             'body_line_count': body_line_count(function_code(p)),
             'mutation_targets': [{'target': p['problem_id'], 'kind': 'self',
                                  'coverage': 'observed',
                                  'observed_nodeids': [pytest_nodeid(t) for t in tests[p['problem_id']]]}]}
            for p in problems]
    fingerprint = digest({'dataset': parent['fingerprint'], 'files': parent['files'],
                          'policy': 'self-only-v1'})
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous:
            if not resume:
                raise ValueError('Scope exists; use --resume or new output')
            if previous.get('fingerprint') != fingerprint:
                raise ValueError('Scope fingerprint mismatch')
            if previous.get('complete'):
                load_scope(output, repo)
                return read(output/'summary.json')
        manifest = {'schema': 'mutation-scope-v1', 'complete': False, 'fingerprint': fingerprint,
                    'policy': 'self-only-v1', 'project': parent['project'], 'dataset': str(dataset),
                    'dataset_fingerprint': parent['fingerprint'], 'dataset_files': parent['files'],
                    'source_environment': source_environment(repo)}
        write(output/'manifest.json', manifest)
        write(output/'scope.json', rows)
        write(output/'targets.json', problems)
        summary = {'functions': len(rows), 'unique_targets': len(problems), 'callee_targets': 0,
                   'target_pairs': len(rows), 'policy': 'self-only-v1', 'output': str(output)}
        write(output/'summary.json', summary)
        manifest.update(complete=True, files={n: file_hash(output/n) for n in
                                             ('scope.json', 'targets.json', 'summary.json')})
        write(output/'manifest.json', manifest)
    return summary


def load_scope(output, repo):
    output = Path(output).resolve()
    manifest = read(output/'manifest.json')
    if not manifest or not manifest.get('complete') or manifest.get('schema')!='mutation-scope-v1':
        raise ValueError('Missing or incomplete mutation scope')
    required = {'scope.json', 'targets.json'}
    if not isinstance(manifest.get("files"), dict) or not required.issubset(manifest["files"]):
        raise ValueError("Manifest missing required artifact hashes")
    if manifest['source_environment']!=source_environment(repo):
        raise ValueError('Mutation scope source/environment mismatch')
    for name, value in manifest['files'].items():
        if Path(name).name!=name or file_hash(output/name)!=value:
            raise ValueError('Mutation scope artifact mismatch')
    rows, targets = read(output/'scope.json'), read(output/'targets.json')
    if manifest.get('policy') == 'self-only-v1':
        ids = [p['problem_id'] for p in targets]
        if len(ids) != len(set(ids)) or len(rows) != len(ids):
            raise ValueError('Self-only scope has duplicate or missing targets')
        if {r['assertion_target'] for r in rows} != set(ids):
            raise ValueError('Self-only scope target mismatch')
        for row in rows:
            selected = row['mutation_targets']
            if (row.get('expansion') or len(selected) != 1 or selected[0]['kind'] != 'self'
                    or selected[0]['target'] != row['assertion_target']):
                raise ValueError('Self-only scope contains expansion or mismatched target')
    return rows, targets, manifest
