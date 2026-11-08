
import pytest

from specmind_prep.project import validate_project
from specmind_prep.public_api import inventory


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
