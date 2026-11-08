"""Small preparation runner with explicit selection and separate mutation generators.

Run with python -m specmind_prep.prototype --config pilot.json.
The pilot requires explicit keep/drop decisions and explicit pytest nodeids.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from specmind_prep.selection import Sections as Sections, collect_inventory, selected_candidates as selected_candidates

from specmind_eval.common.fingerprint import digest, file_hash, source_environment
from specmind_eval.common.jsonl import write_jsonl, read_jsonl
from specmind_eval.common.storage import read, write
from specmind_eval.common.outputs import separate_output
from specmind_eval.inputs.prepared import load_prepared_dataset, load_prepared_mutants
from specmind_prep.docs_build import validate_docs
from specmind_prep.gather.gather import revision, validate_candidates
from specmind_prep.project import load_project
from specmind_prep.trace.trace import trace
from specmind_prep.filter.dataset import prepare_linked_dataset



def checked_docs(directory, commit):
    manifest = validate_docs(directory, commit)
    required = {'project.json', 'environment.json', 'checks.json', 'html/api_reference.html'}
    if not required <= set(manifest.get('files', {})):
        raise ValueError('Docs artifact missing required files')
    if read(directory/'checks.json').get('errors'):
        raise ValueError('Docs checks contain errors')
    return manifest



def seal(directory, manifest, names):
    manifest.update(complete=True, files={n: file_hash(directory/n) for n in names})
    write(directory/'manifest.json', manifest)


def self_scope(dataset, repo, output):
    from specmind_prep.scope.scope import prepare_scope, load_scope
    prepare_scope(dataset, repo, output)
    return load_scope(output, repo)


def local_mutants(scope, repo, output, vendor, timeout):
    """Use mutmut's pure source operators in a timed subprocess; never execute mutants."""
    rows, targets, parent = scope
    output.mkdir()
    write(output/'targets_input.json', targets)
    manifest = {'schema': 'prepared-mutants-v1', 'complete': False, 'generator': 'mutmut-2.5.1-source-api',
                'dataset_fingerprint': parent['dataset_fingerprint'], 'dataset_files': parent['dataset_files'],
                'scope_fingerprint': parent['fingerprint'], 'scope_files': parent['files'],
                'source_environment': source_environment(repo), 'project': parent['project']}
    write(output/'manifest.json', manifest)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(vendor), str(Path(__file__).resolve().parents[1])]),
               PYTHONUTF8='1')
    command = [sys.executable, '-m', 'specmind_prep.prototype_mutants', '--input', str(output/'targets_input.json'),
               '--output', str(output/'exports.json')]
    with (output/'generation.log').open('w', encoding='utf8') as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout)
    exports = read(output/'exports.json')
    functions = []
    for target, row in zip(targets, rows):
        key = target['problem_id']
        problem = dict(target, _expected_source=target['source'],
                       _expected_source_path=str(repo/target['repo_path']))
        mutants = [dict(m, id=key+'::'+m['id'], engine_id=m['id'], mutation_target=key,
                        kind='self', coverage='observed', mutation_problem=problem)
                   for m in exports[key]['mutants']]
        functions.append({'function': key, 'done': True, 'pending_targets': [],
                          'expansion': False, 'scope': row, 'mutants': mutants})
    write(output/'functions.json', functions)
    summary = {'functions': len(functions), 'unique_mutants': sum(len(r['mutants']) for r in functions),
               'per_function': {r['function']: len(r['mutants']) for r in functions},
               'generator': manifest['generator'], 'executed_mutants': 0,
               'meaning': 'Prepared source mutations, not assertion kills or semantic inequivalence proof.'}
    write(output/'summary.json', summary)
    manifest['fingerprint'] = digest({'scope': parent['fingerprint'], 'exports': file_hash(output/'exports.json')})
    seal(output, manifest, ['targets_input.json', 'functions.json', 'summary.json', 'exports.json', 'generation.log'])
    return summary


