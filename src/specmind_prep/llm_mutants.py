"""Small cached LLM mutant pilot. One request per target, separate from mutmut."""
import argparse
import ast
import difflib
import json
import os
import shutil
from pathlib import Path

from specmind_eval.common.fingerprint import digest, file_hash, source_environment
from specmind_eval.common.storage import read, write, run_lock
from specmind_eval.common.outputs import separate_output
from specmind_eval.inputs.prepared import load_prepared_dataset, load_prepared_mutants
from specmind_prep.scope.scope import load_scope
from specmind_prep.mutants.worker import executable_ast

SYSTEM = '''Generate Python function mutants for mutation-testing research.
Return only a JSON object with a "mutants" array. Each item must contain
"function_source" (the complete replacement function) and "reason" (the changed behavior).
Preserve the function name, signature including defaults/annotations, and decorators exactly.
Create distinct, plausible behavioral bugs, one conceptual bug per mutant.
Do not add imports, I/O, network, filesystem operations, or unrelated side effects.
Use only names available in the original function. Do not fix the original implementation.
Do not include markdown fences. Fewer distinct meaningful mutants is preferable to duplicates.
'''


def messages(target, count):
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': f'Produce at most {count} distinct mutants for this function.\n'
             + target['source']}]


def request(prompt, model, *, temperature=None, max_tokens=7000):
    from openai import OpenAI
    key = os.getenv('OPENAI_API_KEY') or os.getenv('OPENROUTER_API_KEY')
    if not key:
        raise ValueError('No OPENAI_API_KEY or OPENROUTER_API_KEY configured')
    base = os.getenv('OPENAI_BASE_URL')
    if not base and not os.getenv('OPENAI_API_KEY'):
        base = 'https://openrouter.ai/api/v1'
    client = OpenAI(api_key=key, base_url=base, timeout=120, max_retries=0)
    options = {'model': model, 'messages': prompt}
    official = not base or base.rstrip('/') == 'https://api.openai.com/v1'
    options['max_completion_tokens' if official and model.startswith(('gpt-5', 'o1', 'o3', 'o4')) else 'max_tokens'] = max_tokens
    if temperature is not None:
        options['temperature'] = temperature
    response = client.chat.completions.create(**options)
    return response.model_dump(mode='json')


def filter_response(source, content, count):
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get('mutants'), list):
        raise ValueError('Expected JSON object with mutants array')
    if len(payload['mutants']) > count:
        raise ValueError('Response exceeds requested mutant limit')
    original = ast.parse(source).body[0]
    def signature(node):
        return (type(node).__name__, node.name, ast.dump(node.args),
                ast.dump(node.returns) if node.returns else None,
                [ast.dump(d) for d in node.decorator_list])
    seen, accepted, rejected = {executable_ast(source)}, [], []
    for i, item in enumerate(payload['mutants']):
        try:
            if not isinstance(item, dict) or not isinstance(item.get('function_source'), str):
                raise ValueError('Missing function_source')
            mutated = item['function_source']
            tree = ast.parse(mutated)
            if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
                raise ValueError('Expected exactly one function')
            if signature(tree.body[0]) != signature(original):
                raise ValueError('Changed name/signature/decorators')
            if any(isinstance(n, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)) for n in ast.walk(tree)):
                raise ValueError('Unsupported import/global/nonlocal')
            compile('from __future__ import annotations\n'+mutated, '<llm-mutant>', 'exec')
            key = executable_ast(mutated)
            if key in seen:
                raise ValueError('Unchanged executable AST or duplicate')
            seen.add(key)
            accepted.append({'id': str(i), 'function_source': mutated, 'source_sha256': digest(mutated),
                             'reason': str(item.get('reason', '')), 'generator': 'llm',
                             'diff': ''.join(difflib.unified_diff(source.splitlines(True), mutated.splitlines(True)))})
        except (ValueError, SyntaxError) as exc:
            rejected.append({'id': str(i), 'reason': str(exc)})
    return {'discovered': len(payload['mutants']), 'mutants': accepted, 'skipped': rejected}


