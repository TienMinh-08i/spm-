"""Export source excerpts of selected executions for the E experiment."""
import argparse
import hashlib
import json
from pathlib import Path

from run import PIN

def export(results, problems, output):
    rows = json.loads((results / 'api_contexts.json').read_text(encoding='utf8'))
    targets = {p['function_id']: p['problem_id'] for p in
               (json.loads(line) for line in problems.read_text(encoding='utf8').splitlines())}
    if {r['api'] for r in rows} != targets.keys() or len(rows) != len(targets):
        raise ValueError('Slice API inventory differs from the dataset')
    packs = {}
    for row in rows:
        examples = []
        for example in row['examples']:
            lines = [item['code'] for item in example['lines']]
            block = '\n'.join(lines)
            if block.strip() and block not in examples:
                examples.append(block)
        code = '\n\n'.join(examples)
        if not code or len(code) > 16000:
            raise ValueError('Missing or oversized code context for ' + row['api'])
        packs[row['api']] = dict(analysis_target=targets[row['api']], repository_context=code,
                                 metadata=dict(role='caller_slice', model='dynapyt-code-v1',
                                               status=row['status'], actual_chars=len(code)))
    output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(packs, ensure_ascii=False, indent=2).encode()
    (output/'packs.json').write_bytes(payload)
    manifest = dict(schema='dynapyt-code-v1', commit=PIN, count=len(packs),
                    sha256=hashlib.sha256(payload).hexdigest())
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('results','problems','output'):
        parser.add_argument('--'+arg, type=Path, required=True)
    args=parser.parse_args()
    export(args.results,args.problems,args.output)
