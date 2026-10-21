import argparse
import json
import sys
from pathlib import Path

from .report import report
from .storage import read

DEFAULTS = {
    "dataset": "marshmallow",
    "mode": "multiturn",
    "max_turns": 12,
    "threshold": 0.9,
    "model": "gpt-5.4-mini",
    "temperature": 0,
    "max_tokens": 1024,
    "pytest_timeout": 120,
    "batch_size": 25,
    "api_timeout": 180,
    "api_retries": 2,
    "mutmut_timeout": 1800,
    "path_mappings": {},
    "context_mode": "none",
    "context_budget": 8000,
    "context_depth": 1,
    "generate_without_mutants": False,
    "negative_control": False,
    "assertion_policy": "behavioral",
    "evaluation_retry_limit": 2,
}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "prepare":
        from .preparation.cli import main as prepare_main
        return prepare_main(sys.argv[2:])
    from .runner import run
    parser = argparse.ArgumentParser(description="Independent SpecMind dataset evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", help="Offline extraction and dataset preparation; use prepare --help")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--resume", action="store_true")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--output", type=Path, required=True)
    experiment_parser = sub.add_parser("experiment", help="Run A/B/C/D with one shared configuration")
    experiment_parser.add_argument("--config", type=Path, required=True)
    experiment_parser.add_argument("--output", type=Path, required=True)
    experiment_parser.add_argument("--resume", action="store_true")
    compare_parser = sub.add_parser("compare", help="Rebuild A/B/C/D comparison without model calls")
    compare_parser.add_argument("--output", type=Path, required=True)
    compare_parser.add_argument('--paired', type=Path, help='Separate joined A/B/C/D reports')
    assets_parser = sub.add_parser('prepare-context', help='Verify graph and ZIP runtime usages against checkout')
    for flag in ('graph', 'usage-zip', 'repo-root', 'output'):
        assets_parser.add_argument('--' + flag, type=Path, required=True)
    suite_parser = sub.add_parser("evaluate-suite", help="Evaluate original tests on saved mutants, without LLM calls")
    suite_parser.add_argument("--run", type=Path, required=True, help="Existing single-arm run directory")
    suite_parser.add_argument("--output", type=Path, required=True, help="Separate suite evaluation directory")
    join_parser = sub.add_parser('join-suite', help='Pair saved suite and assertion evidence; no execution')
    for flag in ('run', 'suite', 'output'):
        join_parser.add_argument('--' + flag, type=Path, required=True)
    prepare_parser = sub.add_parser('prepare-tests', help='Collect corrected nodeids and write a NEW test dataset')
    for flag in ('tests', 'repo-root', 'output'):
        prepare_parser.add_argument('--' + flag, type=Path, required=True)
    dataset_parser = sub.add_parser('prepare-dataset', help='Offline True/False filtering; no LLM or mutants')
    for flag in ('problems', 'tests', 'repo-root', 'output'):
        dataset_parser.add_argument('--' + flag, type=Path, required=True)
    dataset_parser.add_argument('--resume', action='store_true')
    dataset_parser.add_argument('--force', action='store_true')
    dataset_parser.add_argument('--tasks', nargs='+', help='Exact function IDs for a pilot/debug subset')
    dataset_parser.add_argument('--pytest-timeout', type=int, default=120)
    dataset_parser.add_argument('--batch-size', type=int, default=500)
    validate_parser = sub.add_parser('validate-dataset', help='Check prepared artifact without running tests')
    validate_parser.add_argument('--dataset', type=Path, required=True)
    validate_parser.add_argument('--repo-root', type=Path, required=True)
    scope_parser = sub.add_parser('prepare-mutation-scope', help='Offline short-function scope and callee trace')
    for flag in ('dataset','repo-root','output'):
        scope_parser.add_argument('--'+flag,type=Path,required=True)
    scope_parser.add_argument('--resume',action='store_true')
    mutant_parser = sub.add_parser('prepare-mutants', help='Docker offline mutant corpus, no LLM')
    for flag in ('scope','repo-root','output'):
        mutant_parser.add_argument('--'+flag,type=Path,required=True)
    mutant_parser.add_argument('--resume',action='store_true')
    mutant_parser.add_argument('--image',default='specmind-paper-eval-mutmut-py312:latest')
    mutant_parser.add_argument('--mutmut-timeout',type=int,default=1800)
    mutant_validate = sub.add_parser("validate-mutants", help="Read-only verification of prepared corpus and dataset")
    for flag in ("mutants", "dataset", "repo-root"):
        mutant_validate.add_argument("--"+flag, type=Path, required=True)
    debug_parser = sub.add_parser('debug', help='Replay saved evidence without calling an LLM')
    debug_sub = debug_parser.add_subparsers(dest='debug_action', required=True)
    node_parser = debug_sub.add_parser('nodeid', help='Convert IDs only; no pytest')
    node_parser.add_argument('--tests', type=Path, required=True)
    node_parser.add_argument('--output', type=Path, required=True)
    for name in ('candidate', 'attempt', 'mutant', 'metrics'):
        p = debug_sub.add_parser(name)
        p.add_argument('--run', type=Path, required=True)
        p.add_argument('--function', required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--attempt', type=int)
        p.add_argument('--assertion-file', type=Path)
        p.add_argument('--mutant')
        p.add_argument('--nodeid')
        p.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare-context':
        from .context_assets import prepare_context_assets
        result = prepare_context_assets(args.graph, args.usage_zip, args.repo_root, args.output)
    elif args.command == 'debug':
        from .debug import nodeids, replay
        if args.debug_action == 'nodeid':
            result = nodeids(args.tests, args.output)
        else:
            result = replay(args.run, args.function, args.output, action=args.debug_action,
                            attempt=args.attempt, assertion_file=args.assertion_file,
                            mutant=args.mutant, nodeid=args.nodeid, force=args.force)
    elif args.command == 'prepare-mutation-scope':
        from .mutation_scope import prepare_scope
        result=prepare_scope(args.dataset,args.repo_root,args.output,resume=args.resume)
    elif args.command == 'prepare-mutants':
        from .prepare_mutants import prepare_mutants
        result=prepare_mutants(args.scope,args.repo_root,args.output,resume=args.resume,
                               image=args.image,timeout=args.mutmut_timeout)
        if result.get('pending_targets'):
            print(json.dumps(result,indent=2))
            sys.exit(2)
    elif args.command == 'validate-mutants':
        from .prepare_dataset import load_prepared_dataset
        from .prepare_mutants import load_prepared_mutants
        problems, tests, dm = load_prepared_dataset(args.dataset, args.repo_root)
        rows, mm = load_prepared_mutants(args.mutants, args.repo_root, dm)
        wanted = {p['problem_id'] for p in problems}
        if set(rows) != wanted or any(not r['done'] for r in rows.values()):
            raise ValueError('Prepared corpus function mapping mismatch')
        result = {'valid': True, 'functions': len(rows), 'function_mutant_pairs': sum(len(r['mutants']) for r in rows.values()), 'fingerprint': mm['fingerprint']}
    elif args.command == 'prepare-dataset':
        from .prepare_dataset import prepare_dataset
        result = prepare_dataset(args.problems, args.tests, args.repo_root, args.output,
                                 resume=args.resume, tasks=args.tasks, timeout=args.pytest_timeout,
                                 batch_size=args.batch_size, force=args.force)
        if result['pending_functions']:
            print(json.dumps(result, indent=2))
            sys.exit(2)
    elif args.command == 'validate-dataset':
        from .prepare_dataset import load_prepared_dataset
        problems, tests, manifest = load_prepared_dataset(args.dataset, args.repo_root)
        result = {'valid': True, 'functions': len(problems), 'test_links': sum(map(len, tests.values())),
                  'fingerprint': manifest['fingerprint']}
    elif args.command == 'prepare-tests':
        from .prepare_tests import prepare_tests
        result = prepare_tests(args.tests, args.repo_root, args.output)
    elif args.command == 'join-suite':
        from .join import join_suite
        result = join_suite(args.run, args.suite, args.output)
    elif args.command == "evaluate-suite":
        from .suite_evaluation import evaluate_suite

        result = evaluate_suite(args.run, args.output)
    elif args.command == "compare":
        from .experiment import compare

        result = compare(args.output, paired=args.paired)
    elif args.command == "report":
        if not (args.output / "manifest.json").exists():
            parser.error("No run manifest found")
        result = report(args.output)
    else:
        supplied = read(args.config)
        if supplied is None:
            parser.error("Configuration file not found")
        allowed = set(DEFAULTS) | {
            "prepared_dataset",
            "prepared_mutants",
            "problems",
            "tests",
            "repo_root",
            "reuse_mutmut_rows",
            "tasks",
            "mock_assertion",
            "project",
            "call_graph_dir",
            "expected_repo_commit",
        }
        unknown = set(supplied) - allowed
        if unknown:
            parser.error(f"Unknown config keys: {sorted(unknown)}")
        config = {**DEFAULTS, **supplied}
        required = ("repo_root", "prepared_dataset", "prepared_mutants") if config.get("prepared_dataset") or config.get("prepared_mutants") else ("problems", "tests", "repo_root")
        for key in required:
            if not config.get(key):
                parser.error(f"Missing config key: {key}")
        for key in ("problems", "tests", "repo_root", "reuse_mutmut_rows", "call_graph_dir", "prepared_dataset", "prepared_mutants"):
            if config.get(key):
                path = Path(config[key])
                config[key] = str(
                    (args.config.parent / path).resolve() if not path.is_absolute() else path.resolve()
                )
        if config["mode"] not in {"multiturn", "retry"}:
            parser.error("mode must be multiturn or retry")
        if config['assertion_policy'] not in {'legacy', 'behavioral'}:
            parser.error('assertion_policy must be legacy or behavioral')
        if config["context_mode"] not in {"none", "callee", "caller", "callgraph"}:
            parser.error("Invalid context_mode")
        if config["context_budget"] < 1 or config["context_depth"] < 1:
            parser.error("Context budget and depth must be positive")
        for key in ("generate_without_mutants", "negative_control"):
            if not isinstance(config[key], bool):
                parser.error(f"{key} must be boolean")
        if config["max_turns"] < 1 or config["batch_size"] < 1 or not 0 <= config["threshold"] <= 1:
            parser.error("Invalid turn count, batch size, or threshold")
        for key in ("pytest_timeout", "api_timeout", "mutmut_timeout", "api_retries", "evaluation_retry_limit"):
            if config[key] < 0:
                parser.error(f"{key} must be nonnegative (0 disables timeouts)")
        if args.command == "experiment":
            from .experiment import experiment

            result = experiment(config, args.output, args.resume)
        else:
            result = run(config, args.output, args.resume)
    print(json.dumps(result, indent=2))
    if args.command in {"run", "experiment", "compare", "evaluate-suite"} and result.get("unfinished"):
        sys.exit(2)


if __name__ == "__main__":
    sys.exit(main())
