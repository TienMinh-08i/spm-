"""Offline self-only mutation scope, sealed against a prepared dataset."""
from specmind_eval.common.fingerprint import require_source_environment
import ast
from pathlib import Path

from specmind_eval.inputs.dataset import function_code, pytest_nodeid, evaluation_key
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
    rows = [{'assertion_target': evaluation_key(p), 'analysis_target': p['problem_id'], 'expansion': False,
             'body_line_count': body_line_count(function_code(p)),
             'mutation_targets': [{'target': p['problem_id'], 'kind': 'self',
                                  'coverage': 'observed',
                                  'observed_nodeids': [pytest_nodeid(t) for t in tests[evaluation_key(p)]]}]}
            for p in problems]
    from specmind_prep.source_index import source_index
    if parent.get('project') and 'source_root' in parent['project']:
        index, _ = source_index(repo, parent['project'])
    else:
        # Legacy prepared inputs already contain verified source records and
        # may describe a single module without a project/package configuration.
        source_fields = {'problem_id','source','module','parent_name','entry_point','repo_path',
                         'line','code_line','decorators','async'}
        index = {p['problem_id']:{k:v for k,v in p.items() if k in source_fields} for p in problems}
    targets = [dict(index[key], docstring=ast.get_docstring(ast.parse(index[key]['source']).body[0], clean=False))
               for key in sorted({p['problem_id'] for p in problems})]
    fingerprint = digest({'dataset': parent['fingerprint'], 'files': parent['files'],
                          'policy': 'self-only-tasks-v2'})
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
                    'policy': 'self-only-tasks-v2', 'project': parent['project'], 'dataset': str(dataset),
                    'dataset_fingerprint': parent['fingerprint'], 'dataset_files': parent['files'],
                    'source_environment': source_environment(repo)}
        write(output/'manifest.json', manifest)
        write(output/'scope.json', rows)
        write(output/'targets.json', targets)
        summary = {'functions': len(rows), 'unique_targets': len(targets), 'callee_targets': 0,
                   'target_pairs': len(rows), 'policy': 'self-only-tasks-v2', 'output': str(output)}
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
    require_source_environment(manifest['source_environment'], source_environment(repo), 'Mutation scope')
    for name, value in manifest['files'].items():
        if Path(name).name!=name or file_hash(output/name)!=value:
            raise ValueError('Mutation scope artifact mismatch')
    rows, targets = read(output/'scope.json'), read(output/'targets.json')
    if manifest.get('policy') == 'self-only-tasks-v2':
        ids = [p['problem_id'] for p in targets]
        task_ids = [r['assertion_target'] for r in rows]
        if len(ids) != len(set(ids)) or len(task_ids) != len(set(task_ids)):
            raise ValueError('Duplicate task or source target')
        for row in rows:
            selected = row['mutation_targets']
            if (row.get('expansion') or len(selected) != 1 or selected[0]['kind'] != 'self'
                    or selected[0]['target'] != row['analysis_target'] or row['analysis_target'] not in ids):
                raise ValueError('Invalid self-only task scope')
        problems, _, dm = load_prepared_dataset(manifest['dataset'],repo)
        if dm['fingerprint'] != manifest['dataset_fingerprint'] or dm['files'] != manifest['dataset_files']:
            raise ValueError('Scope dataset lineage mismatch')
        if {evaluation_key(p):p['problem_id'] for p in problems} != {r['assertion_target']:r['analysis_target'] for r in rows}:
            raise ValueError('Scope task ownership mismatch')
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
