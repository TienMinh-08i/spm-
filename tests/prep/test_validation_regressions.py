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
