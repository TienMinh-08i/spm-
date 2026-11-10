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
    scope, dataset, repo = [Path(p).resolve() for p in (scope, dataset, repo)]
    output = separate_output(output, scope, dataset, repo)
    rows, targets, parent = load_scope(scope, repo)
    _, _, dm = load_prepared_dataset(dataset, repo)
    if parent['dataset_fingerprint'] != dm['fingerprint'] or parent['dataset_files'] != dm['files']:
        raise ValueError('Scope does not belong to this dataset')
    return _generate(rows, targets, parent, dm, repo, output, model, count, call, max_targets, reuse_from, method)


def prepare_pool(candidates, repo, output, model, *, reuse_from=None, max_new_requests=0,
                 new_max_tokens=32768, call=request, target_ids=None):
    """Generate unique source targets before probing; never claim dataset readiness."""
    from specmind_prep.gather.gather import validate_candidates
    from specmind_prep.source_index import source_index
    from specmind_eval.common.jsonl import read_jsonl

    candidates, repo = Path(candidates).resolve(), Path(repo).resolve()
    output = separate_output(output, candidates, repo)
    parent = validate_candidates(candidates, repo)
    tasks = read_jsonl(candidates/'problems.jsonl')
    if target_ids:
        wanted = set(target_ids)
        tasks = [p for p in tasks if p['problem_id'] in wanted]
        if {p['problem_id'] for p in tasks} != wanted:
            raise ValueError('Unknown source-pool targets')
    index, _ = source_index(repo, parent['project'])
    keep = {p['problem_id']: p for p in tasks if p.get('decision') == 'keep'}
    targets = []
    for key in sorted({p['problem_id'] for p in tasks}):
        if key not in index:
            raise ValueError('Analysis target missing from source: '+key)
        target = dict(index[key], docstring=ast.get_docstring(ast.parse(index[key]['source']).body[0], clean=False))
        # Use the target's own public documentation when available, never an
        # arbitrary delegating API's contract for a shared helper.
        if key in keep:
            target['document'] = keep[key].get('document')
        targets.append(target)
    bindings = [{'task_id': p['task_id'], 'function_id': p['function_id'],
                 'problem_id': p['problem_id'], 'decision': p['decision']} for p in tasks]
    if len({p['task_id'] for p in bindings}) != len(bindings):
        raise ValueError('Duplicate API task identity')
    rows = [{'mutation_targets': [{'target': p['problem_id'], 'kind': 'self'}]} for p in targets]
    return _generate(rows, targets, parent, None, repo, output, model, 10, call, 22, reuse_from, 'line',
                     bindings=bindings, candidates=candidates, max_new_requests=max_new_requests,
                     new_max_tokens=new_max_tokens)


def load_target_pool(output, repo, candidates=None):
    """Read a source pool. This is deliberately not a prepared evaluation corpus."""
    from specmind_eval.common.fingerprint import require_source_environment

    output, repo = Path(output).resolve(), Path(repo).resolve()
    manifest = read(output/'manifest.json', {})
    if manifest.get('schema') != 'llm-target-pool-v1' or not manifest.get('complete'):
        raise ValueError('Target pool missing or incomplete')
    required = {'targets_input.json', 'task_bindings.json', 'functions.json', 'summary.json',
                'exports.json', 'checkpoints.json'}
    if not required <= manifest.get('files', {}).keys():
        raise ValueError('Target pool missing required hashes')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or file_hash(output/name) != expected:
            raise ValueError('Target pool artifact mismatch: '+name)
    require_source_environment(manifest['source_environment'], source_environment(repo), 'Target pool')
    if candidates is not None:
        from specmind_prep.gather.gather import validate_candidates
        parent = validate_candidates(candidates, repo)
        if (parent['fingerprint'] != manifest['candidate_fingerprint']
                or parent['files'] != manifest['candidate_files']):
            raise ValueError('Pool does not belong to these candidates')
    functions = read(output/'functions.json')
    keys = [f['function'] for f in functions]
    targets = read(output/'targets_input.json')
    bindings = read(output/'task_bindings.json')
    if (len(keys) != len(set(keys)) or set(keys) != {p['problem_id'] for p in targets}
            or {b['problem_id'] for b in bindings} != set(keys)
            or len({b['task_id'] for b in bindings}) != len(bindings)):
        raise ValueError('Target pool task/target identity mismatch')
    for row in functions:
        if not row.get('done') or any(m['mutation_target'] != row['function'] for m in row['mutants']):
            raise ValueError('Target pool mutant ownership mismatch')
    return {f['function']: f for f in functions}, bindings, manifest


