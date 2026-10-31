"""Rebuild a collectable dataset in a new directory; never mutate old inputs."""
import os
import json
from pathlib import Path
import subprocess
import sys
from specmind_eval.common.jsonl import read_jsonl
from specmind_eval.inputs.dataset import pytest_nodeid
from specmind_eval.common.outputs import separate_output
from specmind_eval.evaluate.pytest_runner import pythonpath_for
from specmind_eval.common.fingerprint import digest
from specmind_eval.common.storage import write


def prepare_tests(tests, repo, output):
    tests, repo = Path(tests).resolve(), Path(repo).resolve()
    output = separate_output(output, repo, tests.parent)
    if output.exists():
        raise ValueError('Choose a new output directory')
    rows = read_jsonl(tests)
    if not rows:
        raise ValueError('Empty test dataset')
    env = dict(os.environ, PYTHONPATH=pythonpath_for(repo), PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env.pop('PYTEST_ADDOPTS', None)
    proc = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q'], cwd=repo,
                          env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    if proc.returncode:
        raise ValueError('Collection failed; no dataset written:\n' + proc.stdout[-4000:] + proc.stderr[-2000:])
    collected = {line.strip() for line in proc.stdout.splitlines() if '::' in line and '.py::' in line}
    valid, invalid = [], []
    for row in rows:
        item = dict(row, nodeid=pytest_nodeid(row))
        (valid if item['nodeid'] in collected else invalid).append(item)
    output.mkdir(parents=True)
    for name, items in [('tests_valid.jsonl', valid), ('tests_invalid.jsonl', invalid)]:
        (output/name).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in items), encoding='utf-8')
    summary = {'source': str(tests), 'source_sha256': digest(rows), 'links': len(rows),
               'valid_links': len(valid), 'invalid_links': len(invalid),
               'unique_valid_tests': len({r['nodeid'] for r in valid}),
               'meaning': 'collection only; no claim of passing tests or input coverage'}
    write(output/'summary.json', summary)
    (output/'collection.log').write_text(proc.stdout+proc.stderr, encoding='utf-8')
    return summary
