"""Source-only references for caller context, not a program slicer.

Resolved edges describe lexical references, never proof of runtime dispatch.
No target module is imported or executed.
"""
import ast
import hashlib
import importlib.util
from pathlib import Path


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


class BodyVisitor(ast.NodeVisitor):
    """Do not attribute nested scopes or definition-time expressions to a caller."""

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef


class Bindings(BodyVisitor):
    def __init__(self):
        self.names = set()
        self.calls = []
        self.star_import = False

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Import(self, node):
        self.names.update(a.asname or a.name.split('.')[0] for a in node.names)

    def visit_ImportFrom(self, node):
        self.names.update(a.asname or a.name for a in node.names)
        self.star_import |= any(a.name == '*' for a in node.names)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + '.' + node.attr if base else None
    return None


def module_bindings(tree, module, is_package):
    """Only unique, unconditional declarations are statically usable."""
    declared = {}
    counts = {}
    blocked = Bindings()
    package = module if is_package else module.rpartition('.')[0]
    for node in tree.body:
        entries = {}
        if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            entries[node.name] = module + '.' + node.name
        elif isinstance(node, ast.Import):
            entries = {a.asname or a.name.split('.')[0]: a.name if a.asname else a.name.split('.')[0]
                       for a in node.names}
        elif isinstance(node, ast.ImportFrom) and all(a.name != '*' for a in node.names):
            try:
                base = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module
            except (ImportError, ValueError):
                base = None
            if base:
                entries = {a.asname or a.name: base + '.' + a.name for a in node.names}
        else:
            blocked.visit(node)
        for name, value in entries.items():
            declared[name] = value
            counts[name] = counts.get(name, 0) + 1
    return {k: v for k, v in declared.items() if counts[k] == 1 and k not in blocked.names}, blocked.star_import


def function_nodes(tree, module):
    def walk(nodes, prefix):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                yield from walk(node.body, prefix + '.' + node.name)
            elif isinstance(node, FUNCTIONS):
                yield prefix + '.' + node.name, node
    yield from walk(tree.body, module)


def references(repo, index, function_id, *, context_lines=3):
    """Accept a source index so flat and packaged repositories share this logic."""
    if function_id not in index:
        raise ValueError('Unknown function ID: ' + function_id + '; use a fully qualified name')
    if not 0 <= context_lines <= 20:
        raise ValueError('context_lines must be between 0 and 20')
    repo = Path(repo).resolve()
    target = index[function_id]
    callers, unresolved, hashes = [], [], {}
    target_code_line = target.get('code_line', target['line'])
    files = {row['repo_path']: row['module'] for row in index.values()}
    for filename, module in sorted(files.items()):
        path = (repo / filename).resolve()
        if not path.is_relative_to(repo):
            raise ValueError('Source path must stay inside repo')
        raw = path.read_bytes()
        hashes[filename] = hashlib.sha256(raw).hexdigest()
        text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        lines = text.splitlines()
        tree = ast.parse(text, filename=filename)
        bindings, star_import = module_bindings(tree, module, path.name == '__init__.py')
        for caller, node in function_nodes(tree, module):
            if caller == function_id and node.lineno == target['line']:
                target_code_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
            local = Bindings()
            local.names.update(a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))
            for arg in (node.args.vararg, node.args.kwarg):
                if arg:
                    local.names.add(arg.arg)
            for statement in node.body:
                local.visit(statement)
            for call in local.calls:
                expression = dotted(call.func)
                root = expression.split('.')[0] if expression else None
                resolved = None
                if root and root not in local.names and not star_import:
                    binding = bindings.get(root)
                    if binding:
                        resolved = binding + expression[len(root):]
                if resolved not in index:
                    resolved = None
                # Instance/class attributes, local aliases and dynamic calls stay uncertain.
                if resolved and resolved != function_id:
                    continue
                start = max(node.lineno, call.lineno - context_lines)
                end = min(node.end_lineno, call.end_lineno + context_lines)
                record = {
                    'caller': caller, 'caller_definition': f'{filename}:{node.lineno}',
                    'file': filename, 'line': call.lineno, 'column': call.col_offset + 1,
                    'location': f'{filename}:{call.lineno}',
                    'expression': ast.get_source_segment(text, call),
                    'context_start_line': start, 'context_end_line': end,
                    'context': '\n'.join(lines[start - 1:end]),
                }
                if resolved:
                    record['resolution'] = 'static_reference'
                    callers.append(record)
                else:
                    record['resolution'] = 'unresolved'
                    record['reason'] = 'Dynamic dispatch, shadowed/local binding or unsupported reference'
                    record['name_matches_target'] = bool(expression and expression.split('.')[-1] == target['entry_point'])
                    unresolved.append(record)
    callers.sort(key=lambda r: (r['file'], r['line'], r['column']))
    unresolved.sort(key=lambda r: (r['file'], r['line'], r['column']))
    return {
        'schema': 'function-references-v1',
        'target': {'function': function_id, 'file': target['repo_path'], 'line': target['line'],
                   'code_line': target_code_line,
                   'location': f"{target['repo_path']}:{target['line']}"},
        'callers': callers, 'unresolved_calls': unresolved, 'source_sha256': hashes,
        'context_kind': 'caller_source_excerpt_not_slice',
        'limitations': [
            'Static references are not runtime-confirmed edges; decorators and monkey-patching may change dispatch.',
            'Only indexed package functions and methods are scanned; tests, module/class bodies, nested functions and lambdas are excluded.',
            'Instance dispatch, inherited methods, re-exports and local aliases are not resolved.',
            'Unresolved calls are not confirmed callers of the target; name matches are hints only.',
            'Source excerpts are not data/control-dependency slices and do not establish the full API contract.',
        ],
    }
