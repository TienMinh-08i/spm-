"""Small stdlib-only call ownership protocol shared by injected runtimes."""
import os
from pathlib import Path


def matches_call(binding, arguments, frame, repo):
    for name, value in binding.get('constants', {}).items():
        # A factory such as validates() forwards fn=None first, then the
        # returned partial supplies the decorated function. Its literal tag
        # remains the API discriminator in both calls.
        if (name == 'fn' and value is None and not binding.get('require_ancestor', True)
                and isinstance(binding.get('constants', {}).get('tag'), str)):
            continue
        actual = arguments.get(name)
        if name not in arguments or type(actual) is not type(value) or actual != value:
            return False
    if not binding.get('require_ancestor', True):
        return True
    expected = os.path.normcase(str((Path(repo)/binding['api_path']).resolve()))
    while frame is not None:
        code = frame.f_code
        if (code.co_firstlineno == binding['api_code_line']
                and os.path.normcase(str(Path(code.co_filename).resolve())) == expected):
            return True
        frame = frame.f_back
    return False
