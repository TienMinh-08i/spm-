from pathlib import Path
from types import SimpleNamespace

from specmind_eval.evaluate.pytest_runner import run_pytest_postcondition, _case_evidence


def run(repo, tests, source, mutant, **options):
    return run_pytest_postcondition(
        problem={'module':'target','entry_point':'f','_expected_source':source},
        tests=[{'test_path':file,'test_name':name} for file,name in tests],
        postcondition='assert True', mutant_source=mutant, repo_root=repo,
        timeout=20, batch_size=500, max_command_chars=24000, collect_all=True, **options)


def test_collection_file_skipped_and_other_file_can_kill(tmp_path):
    source='def f():\n    return True\n'
    (tmp_path/'target.py').write_text(source)
    (tmp_path/'test_bad.py').write_text('from target import f\nassert f()\n'+''.join(
        f'def test_{i}():\n    assert f()\n' for i in range(20)))
    (tmp_path/'test_good.py').write_text('from target import f\ndef test_good():\n    assert f()\n')
    tests=[('test_bad.py',f'test_{i}') for i in range(20)]+[('test_good.py','test_good')]
    status,detail=run(tmp_path,tests,source,source.replace('True','False'),suite_stop_on_kill=True)
    assert status=='fail' and detail['early_stop']=='confirmed_suite_kill'
    assert detail['process_count']==2 and detail['collection_errors']
    assert _case_evidence(detail)['test_good.py::test_good']['mutation_calls']


def test_collection_only_is_unknown_evidence_not_kill(tmp_path):
    source='def f():\n    return True\n'
    (tmp_path/'target.py').write_text(source)
    (tmp_path/'test_bad.py').write_text('from target import f\nassert f()\ndef test_x():\n    f()\n')
    status,detail=run(tmp_path,[('test_bad.py','test_x')],source,source.replace('True','False'),suite_stop_on_kill=True)
    assert status=='collection_error' and detail['process_count']==1
    assert not any(c['reports'].get('call')=='failed' for c in _case_evidence(detail).values())


def test_suite_stops_after_confirmed_kill_but_normal_mode_collects_all(tmp_path):
    source='def f():\n    return 1\n'
    (tmp_path/'target.py').write_text(source)
    (tmp_path/'test_good.py').write_text('from target import f\ndef test_a():\n    assert f()==1\ndef test_b():\n    assert f()==1\n')
    tests=[('test_good.py','test_a'),('test_good.py','test_b')]
    for suite in (True,False):
        status,detail=run(tmp_path,tests,source,source.replace('1','2'),suite_stop_on_kill=suite)
        cases=_case_evidence(detail)
        assert status=='fail'
        assert sum(c['reports'].get('call')=='failed' for c in cases.values())==(1 if suite else 2)


def test_invalid_selector_retries_are_bounded(tmp_path, monkeypatch):
    calls=[]
    def fake(*a,**kw):
        calls.append(kw['timeout'])
        return SimpleNamespace(stdout='ERROR: not found: test name',returncode=4)
    monkeypatch.setattr('specmind_eval.evaluate.pytest_runner.subprocess.run',fake)
    status,detail=run(tmp_path,[('test_bad.py',f'test_{i}') for i in range(100)],'def f(): pass','def f(): pass',max_processes=3)
    assert status=='budget_exhausted' and len(calls)==3
    assert detail['process_count']==3


def test_total_budget_applies_across_processes(tmp_path, monkeypatch):
    ticks=iter([0,0,5])
    monkeypatch.setattr('specmind_eval.evaluate.pytest_runner.monotonic',lambda:next(ticks))
    monkeypatch.setattr('specmind_eval.evaluate.pytest_runner.subprocess.run',lambda *a,**kw:SimpleNamespace(stdout='ERROR: not found:',returncode=4))
    status,detail=run(tmp_path,[('test_bad.py','a'),('test_bad.py','b')],'def f(): pass','def f(): pass',total_timeout=4)
    assert status=='budget_exhausted' and detail['process_count']==1
