"""Execute neutral/candidate arms and retain evidence independently of metrics."""

from __future__ import annotations

from time import perf_counter
from pathlib import Path
import ast

from .execution import _case_evidence, run_pytest_postcondition
from .metrics import mutation_metrics
from .storage import digest, read, write
from .provenance import evaluation_identity


class Evaluator:
    def __init__(self, problem, tests, repo_root, output, config):
        self.problem, self.tests, self.repo_root = problem, tests, repo_root
        self.output, self.config = output, config
        self.baseline = None
        self.cache_identity = config.get('_evaluation_identity') or evaluation_identity(repo_root, config)
        self.force = config.get('_force', False)

    def arm_key(self, assertion, mutant=None):
        try:
            normalized = ast.dump(ast.parse(assertion), include_attributes=False)
        except SyntaxError:
            normalized = assertion
        return digest({"assertion_ast": normalized, "mutant": mutant,
                      "suite_stop_on_kill": self.config.get('_suite_stop_on_kill', False),
                      "problem": self.problem,
                      "tests": self.tests, "identity": self.cache_identity,
                      "plugin": Path(__file__).with_name("pytest_plugin.py").read_text(encoding="utf-8")})

    def arm(self, assertion, mutant=None):
        key = self.arm_key(assertion, mutant)
        path = self.output / "arms" / f"{key}.json"
        cached = read(path)
        if not cached and not self.force:
            for directory in self.config.get('_read_cache', []):
                cached = read(Path(directory) / f'{key}.json')
                if cached:
                    break
        suite_proof = cached and self.config.get('_suite_stop_on_kill', False) and any(
            c.get('mutation_calls') and c['reports'].get('call') == 'failed'
            for n, c in cached['cases'].items() if n in getattr(self, 'eligible', []))
        if (not self.force and cached and (cached["status"] in {"pass", "fail"} or suite_proof)
                and not any(c['runtime_errors'] for c in cached['cases'].values())):
            if not path.exists():
                write(path, cached)
            return cached
        started = perf_counter()
        mutation_problem = mutant.get('mutation_problem') if isinstance(mutant, dict) else None
        mutant_source = mutant['function_source'] if isinstance(mutant, dict) else mutant
        status, detail = run_pytest_postcondition(
            problem=self.problem,
            tests=self.tests,
            postcondition=assertion,
            mutant_source=mutant_source,
            mutation_problem=mutation_problem,
            repo_root=self.repo_root,
            timeout=self.config["pytest_timeout"] or None,
            batch_size=self.config["batch_size"],
            max_command_chars=24000,
            collect_all=True,
            suite_stop_on_kill=self.config.get('_suite_stop_on_kill', False),
        )
        result = {
            "status": status,
            "seconds": perf_counter() - started,
            "cases": _case_evidence(detail),
            "detail": detail,
            "assertion": assertion,
            "mutant_source": mutant_source,
            'mutation_problem': mutation_problem,
            "evaluation_identity": self.cache_identity,
        }
        write(path, result)
        return result

    def preflight(self):
        self.baseline = self.arm("assert True")
        cases = self.baseline["cases"]
        covered = [n for n, c in cases.items() if c["passed"] and c["checks"]]
        self.eligible = covered
        self.ready = self.baseline['status'] == 'pass' and bool(covered)
        result = {
            "status": "pass" if self.ready else "unavailable",
            "requested": len(self.tests),
            "collected_cases": len(
                {
                    n
                    for e in self.baseline["detail"].get("events", [])
                    if e.get("kind") == "collection"
                    for n in e["nodeids"]
                }
            ),
            "executed_cases": sum("call" in c["reports"] for c in cases.values()),
            "assertion_covered_cases": len(covered),
            "assertion_executions": sum(c["checks"] for c in cases.values()),
            "eligible_nodeids": self.eligible,
            "reason": None
            if self.ready
            else "Baseline failed, source mismatch, or no assertion-covered tests",
        }
        errors = [e["message"] for e in self.baseline["detail"].get("events", [])
                  if e.get("kind") == "hook_install_error"]
        if not self.ready and errors:
            result["reason"] = "; ".join(dict.fromkeys(errors))
        if self.ready and self.config.get('negative_control', False):
            negative = self.correctness('assert False')
            result['negative_control'] = negative
            if negative['status'] != 'fail' or not negative.get('assertion_violations'):
                self.ready = False
                result.update(status='unavailable', reason='Negative control did not prove assertion failure')
        write(self.output / "preflight.json", result)
        return result

    def correctness(self, assertion):
        if not self.ready:
            return {"status": "unavailable", "reason": "Preflight failed"}
        arm = self.arm(assertion)
        cases = arm["cases"]
        violations = sum(c["violations"] for c in cases.values())
        runtime = sum(c["runtime_errors"] for c in cases.values())
        checks = sum(c["checks"] for c in cases.values())
        covered = sum(c["checks"] > 0 for c in cases.values())
        status = "fail" if violations or runtime else "unavailable"
        if arm["status"] == "pass" and checks and not violations and not runtime:
            status = "pass"
        elif arm["status"] == "fail":
            status = "fail"
        return {
            "status": status,
            "assertion_violations": violations,
            "postcondition_runtime_errors": runtime,
            "covered_cases": covered,
            "checks": checks,
            "log_tail": "\n".join(arm["detail"].get("logs", []))[-4000:] if status != "pass" else "",
        }

    def mutation(self, assertion, mutants):
        rows = []
        for mutant in mutants:
            payload = mutant if mutant.get('mutation_problem') else mutant['function_source']
            candidate = self.arm(assertion, payload)
            ccases = [candidate["cases"].get(n) for n in self.eligible]

            def complete(arm, cases):
                return arm["status"] in {"pass", "fail"} and all(
                    c
                    and all(p in c["reports"] for p in ("setup", "call", "teardown"))
                    and all(c["reports"][p] == "passed" for p in ("setup", "teardown"))
                    for c in cases
                )

            assertion_kill = any(c and c["violations"] for c in ccases)
            covered = any(c and c["checks"] for c in ccases)
            reached = any(c and c.get('mutation_calls',0) for c in ccases) if mutant.get('mutation_problem') else True
            runtime = any(c and c["runtime_errors"] for c in ccases)
            assertion_result = (
                True
                if assertion_kill and reached
                else False
                if complete(candidate, ccases) and covered and not runtime and reached
                else None
            )
            rows.append(
                {
                    "id": mutant["id"],
                    "suite_killed": None,
                    "assertion_killed": assertion_result,
                    "covered": bool(covered),
                    "postcondition_runtime_error": bool(runtime),
                    "target_error": None,
                    "neutral_status": "not_run",
                    "candidate_status": candidate["status"],
                    "diff": mutant.get("diff", ""),
                    "neutral_arm": None,
                    "candidate_arm": self.arm_key(assertion, payload),
                    'mutation_target': (mutant.get('mutation_problem') or {}).get('problem_id',self.problem.get('problem_id')),
                    'mutation_reached': reached,
                }
            )
        return {"metrics": mutation_metrics(rows), "mutants": rows}
