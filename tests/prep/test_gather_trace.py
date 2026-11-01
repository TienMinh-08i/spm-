import json
import subprocess
from pathlib import Path
import pytest
from specmind_prep.gather.gather import gather,validate_candidates
from specmind_prep.trace.trace import trace,validate_links
from specmind_prep.filter.dataset import prepare_linked_dataset
from specmind_eval.common.storage import read
from specmind_eval.common.fingerprint import implementation_files

@pytest.fixture(params=['src','.'])
def fixture_project(tmp_path,request):
 repo=tmp_path/'Check_Out'
 source=repo/request.param
 (source/'acme').mkdir(parents=True)
 (repo/'testing').mkdir()
 (repo/'docs').mkdir()
 (repo/'examples').mkdir()
 (repo/'docs/index.rst').write_text('.. autofunction:: acme.f\n')
 (repo/'examples/demo.py').write_text('def example():\n    """Example."""\n    pass\n')
 (repo/'setup.py').write_text('def setup():\n    """Setup."""\n    pass\n')
 (source/'acme/__init__.py').write_text('def deco(fn):\n    return fn\n@deco\n@deco\ndef f(x):\n    """Increment."""\n    return x+1\ndef g():\n    """Unused."""\n    return 0\n')
 (repo/'testing/test_f.py').write_text('from acme import f\ndef test_f():\n    assert f(1)==2\n')
 project={'name':'demo','source_root':request.param,'packages':['acme'],'tests':['testing'],'docs_dir':'docs'}
 return repo,project,tmp_path/'candidates'

def test_gather_is_source_only_and_seals_decorator_location(fixture_project,monkeypatch):
 repo,project,candidates=fixture_project
 original=subprocess.run
 def run(command,**kwargs):
  assert 'pytest' not in command
  return original(command,**kwargs)
 monkeypatch.setattr(subprocess,'run',run)
 result=gather(repo,candidates,project=project,documented_only=True,tasks=['acme.f'])
 assert result['candidate_functions']==1
 problem=json.loads((candidates/'problems.jsonl').read_text().splitlines()[0])
 assert problem['code_line']==3 and problem['line']==5
 assert set(implementation_files('specmind_prep.gather.gather')).isdisjoint({'specmind_eval/generate/loop.py','specmind_eval/generate/prompts/multiturn.txt'})
 assert not any('/tests/' in p or p.startswith('tests/') for p in implementation_files('specmind_prep.gather.gather'))

def test_trace_failure_preserves_candidates_and_lineage(fixture_project,tmp_path):
 repo,project,candidates=fixture_project
 gather(repo,candidates,project=project,tasks=['acme.f'])
 before=(candidates/'manifest.json').read_bytes()
 with pytest.raises(ValueError,match='timed out'):
  trace(candidates,repo,tmp_path/'short',timeout=.001)
 assert (candidates/'manifest.json').read_bytes()==before
 assert validate_candidates(candidates,repo)['complete']
 links=tmp_path/'links'
 result=trace(candidates,repo,links,timeout=30)
 assert result['test_links']==1
 manifest=validate_links(links,repo,candidates)
 assert 'trace_config.json' in manifest['files']
 gather(repo,candidates,project=project,tasks=['acme.f'],resume=True)
 assert (candidates/'manifest.json').read_bytes()==before
 dataset=tmp_path/'prepared'
 assert prepare_linked_dataset(candidates,links,repo,dataset,timeout=30)['ready_functions']==1
 assert read(dataset/'manifest.json')['project']['name']=='demo'
 other=tmp_path/'other'
 gather(repo,other,project=project,tasks=['acme.g'])
 with pytest.raises(ValueError,match='do not belong'):
  prepare_linked_dataset(other,links,repo,tmp_path/'wrong')
 with (links/'trace_config.json').open('a') as stream:stream.write(' ')
 with pytest.raises(ValueError,match='artifact mismatch'):
  validate_links(links,repo,candidates)

def test_project_config_drives_five_stage_cli(fixture_project,tmp_path):
 from specmind_prep.cli import main
 repo,project,_=fixture_project
 project_file=tmp_path/'project.json'
 project_file.write_text(json.dumps(project))
 config=tmp_path/'prepare.json'
 out=tmp_path/'pipeline'
 config.write_text(json.dumps({'project':'project.json','repo':str(repo),'output':str(out),'trace_timeout':30,'pytest_timeout':30}))
 assert main(['all','--config',str(config)])==0
 assert (out/'candidates/manifest.json').is_file() and (out/'links/manifest.json').is_file()
 assert not (out/'raw').exists()
 assert main(['validate','--repo',str(repo),'--candidates',str(out/'candidates'),'--links',str(out/'links'),'--dataset',str(out/'prepared')])==0
 assert main(['all','--config',str(config),'--resume'])==0
