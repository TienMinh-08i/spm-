"""Evaluation identity shared by normal runs, offline suite and debug cache."""
import importlib.metadata
import platform
from pathlib import Path
from .storage import digest, tree_digest


def runtime_identity(repo):
    return {
        'repo': tree_digest(repo), 'engine': tree_digest(Path(__file__).parent),
        'python': platform.python_version(), 'platform': platform.platform(),
        'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()
                           if d.metadata.get('Name')),
    }


def evaluation_identity(repo, config):
    return digest({**runtime_identity(repo),
                   'pytest_timeout': config['pytest_timeout'], 'batch_size': config['batch_size']})
