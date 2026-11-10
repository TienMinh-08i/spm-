import json
import subprocess

import pytest

from specmind_prep.project import validate_project
from specmind_prep.public_api import analyze, inventory
from specmind_prep.references import public_calls


@pytest.fixture
def sample(tmp_path):
    repo = tmp_path / 'checkout'
    for directory in ('src/acme', 'tests', 'docs'):
        (repo / directory).mkdir(parents=True)
    (repo / 'src/acme/__init__.py').write_text('from .api import f as public_f, Thing\n__all__ = ["public_f", "Thing"]\n')
    (repo / 'src/acme/api.py').write_text('''def f(x):
    """Public function."""
    return x
class Thing:
    """Public class."""
    def run(self):
        """Public method."""
        return f(1)
    def _secret(self):
        """Private method."""
        pass
    @property
    def value(self):
        """Property, not a callable API."""
        return 1
''')
    (repo / 'src/acme/hidden.py').write_text('def hidden():\n    """Private API."""\n    pass\n')
    (repo / 'docs/api_reference.rst').write_text('''.. toctree::
   :caption: API Reference

   api

.. toctree::
   :caption: Private API

   hidden
''')
    (repo / 'docs/api.rst').write_text('.. automodule:: acme\n   :members:\n\n.. autofunction:: acme.api.f\n')
    (repo / 'docs/hidden.rst').write_text('.. automodule:: acme.hidden\n   :members:\n')
    (repo / 'tests/test_calls.py').write_text('''from acme import public_f, Thing
public_f(0)
class TestAPI:
    value = public_f(1)
    def test_usage(self):
        public_f(2)
        public_f(3)
        obj = Thing()
        obj.run()
        def nested():
            return public_f(4)
    def test_shadow(self, public_f):
        return public_f(5)
''')
    project = validate_project({'name': 'demo', 'packages': ['acme'], 'docs_dir': 'docs'}, repo)
    return repo, project


def test_inventory_aliases_private_pages_and_properties(sample):
    repo, project = sample
    result, _ = inventory(repo, project)
    rows = {r['definition']: r for r in result['api']}
    assert set(rows) == {'acme.api.f', 'acme.api.Thing', 'acme.api.Thing.run'}
    assert rows['acme.api.f']['public_names'] == ['acme.api.f', 'acme.public_f']
    assert result['excluded_pages'] == ['docs/hidden.rst']
    assert any(r['kind'] == 'property' for r in result['excluded_members'])


def test_explicit_members_and_exclusion(sample):
    repo, project = sample
    (repo / 'docs/api.rst').write_text('.. autoclass:: acme.Thing\n   :members: run, _secret\n   :exclude-members: run\n')
    result, _ = inventory(repo, project)
    assert [r['definition'] for r in result['api']] == ['acme.api.Thing']
    assert result['excluded_members'][0]['symbol'] == 'acme.Thing._secret'


def test_batch_calls_scope_alias_and_uncertainty(sample):
    repo, project = sample
    api, model = inventory(repo, project)
    calls, unknown, scan = public_calls(repo, project, api['api'], model)
    f_calls = [r for r in calls if r['target'] == 'acme.api.f']
    assert len(f_calls) == 6  # source method + module + class + twice in test + nested
    assert {r['caller_kind'] for r in f_calls} == {'module', 'class_body', 'function'}
    assert any(r['caller'].endswith('.nested') for r in f_calls)
    assert any(r['expression'] == 'obj.run()' and r['resolution'] == 'name_only_possible' for r in unknown)
    assert any('test_shadow' in r['caller'] for r in unknown)
    assert not any('test_shadow' in r['caller'] for r in calls)
    assert scan['test_files'] == 1


def test_artifacts_cli_and_output_guards(sample, tmp_path, capsys):
    from specmind_prep.cli import main
    repo, project = sample
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-m', 'fixture'], check=True, capture_output=True)
    config = tmp_path / 'project.json'
    config.write_text(json.dumps(project))
    out = tmp_path / 'output'
    args = ['public-api-callers', '--repo', str(repo), '--project', str(config), '--output', str(out)]
    assert main(args) == 0
    capsys.readouterr()
    assert len(list(out.iterdir())) == 6
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['complete'] and manifest['counts']['functions_methods'] == 2
    assert main(args) == 1
    with pytest.raises(ValueError, match='outside'):
        analyze(repo, project, repo / 'output')


def test_no_target_import_and_missing_docs_fail(sample):
    repo, project = sample
    p = repo / 'src/acme/api.py'
    p.write_text('raise RuntimeError("must not import")\n' + p.read_text())
    assert inventory(repo, project)[0]['api']
    (repo / 'docs/api.rst').unlink()
    with pytest.raises(FileNotFoundError):
        inventory(repo, project)


def test_dynamic_receivers_remain_visible(sample):
    repo, project = sample
    (repo / 'tests/dynamic.py').write_text('from acme import Thing\ndef check(items):\n    Thing().run()\n    items[0].run()\n')
    api, model = inventory(repo, project)
    _, unknown, _ = public_calls(repo, project, api['api'], model)
    expressions = {r['expression'] for r in unknown}
    assert {'Thing().run()', 'items[0].run()'} <= expressions
