"""Publish verified context inputs without changing evaluation tests or mutants."""
import ast
import hashlib
import json
import subprocess
import textwrap
import zipfile
from pathlib import Path

from specmind_eval.context.callgraph import CallGraph, canonical_id, source_hash_matches
from specmind_eval.common.storage import write


def prepare_context_assets(graph_dir, usage_zip, repo, output):
    graph_dir, usage_zip, repo, output = map(lambda p: Path(p).resolve(),
                                           (graph_dir, usage_zip, repo, output))
    if output.exists():
        raise ValueError('Choose a new context asset directory')
    graph = CallGraph.load(graph_dir)
    manifest = json.loads((graph_dir/'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['source_files'].items():
        path = (repo/name).resolve()
        if not path.is_relative_to(repo) or not path.is_file() or not source_hash_matches(path.read_bytes(), expected):
            raise ValueError('Graph source differs from checkout: ' + name)
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    with zipfile.ZipFile(usage_zip) as z:
        origin = json.loads(z.read('manifest.json'))
        for name, expected in origin['files'].items():
            if hashlib.sha256(z.read(name)).hexdigest() != expected:
                raise ValueError('ZIP checksum mismatch: ' + name)
        rows = [json.loads(line) for line in z.read('marshmallow_tests_57_validated_4_3_0.jsonl').decode().splitlines() if line.strip()]
        calls = {(r['function_id'], r['nodeid']): r for r in json.loads(z.read('runtime_calls.json'))
                 if r['phase'] == 'call' and r['test_status'] == 'passed'}
        collected = {r['nodeid']: r for r in json.loads(z.read('collected_tests.json'))}
        results = json.loads(z.read('test_results.json'))
    sources, usage, seen = {}, [], set()
    def normalized(node):
        # Whitespace in documentation does not change the test executable AST.
        node = ast.parse(ast.unparse(node))
        for n in ast.walk(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str):
                    n.body[0].value.value = ' '.join(n.body[0].value.value.split())
        return ast.dump(node, include_attributes=False)
    for row in rows:
        target, nodeid = row['problem_id'], canonical_id(row['test_id'])
        if target not in graph.nodes or (target, nodeid) in seen:
            raise ValueError('Unknown or duplicate runtime usage: ' + target + ' ' + nodeid)
        seen.add((target, nodeid))
        if not row['observed_nodeids'] or len(set(row['observed_nodeids'])) != len(row['observed_nodeids']):
            raise ValueError('Missing/duplicate observed parameter cases')
        for n in row['observed_nodeids']:
            if ((target, n) not in calls or collected[n]['base_nodeid'] != nodeid
                    or any(results[n].get(p, {}).get('outcome') != 'passed' for p in ('setup', 'call', 'teardown'))):
                raise ValueError('Runtime usage lacks passing call evidence: ' + n)
        if sum(calls[target, n]['calls'] for n in row['observed_nodeids']) != row['observed_calls']:
            raise ValueError('Runtime call count mismatch')
        if nodeid not in sources:
            filename, *names = nodeid.split('::')
            path = (repo/filename).resolve()
            if not path.is_relative_to(repo):
                raise ValueError('Test source escapes checkout')
            node = ast.parse(path.read_text(encoding='utf-8-sig'))
            for name in names:
                matches = [n for n in node.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
                if len(matches) != 1:
                    raise ValueError('Cannot resolve test source: ' + nodeid)
                node = matches[0]
            sources[nodeid] = ast.unparse(node)
            # Include any additional test files in checkout verification.
            manifest['source_files'][filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = ast.parse(textwrap.dedent(row['test_code'].replace('\r\n', '\n')))
        if normalized(ast.parse(sources[nodeid])) != normalized(expected):
            raise ValueError('Runtime usage test source mismatch: ' + nodeid)
        usage.append({'function_id': target, 'nodeid': nodeid, 'file': nodeid.split('::')[0],
                      'source': sources[nodeid], 'observed_nodeids': row['observed_nodeids'],
                      'observed_calls': row['observed_calls']})
    output.mkdir(parents=True)
    for name in ('nodes.json', 'edges.json'):
        (output/name).write_bytes((graph_dir/name).read_bytes())
    write(output/'runtime_usage.json', usage)
    manifest['artifact_sha256']['runtime_usage.json'] = hashlib.sha256((output/'runtime_usage.json').read_bytes()).hexdigest()
    manifest['provenance'] = {'original_graph_commit': manifest['git_commit'],
                              'original_manifest_sha256': graph.manifest_sha256,
                              'usage_source_commit': origin['source_commit'],
                              'usage_zip_sha256': hashlib.sha256(usage_zip.read_bytes()).hexdigest(),
                              'verification': 'All graph source files hash-match; runtime test AST and call evidence verified against checkout.'}
    manifest['git_commit'] = commit
    manifest['repo_root'] = str(repo)
    manifest['source_tree_sha256'] = hashlib.sha256(json.dumps(manifest['source_files'], sort_keys=True).encode()).hexdigest()
    manifest['source_tree_hash_format'] = 'sha256(sorted-json-source_files)'
    write(output/'manifest.json', manifest)
    CallGraph.load(output, repo)
    return {'functions': len({r['function_id'] for r in usage}), 'usage_links': len(usage),
            'unique_tests': len(sources), 'verified_commit': commit, 'output': str(output)}
