# Validation — 2026-10-21

- Windows, Python 3.11.6, pytest 8.4.2; fresh virtual environment installed from requirements.lock.
- **105 tests passed in 248.67 seconds**: 93 prior tests plus 12 integration tests. Includes exact parameterized nodeids, positive/negative probes, swallowed assertion failures, call-only links, property/private targets, one-level callee scope, resume, raw tampering, CLI exit codes, RST filtering and external Docker mount command construction.
- `pip check` passed. Wheel installed in the fresh environment; isolated-mode CLI and console entry point resolved the installed package, not the original workspace. Prompts and provenance remain packaged.
- Wheel SHA256: `2e16005836190506bbb82da14f409e67aa503743a4162667479c2ad51f9d8093`.
- New preparation modules and integration test pass Ruff checks.
- Namespace-normalized AST comparison confirmed the shared filter/evaluation/scope logic matches the standalone preparation engine. No duplicate engine was added.
- Raw extraction on the pinned Marshmallow checkout observed 59 candidates, 10,312 links, 894 unique linked tests, 1,178 collected tests. No explicit exclusions were used. One original timestamp-overflow test fails its expected error-message match on Windows; raw preserves this evidence for offline filtering.
- Optional RST selection returns 28 documented candidates. Fixed the inherited RST reader's incorrect `.__init__` module suffix; regression test covers package-level functions.

## Initial validation failures

The first integration run had 103 passes and two failures. The RST package-module naming issue was fixed. The other failure was a legitimate environment-fingerprint rejection: building package metadata concurrently changed the discovered distribution list between experiment arms. Final validation ran after build/install finished; guards were not weakened. The interrupted initial real-data preparation remains outside the repository and is not a validated dataset.

## Limits

Docker Desktop was started, but its daemon did not respond within a bounded check; it was shut down to release memory. Docker command construction is unit-tested; the new image and a fresh full mutant export have NOT been runtime-validated. No paid LLM calls were made. Historical SOURCE_SNAPSHOT.json describes the first release, not an assertion that all integrated source is unchanged. Use new outputs when fingerprints differ.

## Final focused checks

After the 105-test suite, the CLI was tightened to return exit code 2 for incomplete mutant exports, both individually and in `all`. The 14 preparation integration tests (including two new cases) all passed in 31.66 seconds. The entire 107-test suite was not rerun after this focused CLI-only change.

Real Marshmallow pilot: `class_registry.get_class`, 6 raw links, 4 tests selected by True/False, 1 mutation target. Raw/dataset/scope validation and checkpoint resume passed. This pilot ran from source with the fresh dependency environment; installed-wheel extraction was independently checked above.

At the user's request, full filtering was stopped after 16 of 59 functions completed, with 1,889 selected links at that checkpoint. It is an incomplete diagnostic run, not a published full dataset or a replacement for the historic 57-function corpus. No fresh full corpus comparison is claimed.

Code changes: root package installation; preparation CLI and raw extraction; shared existing engine; RST package-name fix; separate read-only Docker package/checkout mounts; failure exit status propagation; portable configs and pipeline/debug documentation. The main assertion scoring and generation loop were not changed.
