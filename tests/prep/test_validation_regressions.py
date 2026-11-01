import json
import pytest
from specmind_eval.inputs.prepared import load_prepared_dataset, load_prepared_mutants
from specmind_prep.scope.scope import load_scope
from specmind_prep.cli import check_raw

@pytest.mark.parametrize("schema,loader,required", [
 ("prepared-dataset-v1", lambda p: load_prepared_dataset(p,p), ["problems.jsonl","tests.jsonl","results.json","excluded_functions.json","summary.json"]),
 ("prepared-mutants-v1", lambda p: load_prepared_mutants(p,p,{}), ["functions.json","summary.json","targets_input.json"]),
 ("mutation-scope-v1", lambda p: load_scope(p,p), ["scope.json","targets.json"]),
 ("raw-marshmallow-v1", check_raw, ["problems.jsonl","tests.jsonl","trace.json","summary.json","command.json"]),
])
def test_missing_hash_entries_rejected_before_loading(tmp_path,schema,loader,required):
 for missing in required:
  manifest={"schema":schema,"complete":True,"files":{n:"unused" for n in required if n!=missing}}
  (tmp_path/"manifest.json").write_text(json.dumps(manifest))
  with pytest.raises(ValueError,match="required artifact hashes"):
   loader(tmp_path)

def test_raw_extraction_rejects_pytest_early_stop(tmp_path):
 from specmind_prep.extract.extract import extract
 repo=tmp_path/"repo"
 pkg=repo/"src/marshmallow"
 pkg.mkdir(parents=True)
 (repo/"tests").mkdir()
 (pkg/"__init__.py").write_text('def f(x):\n    """Return x."""\n    return x\n')
 (repo/"pytest.ini").write_text("[pytest]\naddopts = -x\n")
 (repo/"tests/test_f.py").write_text('from marshmallow import f\ndef test_a():\n    assert f(1)==2\ndef test_b():\n    assert f(2)==2\n')
 output=tmp_path/"raw"
 with pytest.raises(ValueError,match="Trace stopped"):
  extract(repo,output,timeout=30)
 assert not json.loads((output/"manifest.json").read_text())["complete"]
 trace=json.loads((output/"trace.json").read_text())
 assert len(trace["nodeids"])==2 and len(trace["reports"])==1

def test_missing_exclusion_config_does_not_start_extraction(tmp_path,monkeypatch,capsys):
 from specmind_prep.cli import main
 import specmind_prep.extract.extract as module
 monkeypatch.setattr(module,"extract",lambda *a,**kw:pytest.fail("Extraction started"))
 assert main(["extract","--repo",str(tmp_path),"--output",str(tmp_path/"out"),"--exclude-config",str(tmp_path/"missing.json")])==1
 captured=capsys.readouterr()
 assert "Exclusion config file not found" in captured.err+captured.out

@pytest.mark.parametrize("fingerprint,files",[("other",{"a":"hash"}),("dataset",{"b":"hash"})])
def test_validate_rejects_unrelated_dataset_scope(tmp_path,monkeypatch,capsys,fingerprint,files):
 from specmind_prep.cli import main
 import specmind_eval.inputs.prepared as prepared
 import specmind_prep.scope.scope as scope
 monkeypatch.setattr(prepared,"load_prepared_dataset",lambda *a:([],{}, {"fingerprint":"dataset","files":{"a":"hash"}}))
 monkeypatch.setattr(scope,"load_scope",lambda *a:([],[], {"dataset_fingerprint":fingerprint,"dataset_files":files}))
 assert main(["validate","--repo",str(tmp_path),"--dataset",str(tmp_path/"d"),"--scope",str(tmp_path/"s")])==1
 captured=capsys.readouterr()
 assert "does not belong to this dataset" in captured.err+captured.out

def test_doctor_bounds_unresponsive_docker(tmp_path,monkeypatch,capsys):
 import subprocess
 import importlib.metadata
 from specmind_prep.cli import main
 monkeypatch.setattr(importlib.metadata,"version",lambda *a:"test")
 def timeout(command,**kwargs):
  assert kwargs["timeout"]==30
  raise subprocess.TimeoutExpired(command,kwargs["timeout"])
 monkeypatch.setattr(subprocess,"run",timeout)
 assert main(["doctor","--docker"])==1
 captured=capsys.readouterr()
 assert "timed out" in captured.err+captured.out
