"""Content hashes and runtime identity; algorithms preserved."""
import hashlib
import json
import os
import importlib.metadata
import platform
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tree_digest(root):
    """Hash Python/config inputs including uncommitted edits, excluding outputs."""
    root = Path(root)
    ignored = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "build", "dist"}
    files = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ignored)
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix in {".py", ".toml", ".ini", ".cfg", ".txt", ".json", ".yaml", ".yml"}:
                files.append(
                    (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                )
    return digest(files)


def source_environment(repo):
    identity = runtime_identity(repo)
    identity.pop('engine')
    identity['packages'] = [list(package) for package in identity['packages']]
    return identity


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_identity(repo):
    return {
        'repo': tree_digest(repo), 'engine': engine_digest(),
        'python': platform.python_version(), 'platform': platform.platform(),
        'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()
                           if d.metadata.get('Name')),
    }


def evaluation_identity(repo, config):
    return digest({**runtime_identity(repo),
                   'pytest_timeout': config['pytest_timeout'], 'batch_size': config['batch_size']})



def engine_digest():
    """Hash the complete engine packages, never the surrounding site-packages."""
    root = Path(__file__).resolve().parents[2]
    return digest({name: tree_digest(root / name) for name in
                   ("specmind_eval", "specmind_prep", "paper_eval_v2")})
