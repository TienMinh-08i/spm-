import pytest
from specmind_prep.project import validate_project
from specmind_prep.source_index import source_index

@pytest.fixture
def repo(tmp_path):
 (tmp_path/"src/acme").mkdir(parents=True)
 (tmp_path/"tests").mkdir()
 return tmp_path

@pytest.mark.parametrize("change,key",[({"unexpected":1},"unknown"),({"packages":[]},"packages"),({"packages":["missing"]},"packages"),({"source_root":"../outside"},"source_root"),({"python_path":["missing"]},"python_path")])
def test_invalid_project(repo,change,key):
 with pytest.raises(ValueError,match=key):validate_project({"name":"demo","packages":["acme"],**change},repo)

def test_documented_only_requires_docs(repo):
 with pytest.raises(ValueError,match="docs_dir"):validate_project({"name":"demo","packages":["acme"]},repo,documented_only=True)

def test_flat_layout_scans_only_configured_package(tmp_path):
 for folder in ["acme","docs","examples","testing"]:
  (tmp_path/folder).mkdir()
  (tmp_path/folder/"demo.py").write_text('def f():\n    """Doc."""\n    return 1\n')
 (tmp_path/"setup.py").write_text('def setup():\n    """Doc."""\n    pass\n')
 project=validate_project({"name":"demo","source_root":".","packages":["acme"],"tests":["testing"]},tmp_path)
 index,_=source_index(tmp_path,project)
 assert set(index)=={"acme.demo.f"}
