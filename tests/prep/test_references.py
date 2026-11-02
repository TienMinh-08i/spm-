import json
import pytest

from specmind_prep.project import validate_project
from specmind_prep.references import references
from specmind_prep.source_index import source_index


@pytest.fixture
def sample(tmp_path):
    repo = tmp_path / 'repo'
    package = repo / 'src/acme'
    package.mkdir(parents=True)
    (repo / 'tests').mkdir()
    (package / '__init__.py').write_text('')
    (package / 'math.py').write_text('def target(x):\n    return x + 1\n')
    (package / 'other.py').write_text('def target(x):\n    return x - 1\n')
    (package / 'use.py').write_text('''from .math import target as calculate
from . import math as calc
from .other import target

def checkout(x):
    result = calculate(
        x
    )
    return calc.target(result)

def unrelated(x):
    return target(x)

def shadowed(calculate):
    return calculate(2)

def local_alias():
    alias = calculate
    return alias(1)

def outer():
    def inner():
        return calculate(1)
    return 0

class Service:
    def target(self, x):
        return x
    def call(self):
        return self.target(1)
''')
    project = validate_project({'name': 'demo', 'packages': ['acme']}, repo)
    return repo, project


def test_exact_target_aliases_locations_and_shadowing(sample):
    repo, project = sample
    index, _ = source_index(repo, project)
    result = references(repo, index, 'acme.math.target')
    assert result['target']['location'] == 'src/acme/math.py:1'
    assert [(r['caller'], r['line']) for r in result['callers']] == [
        ('acme.use.checkout', 6), ('acme.use.checkout', 9)]
    assert result['callers'][0]['expression'] == 'calculate(\n        x\n    )'
    assert {r['caller'] for r in result['unresolved_calls']} >= {
        'acme.use.shadowed', 'acme.use.local_alias', 'acme.use.Service.call'}
    assert not any(r['caller'] == 'acme.use.outer' for r in result['callers'])
    for row in result['callers']:
        lines = (repo / row['file']).read_text().splitlines()
        assert row['context'] == '\n'.join(lines[row['context_start_line'] - 1:row['context_end_line']])


def test_decorators_async_and_method_ambiguity(sample):
    repo, project = sample
    (repo / 'src/acme/decorated.py').write_text('''def deco(f):
    return f
@deco
@deco
async def target(x):
    return x
async def caller():
    return await target(1)
''')
    index, _ = source_index(repo, project)
    result = references(repo, index, 'acme.decorated.target')
    assert result['target']['line'] == 5
    assert result['target']['code_line'] == 3
    assert result['callers'][0]['caller'] == 'acme.decorated.caller'
    result = references(repo, index, 'acme.use.Service.target')
    assert not result['callers']
    assert any(r['caller'] == 'acme.use.Service.call' and r['name_matches_target']
               for r in result['unresolved_calls'])


def test_reassigned_global_and_no_execution(sample):
    repo, project = sample
    (repo / 'src/acme/unsafe.py').write_text('''raise RuntimeError("must never import target")
from .math import target
target = object()
def call():
    return target(1)
''')
    index, _ = source_index(repo, project)
    result = references(repo, index, 'acme.math.target')
    assert not any(r['caller'] == 'acme.unsafe.call' for r in result['callers'])
    assert any(r['caller'] == 'acme.unsafe.call' for r in result['unresolved_calls'])
    with pytest.raises(ValueError, match='Unknown function'):
        references(repo, index, 'target')
    with pytest.raises(ValueError, match='context_lines'):
        references(repo, index, 'acme.math.target', context_lines=-1)


def test_cli_json_and_no_overwrite(sample, tmp_path, capsys):
    from specmind_prep.cli import main
    repo, project = sample
    config = tmp_path / 'project.json'
    config.write_text(json.dumps(project))
    output = tmp_path / 'references.json'
    args = ['references', '--repo', str(repo), '--project', str(config),
            '--function', 'acme.math.target', '--output', str(output)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['target']['function'] == 'acme.math.target'
    before = output.read_bytes()
    assert main(args) == 1
    assert output.read_bytes() == before


def test_flat_layout(sample):
    repo, project = sample
    (repo / 'src/acme').rename(repo / 'acme')
    project = validate_project({**project, 'source_root': '.', 'python_path': ['.']}, repo)
    index, _ = source_index(repo, project)
    assert references(repo, index, 'acme.math.target')['target']['location'] == 'acme/math.py:1'
