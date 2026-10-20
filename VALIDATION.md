# Validation — 2026-10-20

- Windows, Python 3.11.6, pytest 8.4.2; existing pinned evaluator environment, not a fresh environment installation.
- Packaged source import verified to resolve inside this repository.
- Full suite: **93 passed in 156.93 seconds**. Includes prepared runner avoiding filtering/export, offline dataset controls, callee mutation, resume and separate debug/suite evaluation. No paid LLM calls.
- `pip check`: no broken requirements in the test environment.
- Wheel built successfully with the declared isolated build backend; installed with `--no-deps` into a separate target folder. Python isolated-mode import, CLI help, prompts and provenance present. Runtime dependencies came from the existing test environment; this is not a full clean-dependency installation test.
- Wheel SHA256: `9fbcb6d8a148865eb962cc2596c8054b6d9c9e2750a8b0ba24773bd5bfb67a0c`.
- Initial build attempt with `--no-build-isolation` failed because the existing environment lacked `bdist_wheel`; the normal isolated build succeeded without changing runtime dependencies.
- All 43 Python files and prompt/provenance assets match the original source hashes in SOURCE_SNAPSHOT.json. Original workspace code was not modified.
- Credential-pattern scan found no matching API token/private-key patterns in the release files. This is a limited pattern check, not an exhaustive security audit.
- Existing trailing whitespace in prompt files was intentionally retained to preserve prompt bytes.

## Limits

Docker daemon was unavailable. The new minimal Python 3.12 mutant image has NOT been built/run in this release validation. A fresh full 57-function mutant export and real LLM benchmark were NOT run. Dataset, target checkout, mutants and prior results are not included. Source-checkout layout is required for Docker mutant preparation; see root README. Historical reports under docs/history do not certify this release.

## Packaging changes

Portable prepared and offline configs, root README with separate preparation/main/debug commands, dedicated mutant Dockerfile, ignore rules for credentials and runtime artifacts, original-source hashes, and separation of historical instructions. Evaluator behavior and metric logic are unchanged.
