"""One public entry point; individual offline stages remain independently runnable."""

import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path


def path(value):
    return Path(value).expanduser().resolve()


def check_raw(directory):
    from specmind_eval.common.storage import read
    from specmind_eval.common.fingerprint import file_hash

    directory = path(directory)
    m = read(directory / "manifest.json")
    if not m or m.get("schema") not in {"raw-v2", "raw-marshmallow-v1"} or not m.get("complete"):
        raise ValueError("Raw dataset missing or incomplete")
    required = {'command.json', 'problems.jsonl', 'tests.jsonl', 'summary.json', 'trace.json'}
    if not isinstance(m.get("files"), dict) or not required.issubset(m["files"]):
        raise ValueError("Manifest missing required artifact hashes")
    for name, h in m["files"].items():
        if Path(name).name != name or file_hash(directory / name) != h:
            raise ValueError("Raw artifact mismatch: " + name)
    return m


def build_parser():
    parser = argparse.ArgumentParser(description="Project preparation stages (no LLM)")
    parser.add_argument("--traceback", action="store_true", help="Show full Python traceback for debugging")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Check Python, package dependencies, checkout and optional Docker")
    doctor.add_argument("--repo", type=path)
    doctor.add_argument("--docker", action="store_true")
    doctor.add_argument("--project", type=path)
    extract = sub.add_parser("extract", help="Collect docstring functions and exact call-phase pytest links")
    extract.add_argument("--repo", type=path, required=True)
    extract.add_argument("--output", type=path, required=True)
    extract.add_argument("--task", action="append", help="Exact function ID; repeat to select several")
    extract.add_argument(
        "--test", action="append", help="Test path or exact nodeid; repeat to select several"
    )
    extract.add_argument(
        "--exclude-config", type=path, help="JSON list of {pattern, reason}; no hidden exclusions"
    )
    extract.add_argument("--documented-only", action="store_true")
    extract.add_argument("--project", type=path)
    extract.add_argument("--expected-commit")
    extract.add_argument("--timeout", type=int, default=1800)
    extract.add_argument("--resume", action="store_true")
    gather = sub.add_parser('gather', help='Read source candidates without running pytest')
    for name in ('repo','project','output'):
        gather.add_argument('--'+name,type=path,required=True)
    gather.add_argument('--task',action='append')
    gather.add_argument('--documented-only',action='store_true')
    gather.add_argument('--resume',action='store_true')
    trace = sub.add_parser('trace', help='Observe tests against sealed candidates')
    for name in ('repo','candidates','output'):
        trace.add_argument('--'+name,type=path,required=True)
    trace.add_argument('--test',action='append')
    trace.add_argument('--exclude-config',type=path)
    trace.add_argument('--timeout',type=int,default=1800)
    trace.add_argument('--resume',action='store_true')
    prep = sub.add_parser(
        "dataset", aliases=["prepare"], help="Offline assert True then assert False filtering"
    )
    inputs = prep.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--raw", type=path, help="Combined-artifact compatibility input")
    inputs.add_argument("--candidates", type=path)
    prep.add_argument("--links", type=path)
    prep.add_argument("--repo", type=path, required=True)
    prep.add_argument("--output", type=path, required=True)
    prep.add_argument("--task", action="append")
    prep.add_argument("--timeout", type=int, default=120)
    prep.add_argument("--batch-size", type=int, default=500)
    prep.add_argument("--resume", action="store_true")
    prep.add_argument("--force", action="store_true")
    scope = sub.add_parser(
        "scope", help="Choose self and one-level callees for functions with <5 AST body lines"
    )
    scope.add_argument("--dataset", type=path, required=True)
    scope.add_argument("--repo", type=path, required=True)
    scope.add_argument("--output", type=path, required=True)
    scope.add_argument("--timeout", type=int, default=300)
    scope.add_argument("--resume", action="store_true")
    mutants = sub.add_parser("mutants", help="Export offline mutmut corpus using Docker")
    mutants.add_argument("--scope", type=path, required=True)
    mutants.add_argument("--repo", type=path, required=True)
    mutants.add_argument("--output", type=path, required=True)
    mutants.add_argument("--image")
    mutants.add_argument("--timeout", type=int, default=1800)
    mutants.add_argument("--resume", action="store_true")
    validate = sub.add_parser(
        "validate", help="Read-only artifact/environment checks; no pytest or Docker run"
    )
    validate.add_argument("--repo", type=path, required=True)
    validate.add_argument("--raw", type=path)
    validate.add_argument("--candidates", type=path)
    validate.add_argument("--links", type=path)
    validate.add_argument("--dataset", type=path)
    validate.add_argument("--scope", type=path)
    validate.add_argument("--mutants", type=path)
    inspect = sub.add_parser(
        "inspect", help="Read summaries, excluded and pending functions without execution"
    )
    inspect.add_argument("--output", type=path, required=True)
    allp = sub.add_parser("all", help="Run staged preparation; default stops after dataset, before Docker")
    allp.add_argument("--config", type=path, required=True)
    allp.add_argument("--resume", action="store_true")
    allp.add_argument("--through", choices=["dataset", "scope", "mutants"], default="dataset")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    # Used by child pytest processes even when launched via run.py without pip install.
    src = str(Path(__file__).resolve().parents[1])
    os.environ["PYTHONPATH"] = os.pathsep.join([src, os.environ.get("PYTHONPATH", "")])
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        from specmind_eval.common.storage import read

        if args.command == "doctor":
            import importlib.metadata

            from specmind_prep.project import load_project, infer_project
            project = None
            if args.project:
                if not args.repo:raise ValueError('--project requires --repo')
                project = load_project(args.project,args.repo)
            elif args.repo:
                project = infer_project(args.repo)
            dependencies = ['pytest'] + (project['dependencies'] if project else [])
            from packaging.requirements import Requirement
            installed = {}
            missing = []
            for requirement in dependencies:
                spec = Requirement(requirement)
                try:
                    version = importlib.metadata.version(spec.name)
                    installed[spec.name] = version
                    if spec.specifier and version not in spec.specifier:missing.append(requirement)
                except importlib.metadata.PackageNotFoundError:
                    missing.append(requirement)
            result = {'python':sys.version,'executable':sys.executable,'dependencies':installed,
                      'missing_dependencies':missing,'install_command':[sys.executable,'-m','pip','install',*missing] if missing else None}
            if project:result['project']=project
            if args.docker:
                result["docker"] = subprocess.run(
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                ).stdout.strip()
        elif args.command == 'gather':
            from specmind_prep.project import load_project
            from specmind_prep.gather.gather import gather
            project=load_project(args.project,args.repo,documented_only=args.documented_only)
            result=gather(args.repo,args.output,project=project,tasks=args.task,documented_only=args.documented_only,resume=args.resume)
        elif args.command == 'trace':
            from specmind_prep.trace.trace import trace
            excluded=read(args.exclude_config) if args.exclude_config else []
            if args.exclude_config and not args.exclude_config.is_file():raise ValueError('Exclusion config file not found: '+str(args.exclude_config))
            result=trace(args.candidates,args.repo,args.output,tests=args.test,exclude=excluded,timeout=args.timeout,resume=args.resume)
        elif args.command == "extract":
            from specmind_prep.extract.extract import extract

            if args.exclude_config and not args.exclude_config.is_file():
                raise ValueError("Exclusion config file not found: " + str(args.exclude_config))
            excluded = read(args.exclude_config) if args.exclude_config else []
            if not isinstance(excluded, list):
                raise ValueError("Exclusion config must contain a JSON list")
            from specmind_prep.project import load_project
            project = load_project(args.project,args.repo,documented_only=args.documented_only) if args.project else None
            result = extract(
                args.repo,
                args.output,
                tasks=args.task,
                tests=args.test,
                exclude=excluded,
                documented_only=args.documented_only,
                expected_commit=args.expected_commit,
                project=project,
                timeout=args.timeout,
                resume=args.resume,
            )
        elif args.command in {"dataset", "prepare"}:
            from specmind_prep.filter.dataset import prepare_dataset
            from specmind_eval.common.fingerprint import source_environment

            if args.candidates:
                if not args.links:raise ValueError('--candidates requires --links')
                from specmind_prep.filter.dataset import prepare_linked_dataset
                result=prepare_linked_dataset(args.candidates,args.links,args.repo,args.output,resume=args.resume,
                                              tasks=args.task,timeout=args.timeout,batch_size=args.batch_size,force=args.force)
            else:
                m = check_raw(args.raw)
                if path(m["repo"]) != args.repo:
                    raise ValueError("Raw artifact belongs to a different checkout path")
                if m["source_environment"] != source_environment(args.repo):
                    raise ValueError("Raw source/environment mismatch; extract into a new output")
                result = prepare_dataset(
                    args.raw / "problems.jsonl",
                    args.raw / "tests.jsonl",
                    args.repo,
                    args.output,
                    tasks=args.task,
                    timeout=args.timeout,
                    batch_size=args.batch_size,
                    resume=args.resume,
                    force=args.force,
                )
        elif args.command == "scope":
            from specmind_prep.scope.scope import prepare_scope

            result = prepare_scope(
                args.dataset, args.repo, args.output, timeout=args.timeout, resume=args.resume
            )
        elif args.command == "mutants":
            from specmind_prep.mutants.corpus import prepare_mutants

            result = prepare_mutants(
                args.scope, args.repo, args.output, timeout=args.timeout, image=args.image, resume=args.resume
            )
        elif args.command == "validate":
            from specmind_eval.inputs.prepared import load_prepared_dataset
            from specmind_eval.common.fingerprint import source_environment

            result = {}
            manifest = None
            if args.candidates:
                from specmind_prep.gather.gather import validate_candidates
                validate_candidates(args.candidates,args.repo)
                result['candidates']='valid'
            if args.links:
                if not args.candidates:raise ValueError('--links requires --candidates')
                from specmind_prep.trace.trace import validate_links
                validate_links(args.links,args.repo,args.candidates)
                result['links']='valid'
            if args.raw:
                m = check_raw(args.raw)
                if path(m["repo"]) != args.repo or m["source_environment"] != source_environment(args.repo):
                    raise ValueError("Raw source/environment mismatch")
                result["raw"] = "valid"
            if args.dataset:
                problems, tests, manifest = load_prepared_dataset(args.dataset, args.repo)
                result["dataset"] = {"functions": len(problems), "test_links": sum(map(len, tests.values()))}
            if args.scope:
                from specmind_prep.scope.scope import load_scope

                rows, targets, m = load_scope(args.scope, args.repo)
                if manifest and (m.get("dataset_fingerprint") != manifest["fingerprint"]
                                 or m.get("dataset_files") != manifest["files"]):
                    raise ValueError("Mutation scope does not belong to this dataset")
                result["scope"] = {"functions": len(rows), "targets": len(targets)}
            if args.mutants:
                from specmind_eval.inputs.prepared import load_prepared_mutants

                if not manifest:
                    raise ValueError("--mutants requires --dataset")
                rows, m = load_prepared_mutants(args.mutants, args.repo, manifest)
                result["mutants"] = {"functions": len(rows)}
            if not result:
                raise ValueError("Select --raw, --dataset, --scope or --mutants")
        elif args.command == "inspect":
            result = {
                name: read(args.output / name)
                for name in ["summary.json", "progress.json", "excluded_functions.json"]
                if (args.output / name).exists()
            }
            if not result:
                raise ValueError("No summary/progress at output directory")
        else:
            from specmind_prep.extract.extract import extract
            from specmind_prep.filter.dataset import prepare_dataset
            from specmind_eval.common.storage import run_lock, write

            cfg = read(args.config)
            base = args.config.parent
            allowed = {
                "repo",
                "output",
                "tasks",
                "tests",
                "exclude",
                "documented_only",
                "expected_commit",
                "project",
                "trace_timeout",
                "pytest_timeout",
                "scope_timeout",
                "batch_size",
                "image",
                "mutmut_timeout",
            }
            if not isinstance(cfg, dict) or set(cfg) - allowed:
                raise ValueError("Unknown preparation config keys or config is not an object")
            if not {"repo", "output"} <= cfg.keys():
                raise ValueError("Preparation config requires repo and output")

            def resolve(name):
                return path(base / cfg[name])

            repo = resolve("repo")
            out = resolve("output")
            from specmind_eval.common.outputs import separate_output

            separate_output(out, repo)
            with run_lock(out):
                result = {}
                if cfg.get('project'):
                    from specmind_prep.project import load_project
                    from specmind_prep.gather.gather import gather
                    from specmind_prep.trace.trace import trace
                    from specmind_prep.filter.dataset import prepare_linked_dataset
                    project=load_project(base/cfg['project'],repo,documented_only=cfg.get('documented_only',False))
                    result['gather']=gather(repo,out/'candidates',project=project,tasks=cfg.get('tasks'),
                                            documented_only=cfg.get('documented_only',False),resume=args.resume)
                    result['trace']=trace(out/'candidates',repo,out/'links',tests=cfg.get('tests'),exclude=cfg.get('exclude',[]),
                                          timeout=cfg.get('trace_timeout',1800),resume=args.resume)
                    result['dataset']=prepare_linked_dataset(out/'candidates',out/'links',repo,out/'prepared',
                                            resume=args.resume,timeout=cfg.get('pytest_timeout',120),batch_size=cfg.get('batch_size',500))
                else:
                    result["raw"] = extract(
                        repo,
                        out / "raw",
                        tasks=cfg.get("tasks"),
                        tests=cfg.get("tests"),
                        exclude=cfg.get("exclude", []),
                        documented_only=cfg.get("documented_only", False),
                        expected_commit=cfg.get("expected_commit"),
                        timeout=cfg.get("trace_timeout", 1800),
                        resume=args.resume,
                    )
                    result["dataset"] = prepare_dataset(
                        out / "raw/problems.jsonl",
                        out / "raw/tests.jsonl",
                        repo,
                        out / "prepared",
                        resume=args.resume,
                        timeout=cfg.get("pytest_timeout", 120),
                        batch_size=cfg.get("batch_size", 500),
                    )
                if result["dataset"]["pending_functions"] or not result["dataset"]["ready_functions"]:
                    raise ValueError(
                        "Dataset incomplete or empty; inspect prepared/progress.json before continuing"
                    )
                if args.through in ["scope", "mutants"]:
                    from specmind_prep.scope.scope import prepare_scope

                    result["scope"] = prepare_scope(
                        out / "prepared",
                        repo,
                        out / "scope",
                        resume=args.resume,
                        timeout=cfg.get("scope_timeout", 300),
                    )
                if args.through == "mutants":
                    from specmind_prep.mutants.corpus import prepare_mutants

                    result["mutants"] = prepare_mutants(
                        out / "scope",
                        repo,
                        out / "mutants",
                        resume=args.resume,
                        image=cfg.get("image"),
                        timeout=cfg.get("mutmut_timeout", 1800),
                    )
                write(out / "pipeline_summary.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("missing_dependencies"):
            return 1
        summaries = [result] + [value for value in result.values() if isinstance(value, dict)]
        incomplete = any(
            row.get("pending_functions", 0) or row.get("pending_targets", 0) for row in summaries
        )
        return 2 if incomplete else 0
    except Exception as exc:
        if args.traceback:
            traceback.print_exc()
        else:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
