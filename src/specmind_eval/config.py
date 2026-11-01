"""Evaluation defaults and configuration validation."""
from pathlib import Path
from specmind_eval.common.storage import read

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


def load_config(config_path, parser):
    supplied = read(config_path)
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
                (config_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
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
    return config