def run(config_path):
    config_path = Path(config_path).resolve()
    cfg = read(config_path)
    generator = cfg.get('mutant_generator', 'mutmut')
    if generator not in {'llm', 'mutmut'}:
        raise ValueError('mutant_generator must be llm or mutmut')
    if generator == 'llm' and not cfg.get('model'):
        raise ValueError('LLM preparation requires an explicit model')
    def path(key):
        return (config_path.parent/cfg[key]).resolve()
    repo, docs, output = path('repo'), path('docs'), path('output')
    separate_output(output, repo, docs)
    tests = cfg.get('tests', [])
    test_limit = cfg.get('test_limit', 10)
    if not isinstance(test_limit, int) or not 1 <= test_limit <= 40:
        raise ValueError('Test limit must be between 1 and 40')
    if not tests or len(tests) > test_limit or any('::' not in t or not (repo/t.split('::')[0]).is_file() for t in tests):
        raise ValueError(f'Requires 1 to {test_limit} explicit pytest nodeids')
    if cfg.get('through', 'mutants') not in {'trace', 'scope', 'mutants'}:
        raise ValueError('through must be trace, scope or mutants')
    if output.exists():
        raise ValueError('Output exists; use a new pilot directory')
    project = load_project(path('project'), repo, documented_only=True)
    if revision(repo) != project['commit']:
        raise ValueError('Source commit mismatch')
    output.mkdir(parents=True)
    report = {'schema': 'prep-pilot-v1', 'complete': False, 'steps': {}, 'llm': 'not_run',
              'scope': 'explicit small sample; not the final public API dataset'}
    write(output/'config.json', cfg)
    try:
        docs_manifest = checked_docs(docs, project['commit'])
        report['steps']['0'] = {'status': 'reused_verified', 'artifact': str(docs), 'digest': docs_manifest['output_digest']}
        rows, index = collect_inventory(repo, project, docs, docs_manifest, set(cfg.get('review_ids', [])))
        write_jsonl(output/'public_api_inventory.jsonl', rows)
        report['steps']['1'] = {'status': 'complete', 'functions': len(rows),
                              'meaning': 'RST proposals checked against built HTML; review list supplied explicitly'}
        from specmind_prep.selection import prepare_selection
        by_id = {r['function_id']: r for r in rows}
        full_selection = [dict(by_id.get(r['function_id'], {}), **r, target=None) for r in cfg['selection']]
        write_jsonl(output/'api_selection.jsonl', full_selection)
        report['steps']['2'] = {'status': 'sample_selected', 'rows': len(full_selection),
                                'author': 'explicit configured decisions'}
        directory = output/'candidates'
        prepare_selection(repo, path('project'), docs, output/'api_selection.jsonl', directory,
                          review_ids=cfg.get('review_ids', []), tests=tests, limit=cfg.get('selection_limit', 5))
        candidates = read_jsonl(directory/'problems.jsonl')
        validate_candidates(directory, repo)
        report['steps']['3'] = {'status': 'complete', 'candidates': len(candidates)}
        print('Step 4: trace selected tests', flush=True)
        report['steps']['4'] = trace(directory, repo, output/'links', tests=tests, timeout=cfg.get('timeout', 120))
        if cfg.get('through') == 'trace':
            report['complete'] = True
            report['completed_through'] = 'trace'
            return report
        print('Step 5: True/False probes', flush=True)
        report['steps']['5'] = prepare_linked_dataset(directory, output/'links', repo, output/'dataset', timeout=cfg.get('timeout', 120))
        if report['steps']['5']['pending_functions'] or not report['steps']['5']['ready_functions']:
            raise ValueError('Dataset pending or empty; inspect probe results')
        scope = self_scope(output/'dataset', repo, output/'scope')
        report['steps']['6'] = {'status': 'complete', 'policy': 'self-only', 'functions': len(scope[0])}
        if cfg.get('through') == 'scope':
            report['complete'] = True
            report['completed_through'] = 'scope'
            return report
        generator = cfg.get('mutant_generator', 'mutmut')
        if generator == 'llm':
            from specmind_prep.llm_mutants import prepare
            if not cfg.get('model'):
                raise ValueError('LLM preparation requires an explicit model')
            print('Step 7: cached line LLM mutants', flush=True)
            report['steps']['7'] = prepare(output/'scope', output/'dataset', repo, output/'mutants',
                                           cfg['model'], method='line', max_targets=len(scope[0]))
            report['llm'] = 'complete'
        elif generator == 'mutmut':
            print('Step 7: local mutmut source operators', flush=True)
            report['steps']['7'] = local_mutants(scope, repo, output/'mutants', path('mutmut_vendor'), cfg.get('timeout', 120))
        else:
            raise ValueError('mutant_generator must be llm or mutmut')
        _, _, dataset_manifest = load_prepared_dataset(output/'dataset', repo)
        load_prepared_mutants(output/'mutants', repo, dataset_manifest)
        report['complete'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        write(output/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.config), ensure_ascii=False, indent=2))
