# Offline mutant preparation

The production path has three separate preparation artifacts:
1. `prepare-dataset`: positive `assert True` and negative `assert False` probes, with exact collected nodeids.
2. `prepare-mutation-scope`: short function selection and direct call tracing on prepared tests.
3. `prepare-mutants`: Docker / mutmut export, per-target checkpoints and root-to-mutant mappings.

`run` configured with `prepared_dataset` and `prepared_mutants` only loads these artifacts. It does not call preflight filtering, callee discovery, Docker, or mutant export. Existing legacy configurations remain supported; use the provided prepared config for this pipeline.

## Scope rule

Count lines in the AST-unparsed function body, excluding the initial docstring. Declarations, decorators, comments and blank lines are excluded; semicolon-separated statements become separate canonical lines. A function with fewer than five body lines includes its own source plus direct internal callees. Other functions include only their own source. No recursive expansion. Standard-library and builtin code are not mutation targets.

Resolve source calls conservatively and augment them with direct Python-call observations from selected tests. Properties, callable objects, polymorphic methods and metaclass constructor hooks can be observed at runtime. Lexical candidates not observed are retained with `lexical_unobserved` coverage; this is not a claim that tests exercise them. Unresolved/external calls remain in `scope.json` for inspection.

For the current prepared Marshmallow dataset: 57 roots, 34 short functions, 68 unique source targets, 85 root-target pairs. Export a shared callee once and reference it from each applicable root. Each mutant ID contains the source target and the mutmut ID.

## Commands (from C:/specmind)

```powershell
python -m paper_eval_v2 prepare-mutation-scope --dataset datasets/marshmallow_prepared_59_20261004_verified --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --output datasets/marshmallow_mutation_scope_57_20261004
python -m paper_eval_v2 prepare-mutants --scope datasets/marshmallow_mutation_scope_57_20261004 --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --output datasets/marshmallow_mutants_57_20261004_verified
python -m paper_eval_v2 validate-mutants --dataset datasets/marshmallow_prepared_59_20261004_verified --mutants datasets/marshmallow_mutants_57_20261004_verified --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest
```

Add `--resume` to preparation commands to reuse completed checkpoints. Hashes bind artifacts to source, environment, dataset, implementation and Docker image. Incomplete manifests are rejected by the main path. Source/environment changes require new preparation outputs.

The configuration `configs/marshmallow_prepared_57.json` points to the existing prepared artifacts. Running it invokes assertion generation and evaluation; preparing mutants does not invoke an LLM.

## Execution semantics

The assertion stays on the root function. Exactly one source target is replaced per pytest subprocess, including when that target is a callee. Module aliases and descriptors are preserved. Class-cell methods retain zero-argument `super()` semantics. Class methods are compiled in lexical class context to preserve private-name mangling. Root parameter binding uses the original signature. The subprocess ends before the next mutant is installed.

Candidate feedback records assertion violations and mutation-call evidence. An uncalled mutant is unknown, not a survivor. Original-suite kills are evaluated separately with a neutral assertion. Installation errors, unavailable coverage and postcondition runtime errors must not be counted as assertion kills.

The exporter uses an isolated function module and an import smoke test to enumerate mutmut source variants. Its mutmut survival/kill labels are not benchmark scores. Benchmark results come only from the repository tests through the evaluator. Syntax-invalid mutants, executable-AST duplicates and changes only to the function docstring/annotations are excluded with explicit reasons. Executable-AST deduplication is not proof of semantic equivalence.

## Debug artifacts

- `scope.json`, `targets.json`: root mapping, source and resolution evidence.
- `trace/*.json`: baseline status, exact test nodeids and direct call evidence.
- `targets/<hash>/export.json`: target source, discovery/export counts, exclusions, elapsed time or traceback.
- `targets/<hash>/mutmut_function_exports/`: original mutmut manifest, individual sources/diffs, workspaces and full stdout/stderr logs (inspect the export manifest for paths).
- `functions.json`: ready-to-run mutants and their distinct mutation target metadata for each root.
- `summary.json`, `manifest.json`, `docker.log`: corpus completeness and provenance.

Use the standalone `debug mutant` command on a saved assertion run to replay a specific root, composite mutant ID and optional single nodeid into a separate debug directory. Preparation, debug replay and the main run have independent outputs.
