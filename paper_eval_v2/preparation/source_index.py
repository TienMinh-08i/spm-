"""AST source indexing shared by extraction and scope preparation."""
import ast
from pathlib import Path

def source_index(repo):
    repo = Path(repo).resolve()
    package = repo/'src'
    if not package.exists():
        package = repo
    records, by_location = {}, {}
    for path in sorted(package.rglob('*.py')):
        if 'tests' in path.relative_to(package).parts:
            continue
        text = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(text)
        module = path.relative_to(package).with_suffix('').as_posix().replace('/', '.')
        if module.endswith('.__init__'):
            module = module[:-9]
        def visit(nodes, parents):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    visit(node.body, parents+[node.name])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    key = '.'.join([module, *parents, node.name])
                    source = ast.unparse(node)+'\n'
                    # Last definition wins over typing.overload declarations.
                    records[key] = {'problem_id': key, 'module': module, 'parent_name': '.'.join(parents) or None,
                                    'entry_point': node.name, 'repo_path': path.relative_to(repo).as_posix(),
                                    'source': source, 'line': node.lineno,
                                    'decorators': [ast.unparse(d) for d in node.decorator_list],
                                    'async': isinstance(node, ast.AsyncFunctionDef)}
                    first = min([node.lineno]+[d.lineno for d in node.decorator_list])
                    by_location[(str(path.resolve()).casefold(), first)] = key
        visit(tree.body, [])
    return records, by_location

