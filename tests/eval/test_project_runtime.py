from specmind_eval.evaluate.pytest_runner import pythonpath_for
from specmind_eval.evaluate.evaluator import Evaluator

def test_configured_import_path(tmp_path):
 (tmp_path/'lib').mkdir()
 assert str(tmp_path/'lib') in pythonpath_for(tmp_path,['lib'])

def test_new_project_import_path(tmp_path):
 repo=tmp_path/'repo';(repo/'lib/acme').mkdir(parents=True)
 source='def f(x):\n    return x+1\n'
 path=repo/'lib/acme/__init__.py';path.write_text(source)
 (repo/'test_f.py').write_text('from acme import f\ndef test_f():\n    assert f(1)==2\n')
 problem={'problem_id':'acme.f','module':'acme','entry_point':'f','repo_path':'lib/acme/__init__.py','_expected_source':source,'_expected_source_path':str(path),'_python_path':['lib']}
 ev=Evaluator(problem,[{'nodeid':'test_f.py::test_f','problem_id':'acme.f'}],repo,tmp_path/'evaluation',{'pytest_timeout':30,'batch_size':25})
 ev.ready=True;ev.eligible=['test_f.py::test_f']
 assert ev.correctness('assert return_value==x+1')['status']=='pass'
