"""Pair assertion/suite evidence with identity checks, without executing tests."""
from pathlib import Path
from paper_eval_v2.common.outputs import separate_output
from .metrics import mutation_metrics
from .report import report
from paper_eval_v2.common.fingerprint import digest
from paper_eval_v2.common.storage import read, write, run_lock


def join_suite(run, suite, output):
    run, suite = Path(run).resolve(), Path(suite).resolve()
    original, neutral = read(run / 'manifest.json'), read(suite / 'manifest.json')
    if not original or not neutral or neutral.get('kind') != 'test-suite-only':
        raise ValueError('Expected assertion and offline suite manifests')
    output = separate_output(output, run, suite, original['config']['repo_root'])
    suites = {r['function']: r for r in read(suite / 'results.json', [])}
    records = read(run / 'results.json', [])
    if not records:
        raise ValueError('No assertion results')
    prepared = []
    for r in records:
        if not r.get('mutation'):
            prepared.append(r)
            continue
        s = suites.get(r['function'])
        if not s or not r.get('evaluation_identity') or r['evaluation_identity'] != s.get('evaluation_identity'):
            raise ValueError('Missing suite or incompatible source/tests/engine/environment: ' + r['function'])
        d = run / 'functions' / digest(r['function'])[:16]
        if digest(read(d / 'input.json')) != s.get('input_sha256'):
            raise ValueError('Saved function inputs differ')
        if r.get('preflight', {}).get('eligible_nodeids') != s.get('preflight', {}).get('eligible_nodeids'):
            raise ValueError('Baseline eligibility differs; cannot pair evidence')
        sources = {m['id']: digest(m['function_source']) for m in read(d / 'mutants.json')['mutants']}
        sm = {m['id']: m for m in s['mutants']}
        if len(sm) != len(s['mutants']):
            raise ValueError('Duplicate suite mutant IDs')
        if any(mid not in sources or m['source_sha256'] != sources[mid] for mid, m in sm.items()):
            raise ValueError('Mutant source hash mismatch')
        # The same suite may be reused across context arms only when the complete
        # mutation installation payload also matches (callee targets included).
        if digest(read(d / 'mutants.json')) != s.get('bundle_sha256'):
            raise ValueError('Suite mutant bundle differs')
        merged = []
        for m in r['mutation']['mutants']:
            if m['id'] not in sources:
                raise ValueError('Unknown assertion mutant ID')
            n = sm.get(m['id'], {})
            merged.append({**m, 'suite_killed': n.get('suite_killed'),
                           'neutral_status': n.get('neutral_status', 'not_run'),
                           'neutral_arm': n.get('neutral_arm'), 'target_error': n.get('target_error')})
        prepared.append({**r, 'mutation': {'mutants': merged, 'metrics': mutation_metrics(merged)}})
    with run_lock(output):
        if (output / 'manifest.json').exists():
            raise ValueError('Join output already exists; choose a new directory')
        write(output / 'manifest.json', {'kind': 'paired-report', 'assertion_run': str(run), 'suite_run': str(suite),
                                         'source_run_fingerprint': original['fingerprint'],
                                         'source_results_sha256': digest(records)})
        for r in prepared:
            name = digest(r['function'])[:16]
            write(output / 'functions' / name / 'result.json', r)
            state = read(run / 'functions' / name / 'generation.json')
            if state:
                write(output / 'functions' / name / 'generation.json', state)
        return report(output)
