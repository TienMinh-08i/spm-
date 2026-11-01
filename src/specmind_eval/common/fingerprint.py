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
                   'pytest_timeout': config['pytest_timeout'], 'batch_size': config['batch_size'],
                   'python_path': config.get('python_path')})



def engine_digest():
    """Hash explicit evaluation entry points and their local dependency closure."""
    return implementation_digest('specmind_eval.run', 'specmind_eval.experiment',
                                 'specmind_eval.offline.replay', 'specmind_eval.offline.suite',
                                 'specmind_eval.offline.join',
                                 'specmind_eval.evaluate.injected.postcondition_plugin',
                                 'specmind_eval.evaluate.injected.mutation_runtime')


def implementation_files(*entrypoints):
    """Hash the local import closure of explicit entry modules; exclude tests/builds."""
    import ast
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    pending = list(entrypoints)
    seen = set()
    files = {}
    while pending:
        module = pending.pop()
        if module in seen or not module.startswith(('specmind_eval', 'specmind_prep')):
            continue
        seen.add(module)
        path = root.joinpath(*module.split('.')).with_suffix('.py')
        if not path.is_file():
            path = root.joinpath(*module.split('.'), '__init__.py')
        if not path.is_file():
            continue
        files[path.relative_to(root).as_posix()] = file_hash(path)
        package = module if path.name == '__init__.py' else module.rsplit('.',1)[0]
        for node in ast.walk(ast.parse(path.read_text(encoding='utf8'))):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                name = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module
                if name:
                    pending.append(name)
                    pending.extend(name + '.' + alias.name for alias in node.names)
        if module == 'specmind_eval.generate.loop':
            for prompt in path.with_name('prompts').glob('*.txt'):
                files[prompt.relative_to(root).as_posix()] = file_hash(prompt)
    return dict(sorted(files.items()))


def implementation_digest(*entrypoints):
    return digest(implementation_files(*entrypoints))