def prepare(scope, dataset, repo, output, model, count=10, call=request, max_targets=2, reuse_from=None, method='function'):
    if not model or not 1 <= count <= 10:
        raise ValueError('Explicit model and count between 1 and 10 required')
    if method not in {'function', 'line'}:
        raise ValueError('method must be function or line')
    max_tokens, temperature = (8192, 0.0) if method == 'line' else (7000, None)
    protocol = {}
    if method == 'line':
        from specmind_prep import line_mutants
        protocol = {'source_reference_sha256': line_mutants.REFERENCE_SHA256,
                    'module_sha256': file_hash(line_mutants.__file__),
                    'system_prompt': line_mutants.SYSTEM_PROMPT, 'user_template': line_mutants.USER_TEMPLATE,
                    'alternatives_per_line': 5}
    scope, dataset, repo = [Path(p).resolve() for p in (scope, dataset, repo)]
    output = separate_output(output, scope, dataset, repo)
    rows, targets, parent = load_scope(scope, repo)
    _, _, dm = load_prepared_dataset(dataset, repo)
    if parent['dataset_fingerprint'] != dm['fingerprint'] or parent['dataset_files'] != dm['files']:
        raise ValueError('Scope does not belong to this dataset')
    if not isinstance(max_targets, int) or not 1 <= max_targets <= 22:
        raise ValueError('max_targets must be between 1 and 22')
    if not 1 <= len(targets) <= max_targets or any(len(r['mutation_targets']) != 1 or r['mutation_targets'][0]['kind'] != 'self' for r in rows):
        raise ValueError(f'LLM run limited to {max_targets} self-only targets')
    reuse_manifest = None
    if reuse_from:
        reuse_from = Path(reuse_from).resolve()
        separate_output(output, reuse_from)
        reuse_manifest = read(reuse_from/'manifest.json', {})
        if not reuse_manifest.get('complete') or reuse_manifest.get('generator') != 'llm':
            raise ValueError('Reuse artifact must be a complete LLM corpus')
        for name, expected in reuse_manifest.get('files', {}).items():
            if Path(name).name != name or file_hash(reuse_from/name) != expected:
                raise ValueError('Reuse artifact hash mismatch')
    identity = digest({'scope': parent['fingerprint'], 'scope_files': parent['files'],
                       'model': model, 'count': count if method == 'function' else None,
                       'system': SYSTEM if method == 'function' else protocol,
                       'method': method, 'max_tokens': max_tokens, 'temperature': temperature,
                       'code': file_hash(__file__)})
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous and previous.get('fingerprint') != identity:
            raise ValueError('LLM settings changed; choose a new output directory')
        if previous and previous.get('complete'):
            load_prepared_mutants(output, repo, dm)
            return read(output/'summary.json')
        manifest = {'schema': 'prepared-mutants-v1', 'complete': False, 'fingerprint': identity,
                    'generator': 'llm', 'model': model, 'count_requested_per_function': count if method == 'function' else None,
                    'mutation_method': method, 'protocol': protocol,
                    'max_tokens': max_tokens, 'temperature': temperature,
                    'dataset_fingerprint': dm['fingerprint'], 'dataset_files': dm['files'],
                    'scope_fingerprint': parent['fingerprint'], 'scope_files': parent['files'],
                    'source_environment': source_environment(repo), 'project': parent['project']}
        write(output/'manifest.json', manifest)
        write(output/'targets_input.json', targets)
        functions, all_exports, files = [], {}, ['targets_input.json', 'functions.json', 'summary.json', 'exports.json', 'checkpoints.json']
        new_requests, reused, checkpoints = 0, [], {}
        for target in targets:
            key = target['problem_id']; slug = digest(key)[:16]
            checkpoints[key] = {'status': 'running'}
            write(output/'checkpoints.json', checkpoints)
            try:
                raw_path, prompt_path = output/(slug+'.response.json'), output/(slug+'.request.json')
                prompt = messages(target, count) if method == 'function' else line_mutants.messages(target)
                raw = read(raw_path)
                if raw is None and not prompt_path.exists() and reuse_manifest:
                    old_prompt = read(reuse_from/prompt_path.name, {})
                    if (old_prompt.get('model') == model and old_prompt.get('messages') == prompt
                            and old_prompt.get('max_tokens') == max_tokens
                            and old_prompt.get('temperature') == temperature
                            and raw_path.name in reuse_manifest['files'] and prompt_path.name in reuse_manifest['files']):
                        shutil.copy2(reuse_from/prompt_path.name, prompt_path)
                        shutil.copy2(reuse_from/raw_path.name, raw_path)
                        raw = read(raw_path)
                        reused.append(key)
                        write(output/(slug+'.reuse.json'), {'from': str(reuse_from),
                              'response_sha256': file_hash(raw_path), 'prompt_sha256': file_hash(prompt_path)})
                if (output/(slug+'.reuse.json')).exists():
                    files.append(slug+'.reuse.json')
                if raw is None:
                    if prompt_path.exists():
                        raise ValueError('Previous request has no saved response. Inspect before making another paid request: '+str(prompt_path))
                    write(prompt_path, {'model': model, 'messages': prompt, 'max_tokens': max_tokens,
                                        'temperature': temperature, 'mutation_method': method,
                                        'timeout': 120, 'max_retries': 0, 'status': 'request_started'})
                    print('LLM request: '+key, flush=True)
                    new_requests += 1
                    raw = (request(prompt, model, temperature=temperature, max_tokens=max_tokens)
                           if call is request else call(prompt, model))
                    write(raw_path, raw)  # Paid output persisted before parsing.
                choice = raw['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise ValueError('Incomplete LLM response; inspect '+str(raw_path))
                exported = (filter_response(target['source'], choice['message']['content'], count)
                            if method == 'function' else line_mutants.filter_response(target['source'], choice['message']['content']))
                if exported['discovered'] != len(exported['mutants'])+len(exported['skipped']):
                    raise ValueError('Mutant filtering accounting mismatch')
                exported['usage'] = raw.get('usage'); exported['returned_model'] = raw.get('model')
                all_exports[key] = exported
                problem = dict(target, _expected_source=target['source'], _expected_source_path=str(repo/target['repo_path']))
                mutants = [dict(m, id=key+'::llm::'+m['id'], engine_id=m['id'], mutation_target=key,
                                kind='self', coverage='observed', mutation_problem=problem) for m in exported['mutants']]
                functions.append({'function': key, 'done': True, 'pending_targets': [], 'expansion': False, 'mutants': mutants})
                files.extend([raw_path.name, prompt_path.name])
                checkpoints[key] = {'status': 'complete', 'mutants': len(mutants),
                                    'response': raw_path.name, 'request': prompt_path.name}
            except Exception as exc:
                # Keep paid raw output and an explicit pending target. Never retry a
                # request whose response may have been lost, even on resume.
                checkpoints[key] = {'status': 'pending', 'error_type': type(exc).__name__,
                                    'error': 'Saved response could not be processed' if raw_path.exists()
                                             else 'Request has no saved response; inspect request before retry',
                                    'response': raw_path.name, 'request': prompt_path.name}
            write(output/'checkpoints.json', checkpoints)
        write(output/'functions.json', functions); write(output/'exports.json', all_exports)
        summary = {'generator': 'llm', 'model': model, 'functions': len(functions),
                   'mutation_method': method,
                   'per_function': {r['function']: len(r['mutants']) for r in functions},
                   'unique_mutants': sum(len(r['mutants']) for r in functions),
                   'skipped': sum(len(r['skipped']) for r in all_exports.values()), 'executed_mutants': 0}
        summary.update(new_requests_this_execution=new_requests, reused_functions_this_execution=reused,
                       pending_targets=[k for k, v in checkpoints.items() if v['status'] != 'complete'])
        if method == 'line':
            summary['line_coverage'] = {key: row['coverage'] for key,row in all_exports.items()}
            manifest['count_requested_per_function'] = None
            manifest['alternatives_requested_per_line'] = 5
        write(output/'summary.json', summary)
        if summary['pending_targets']:
            manifest['pending_targets'] = summary['pending_targets']
            write(output/'manifest.json', manifest)
            raise ValueError('LLM preparation incomplete; inspect checkpoints.json; no automatic paid retry')
        manifest.update(complete=True, files={n: file_hash(output/n) for n in files})
        write(output/'manifest.json', manifest)
        load_prepared_mutants(output, repo, dm)
        return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['scope', 'dataset', 'repo', 'output']:
        p.add_argument('--'+name, required=True, type=Path)
    p.add_argument('--model', required=True); p.add_argument('--count', type=int, default=10)
    p.add_argument('--max-targets', type=int, default=2)
    p.add_argument('--reuse-from', type=Path)
    p.add_argument('--method', choices=['function', 'line'], default='function')
    args = p.parse_args()
    print(json.dumps(prepare(**vars(args)), indent=2))
