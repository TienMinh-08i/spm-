"""Offline, one-level mutation scope: AST candidates plus observed direct calls."""
import ast
from specmind_prep.source_index import source_index
import json
import os
from pathlib import Path
import subprocess
import sys
from collections import defaultdict

from specmind_eval.inputs.dataset import function_code, pytest_nodeid
from specmind_eval.common.jsonl import read_jsonl
from specmind_eval.evaluate.pytest_runner import pythonpath_for, pytest_nodeid_batches
from specmind_eval.inputs.prepared import load_prepared_dataset
from specmind_eval.common.fingerprint import file_hash, source_environment
from specmind_eval.common.jsonl import write_jsonl
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.fingerprint import digest
from specmind_eval.common.storage import read, write, run_lock


def body_line_count(source):
    node = ast.parse(source).body[0]
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body.pop(0)
    return len('\n'.join(ast.unparse(n) for n in body).splitlines())




def static_candidates(problem, index):
    """Resolve conservative lexical self/module calls; dynamic calls need trace."""
    node = ast.parse(function_code(problem)).body[0]
    key = problem['problem_id']
    record = index[key]
    module = record['module']
    owner = key.rsplit('.', 1)[0]
    result = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        expression = ast.unparse(call.func)
        candidate = module+'.'+expression if isinstance(call.func, ast.Name) else None
        if expression.startswith(('self.', 'cls.', 'mcs.')):
            candidate = owner+'.'+expression.split('.', 1)[1]
        target = index.get(candidate)
        abstract = target and any(d.endswith('abstractmethod') or d=='overload' or d.endswith('.overload') for d in target['decorators'])
        result.append({'expression': expression, 'line': call.lineno,
                       'candidate': candidate if target and not abstract else None,
                       'resolution': 'lexical' if target and not abstract else 'needs_runtime_or_external'})
    return result


TRACE_PLUGIN_PATH = Path(__file__).resolve().parents[1] / "injected" / "scope_trace_plugin.py"
TRACE_PLUGIN = TRACE_PLUGIN_PATH.read_text(encoding="utf-8")


def prepare_scope(dataset, repo, output, *, resume=False, timeout=300):
    dataset, repo = Path(dataset).resolve(), Path(repo).resolve()
    output = separate_output(output, dataset, repo)
    problems, tests, manifest = load_prepared_dataset(dataset, repo)
    index, locations = source_index(repo)
    short = {p['problem_id'] for p in problems if body_line_count(function_code(p)) < 5}
    fingerprint = digest({'dataset': manifest['fingerprint'], 'files': manifest['files'],
                          'environment': source_environment(repo), 'code': digest({'scope': file_hash(__file__), 'plugin': file_hash(TRACE_PLUGIN_PATH),
                                          'source_index': file_hash(Path(__file__).resolve().parents[1] / 'source_index.py')}), 'timeout': timeout})
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous and not resume:
            raise ValueError('Scope exists; use --resume or new output')
        if previous and previous['fingerprint'] != fingerprint:
            raise ValueError('Scope fingerprint mismatch')
        write(output/'manifest.json', {'schema': 'mutation-scope-v1', 'fingerprint': fingerprint, 'complete': False,
                                      'dataset': str(dataset), 'dataset_fingerprint': manifest['fingerprint'],
                                      'dataset_files': manifest['files'], 'source_environment': source_environment(repo)})
        config = {'locations': [[p,line,key] for (p,line),key in locations.items()], 'short': sorted(short),
                  'allowed': {k:[pytest_nodeid(t) for t in v] for k,v in tests.items()}}
        write(output/'trace_config.json', config)
        (output/'scope_plugin.py').write_text(TRACE_PLUGIN, encoding='utf-8')
        nodes = sorted({pytest_nodeid(t) for rows in tests.values() for t in rows})
        batches = pytest_nodeid_batches(nodes, 500, 24000)
        evidence = []
        for i, batch in enumerate(batches):
            path = output/'trace'/f'{i:03d}.json'
            saved = read(path)
            if saved and saved['status']=='pass':
                evidence.append(saved['trace'])
                continue
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(output), pythonpath_for(repo)]),
                       PYTHONUTF8='1', SPECMIND_SCOPE_CONFIG=str(output/'trace_config.json'),
                       SPECMIND_SCOPE_RESULT=str(output/'trace'/f'{i:03d}.events.json'))
            env.pop('PYTEST_ADDOPTS',None)
            (output/'trace').mkdir(exist_ok=True)
            print(f'Scope trace [{i+1}/{len(batches)}]: {len(batch)} tests', flush=True)
            proc = subprocess.run([sys.executable,'-m','pytest','-q','--tb=short','-p','scope_plugin',*batch],
                                  cwd=repo,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
            trace = read(output/'trace'/f'{i:03d}.events.json', {})
            write(path, {'status': 'pass' if proc.returncode==0 else 'error', 'trace':trace,
                         'nodeids':batch,'returncode':proc.returncode,'logs':proc.stdout+proc.stderr})
            if proc.returncode:
                raise ValueError('Scope baseline failed; see '+str(path))
            evidence.append(trace)
        edges = defaultdict(set)
        external = defaultdict(set)
        for trace in evidence:
            for edge in trace.get('edges',[]):
                edges[(edge['caller'],edge['callee'])].update(edge['nodeids'])
            for key, values in trace.get('external',{}).items():
                external[key].update(values)
        rows, used = [], set()
        for problem in problems:
            key = problem['problem_id']
            targets = {key}
            lexical = static_candidates(problem,index) if key in short else []
            observed = {b for a,b in edges if a==key}
            if key in short:
                targets.update(observed)
                targets.update(c['candidate'] for c in lexical if c['candidate'])
            details = []
            for target in sorted(targets):
                if target not in index:
                    raise ValueError('Unknown target '+target)
                observed_nodes = sorted(edges.get((key,target),set())) if target!=key else [pytest_nodeid(t) for t in tests[key]]
                details.append({'target':target,'kind':'self' if target==key else 'callee',
                                'observed_nodeids':observed_nodes,
                                'coverage':'observed' if observed_nodes else 'lexical_unobserved'})
            used.update(targets)
            rows.append({'assertion_target':key,'body_line_count':body_line_count(function_code(problem)),
                         'expansion':key in short,'mutation_targets':details,'calls':lexical,
                         'external_calls':sorted(external[key]),
                         'resolution_note':'runtime observed direct calls plus conservative lexical candidates; no recursive expansion'})
        write(output/'scope.json', rows)
        write(output/'targets.json', [index[k] for k in sorted(used)])
        current = read(output/'manifest.json')
        current.update(complete=True, files={name:file_hash(output/name) for name in ('scope.json','targets.json')})
        write(output/'manifest.json',current)
        summary = {'functions':len(rows),'short_functions':len(short),'unique_targets':len(used),
                   'callee_targets':len(used-{p['problem_id'] for p in problems}),
                   'target_pairs':sum(len(r['mutation_targets']) for r in rows),'output':str(output)}
        write(output/'summary.json',summary)
    return summary


def load_scope(output, repo):
    output = Path(output).resolve()
    manifest = read(output/'manifest.json')
    if not manifest or not manifest.get('complete') or manifest.get('schema')!='mutation-scope-v1':
        raise ValueError('Missing or incomplete mutation scope')
    if manifest['source_environment']!=source_environment(repo):
        raise ValueError('Mutation scope source/environment mismatch')
    for name, value in manifest['files'].items():
        if Path(name).name!=name or file_hash(output/name)!=value:
            raise ValueError('Mutation scope artifact mismatch')
    return read(output/'scope.json'), read(output/'targets.json'), manifest