def _generate(rows, targets, parent, dm, repo, output, model, count, call, max_targets, reuse_from, method,
              *, bindings=None, candidates=None, max_new_requests=None, new_max_tokens=None):
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
    if max_new_requests is not None and (type(max_new_requests) is not int or max_new_requests < 0):
        raise ValueError('max_new_requests must be a nonnegative integer')
    if new_max_tokens is not None and (type(new_max_tokens) is not int or new_max_tokens < max_tokens):
        raise ValueError('new_max_tokens must be at least the standard token budget')
    token_limits, planned = {}, []
    for target in targets:
        key, slug = target['problem_id'], digest(target['problem_id'])[:16]
        prompt = messages(target, count) if method == 'function' else line_mutants.messages(target)
        previous_request = read(reuse_from/(slug+'.request.json'), {}) if reuse_manifest else {}
        reusable = (reuse_manifest and slug+'.request.json' in reuse_manifest['files']
                    and slug+'.response.json' in reuse_manifest['files']
                    and previous_request.get('model') == model and previous_request.get('messages') == prompt
                    and previous_request.get('temperature') == temperature
                    and previous_request.get('max_tokens') == max_tokens)
        token_limits[key] = max_tokens if reusable else (new_max_tokens or max_tokens)
        if not reusable and not (output/(slug+'.request.json')).exists() and not (output/(slug+'.response.json')).exists():
            planned.append(key)
    if max_new_requests is not None and len(planned) > max_new_requests:
        raise ValueError(f'Would require {len(planned)} new requests, exceeding budget {max_new_requests}; '
                         'no paid request made')
    identity = digest({'scope': parent['fingerprint'], 'scope_files': parent['files'],
                       'model': model, 'count': count if method == 'function' else None,
                       'system': SYSTEM if method == 'function' else protocol,
                       'method': method, 'max_tokens': max_tokens, 'temperature': temperature,
                       'token_limits': token_limits, 'max_new_requests': max_new_requests,
                       'code': file_hash(__file__)})
    with run_lock(output):
        previous = read(output/'manifest.json')
        if previous and previous.get('fingerprint') != identity:
            raise ValueError('LLM settings changed; choose a new output directory')
        if previous and previous.get('complete'):
            if dm is None:
                load_target_pool(output, repo, candidates)
            else:
                load_prepared_mutants(output, repo, dm)
            return read(output/'summary.json')
        manifest = {'schema': 'prepared-mutants-v1', 'complete': False, 'fingerprint': identity,
                    'generator': 'llm', 'model': model, 'count_requested_per_function': count if method == 'function' else None,
                    'mutation_method': method, 'protocol': protocol,
                    'max_tokens': max_tokens, 'temperature': temperature,
                    'dataset_fingerprint': dm['fingerprint'] if dm else None,
                    'dataset_files': dm['files'] if dm else None,
                    'scope_fingerprint': parent['fingerprint'], 'scope_files': parent['files'],
                    'source_environment': source_environment(repo), 'project': parent['project']}
        if dm is None:
            manifest.update(schema='llm-target-pool-v1', dataset_ready=False,
                            candidate_fingerprint=parent['fingerprint'], candidate_files=parent['files'],
                            token_limits=token_limits, max_new_requests=max_new_requests)
            for field in ('dataset_fingerprint', 'dataset_files', 'scope_fingerprint', 'scope_files'):
                manifest.pop(field)
        write(output/'manifest.json', manifest)
        write(output/'targets_input.json', targets)
        functions, all_exports, files = [], {}, ['targets_input.json', 'functions.json', 'summary.json', 'exports.json', 'checkpoints.json']
        if bindings is not None:
            write(output/'task_bindings.json', bindings)
            files.append('task_bindings.json')
        new_requests, reused, checkpoints = 0, [], {}
        for target in targets:
            key = target['problem_id']; slug = digest(key)[:16]
            target_tokens = token_limits[key]
            checkpoints[key] = {'status': 'running'}
            write(output/'checkpoints.json', checkpoints)
            try:
                raw_path, prompt_path = output/(slug+'.response.json'), output/(slug+'.request.json')
                prompt = messages(target, count) if method == 'function' else line_mutants.messages(target)
                raw = read(raw_path)
                if raw is None and not prompt_path.exists() and reuse_manifest:
                    old_prompt = read(reuse_from/prompt_path.name, {})
                    if (old_prompt.get('model') == model and old_prompt.get('messages') == prompt
                            and old_prompt.get('max_tokens') == target_tokens
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
                    write(prompt_path, {'model': model, 'messages': prompt, 'max_tokens': target_tokens,
                                        'temperature': temperature, 'mutation_method': method,
                                        'timeout': 120, 'max_retries': 0, 'status': 'request_started'})
                    print('LLM request: '+key, flush=True)
                    new_requests += 1
                    raw = (request(prompt, model, temperature=temperature, max_tokens=target_tokens)
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
                                kind='self', coverage='not_probed' if dm is None else 'observed',
                                mutation_problem=problem) for m in exported['mutants']]
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
        if bindings is not None:
            summary.update(api_tasks=len(bindings), unique_targets=len(targets), dataset_ready=False)
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
        if dm is None:
            load_target_pool(output, repo, candidates)
        else:
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
