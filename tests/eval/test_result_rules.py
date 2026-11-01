from specmind_eval.evaluate.candidate import refresh, evaluate
from specmind_eval.evaluate.metrics import mutation_metrics
from specmind_eval.report import report
from specmind_eval.common.storage import write

def attempt(turn,action,score):
 return {"turn":turn,"action":action,"correctness":{"status":"pass"},"mutation":{"metrics":{"feedback":{"score":score,"unknown":0}}}}

def test_best_includes_explore_and_ties_choose_earlier_turn():
 early=attempt(1,"explore",.5)
 submitted=attempt(2,"submit",.5)
 later=attempt(3,"explore",.5)
 state={"attempts":[early,submitted,later]}
 refresh(state,[{}])
 assert state["best"] is early and state["best_submission"] is submitted
 assert state["latest"] is later and early["action"]=="explore"

def test_fail_never_invokes_mutation():
 class Ev:
  def correctness(self,_):return {"status":"fail"}
  def mutation(self,*_):raise AssertionError("mutation invoked")
 correctness,mutation=evaluate("assert False","def f(): return 1","f",Ev(),[{}],{})
 assert correctness["status"]=="fail" and mutation is None

def test_score_and_report_keep_unknown_in_prepared_denominator(tmp_path):
 rows=[{"id":str(i),"suite_killed":None,"assertion_killed":v} for i,v in enumerate([True,False,None])]
 metrics=mutation_metrics(rows)
 assert metrics["assertion"]["score"]==1/3
 assert metrics["feedback"]=={"metric":"assertion_kills_on_prepared_mutants","killed":1,"total":3,"unknown":1,"score":1/3,"not_killed":1,"complete":False}
 write(tmp_path/"functions/f/result.json",{"function":"f","status":"completed","done":True,"assertion":"assert True","correctness":{"status":"pass"},"mutation":{"metrics":metrics,"mutants":rows}})
 summary=report(tmp_path)
 assert summary["assertion"]["micro_score"]==1/3
 for file in ["report.md","attempts.csv","summary.json"]:
  text=(tmp_path/file).read_text(encoding="utf8")
  assert "lower_bound" not in text and "upper_bound" not in text
 assert mutation_metrics([])["assertion"]["score"] is None

def test_policy_feedback_names_the_forbidden_call():
 from specmind_eval.evaluate.policy import validate
 import pytest
 with pytest.raises(ValueError,match=r"Call not allowed: set\(x\).issubset"):
  validate("assert set(x).issubset(set())","def f(x): return x","f","behavioral")

def test_feedback_preserves_first_failure_counterexample():
 from types import SimpleNamespace
 from specmind_eval.evaluate.evaluator import Evaluator
 from specmind_eval.generate.feedback import build_feedback
 ev=SimpleNamespace(ready=True,arm=lambda _: {"status":"fail","cases":{},"detail":{"events":[{"kind":"report","outcome":"failed","detail":"COUNTEREXAMPLE obj=b'abc'"}],"logs":["start\n"+"summary\n"*1000]}})
 correctness=Evaluator.correctness(ev,"assert False")
 assert "COUNTEREXAMPLE" in build_feedback(correctness,None)
 assert "COUNTEREXAMPLE" not in correctness["log_tail"]
