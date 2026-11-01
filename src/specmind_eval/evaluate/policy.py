"""Conservative behavioral postconditions. A protocol check, not a security sandbox."""
import ast

PURE = set('abs all any bool callable dict enumerate float frozenset int isinstance issubclass len list max min range reversed round set sorted str sum tuple type zip'.split())
MODULES = {
    'inspect': {'isgenerator', 'isgeneratorfunction', 'iscoroutine', 'iscoroutinefunction'},
    'math': {'isclose', 'isfinite', 'isinf', 'isnan', 'ceil', 'floor', 'fabs', 'sqrt', 'pi', 'e', 'inf', 'nan'},
    're': {'match', 'fullmatch', 'search', 'findall', 'split', 'escape'},
    'collections.abc': {'Mapping', 'Sequence', 'Iterable', 'Collection', 'Set'},
}
READ_METHODS = set('get keys values items count index startswith endswith lower upper strip lstrip rstrip split isalpha isdigit isspace'.split())


def validate(assertion, source, entrypoint, policy='legacy'):
    tree = ast.parse(assertion)
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if not asserts:
        raise ValueError('Candidate contains no assert statement')
    if policy == 'legacy':
        return
    if policy != 'behavioral':
        raise ValueError('Unknown assertion policy: ' + policy)
    if len(asserts) != 1 or not isinstance(tree.body[-1], ast.Assert):
        raise ValueError('Behavioral policy requires one final, unconditional assert')
    fn = ast.parse(source).body[0]
    args = fn.args
    inputs = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    inputs.update(a.arg for a in (args.vararg, args.kwarg) if a)
    names = inputs | PURE | {'return_value'}
    modules, imported = {}, set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in MODULES or '.' in alias.name:
                    raise ValueError('Unsupported import: ' + alias.name)
                local = alias.asname or alias.name
                if local in names or local == entrypoint:
                    raise ValueError('Import must not shadow inputs or builtins')
                modules[local] = alias.name
                names.add(local)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in MODULES:
                raise ValueError('Unsupported import module')
            for alias in node.names:
                if alias.name not in MODULES[node.module]:
                    raise ValueError('Unsupported imported name: ' + alias.name)
                local = alias.asname or alias.name
                if local in names or local == entrypoint:
                    raise ValueError('Import must not shadow inputs or builtins')
                imported.add(local)
                names.add(local)
        elif isinstance(node, ast.Assign):
            if any(not isinstance(t, ast.Name) for t in node.targets):
                raise ValueError('Do not mutate inputs or attributes')
            for target in node.targets:
                if target.id in names or target.id == entrypoint:
                    raise ValueError('Do not rebind inputs, imports or builtins')
                names.add(target.id)
        elif not isinstance(node, ast.Assert):
            raise ValueError('Only approved imports, local assignments and a final assert are allowed')
    for n in ast.walk(tree):
        if isinstance(n, ast.comprehension):
            for target in ast.walk(n.target):
                if isinstance(target, ast.Name):
                    if target.id in inputs | {'return_value', entrypoint} | PURE | set(modules) | imported:
                        raise ValueError('Do not shadow inputs or callable names in comprehensions')
                    names.add(target.id)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Lambda, ast.NamedExpr, ast.Await, ast.Yield)):
            raise ValueError('Unsupported expression in behavioral assertion')
        if isinstance(node, ast.Name):
            if node.id == entrypoint or node.id not in names:
                raise ValueError('Name outside input/output contract: ' + node.id)
        if isinstance(node, ast.Attribute):
            if node.attr.startswith('__'):
                raise ValueError('Metadata/dunder access is not allowed')
            if isinstance(node.value, ast.Name) and node.value.id in modules:
                if node.attr not in MODULES[modules[node.value.id]]:
                    raise ValueError('Unsupported module attribute: ' + node.attr)
        if isinstance(node, ast.Call):
            f = node.func
            allowed = isinstance(f, ast.Name) and f.id in PURE | imported
            if isinstance(f, ast.Attribute):
                allowed = (isinstance(f.value, ast.Name) and f.value.id in modules
                           and f.attr in MODULES[modules[f.value.id]]) or f.attr in READ_METHODS
            if not allowed:
                raise ValueError('Call not allowed: ' + ast.unparse(node.func) + '. Only approved pure calls are allowed')
