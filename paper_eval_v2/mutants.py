"""Verified migration of existing exports and native v2 mutant preparation."""

from __future__ import annotations

import ast
import difflib
from pathlib import Path
from types import SimpleNamespace

from .dataset import DatasetSpec, function_code
from .mutmut import export_mutmut_function_mutants, load_exported_mutants
from .source import source_match
from .storage import digest, read, write


def export_bundle_digest(config):
    path = config.get("reuse_mutmut_rows")
    if not path:
        return None
    rows = read(path)
    if not isinstance(rows, list):
        raise ValueError("reuse_mutmut_rows must point to a JSON list")
    artifacts = []
    for row in rows:
        manifest_path = mapped_path(row.get("manifest_path", ""), config["path_mappings"])
        manifest = read(manifest_path) if manifest_path.is_file() else None
        files = []
        if manifest:
            for item in manifest.get("mutants", []):
                file = manifest_path.parent / item["file"]
                files.append((item["file"], file.read_text(encoding="utf-8") if file.is_file() else None))
        artifacts.append((row.get("problem_id"), manifest, files))
    return digest(artifacts)


def mapped_path(text, mappings):
    normalized = str(text).replace("\\", "/")
    for old, new in mappings.items():
        old = old.rstrip("/")
        if normalized == old or normalized.startswith(old + "/"):
            return Path(new) / normalized[len(old) :].lstrip("/")
    return Path(normalized)


def prepare(problem, repo_root, output, config):
    saved = output / "mutants.json"
    cached = read(saved)
    if cached is not None:
        return cached
    source = function_code(problem)
    if config.get("reuse_mutmut_rows"):
        rows = read(config["reuse_mutmut_rows"])
        key = problem.get("problem_id") or problem.get("qualified_name") or problem.get("task_id")
        matches = [r for r in rows if key in (r.get("problem_id"), r.get("qualified_name"), r.get("task_id"))]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one exported row for {key}, found {len(matches)}; no automatic regeneration"
            )
        row = matches[0]
        manifest_path = mapped_path(row["manifest_path"], config["path_mappings"])
        manifest = read(manifest_path)
        if not manifest:
            raise ValueError(f"Missing export manifest: {manifest_path}")
        if manifest.get("error"):
            raise ValueError(f"Export failed: {manifest['error']}")
        original = manifest.get("original_function_source")
        if not original or source_match(source, original) not in {"exact_ast", "same_except_docstrings"}:
            raise ValueError("Reused mutants' original source does not match dataset")
        mutants = load_exported_mutants(manifest_path.parent, manifest)
        if len(mutants) != manifest.get("total_exported"):
            raise ValueError("Missing exported mutant files; cannot silently shrink denominator")
        origin = str(manifest_path)
    else:
        args = SimpleNamespace(
            _run_dir=str(output), max_mutants=100000, mutmut_run_timeout=config["mutmut_timeout"] or None
        )
        spec = DatasetSpec(config["dataset"], config.get("project", ""), Path("."), "", "")
        _, mutants, manifest = export_mutmut_function_mutants(
            spec=spec, problem=problem, repo_root=repo_root, args=args
        )
        if manifest.get("error"):
            raise ValueError(manifest["error"])
        origin = "native-export"
    ids = [m["id"] for m in mutants]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate mutant IDs")
    for mutant in mutants:
        ast.parse(mutant["function_source"])
        if source_match(source, mutant["function_source"]) in {"exact_ast", "same_except_docstrings"}:
            raise ValueError(f"Mutant {mutant['id']} does not change executable function AST")
        if not mutant.get("diff"):
            mutant["diff"] = "".join(
                difflib.unified_diff(
                    source.splitlines(True),
                    mutant["function_source"].splitlines(True),
                    fromfile="original",
                    tofile="mutant",
                )
            )
    result = {"origin": origin, "original_function_source": source, "mutants": mutants, "manifest": manifest}
    write(saved, result)
    return result
