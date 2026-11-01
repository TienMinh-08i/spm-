"""AST source indexing shared by extraction and scope preparation."""
import ast
import os
from pathlib import Path

def source_index(repo, project=None):
    repo = Path(repo).resolve()
    if project is None:
        from .project import infer_project
        project = infer_project(repo)
    package = repo / project['source_root']
    test_roots = [(repo / value).resolve() for value in project['tests']]
    records, by_location = {}, {}
    paths = {p for name in project['packages'] for p in (package / name.replace('.', '/')).rglob('*.py')}
    for path in sorted(paths):
        if any(path.resolve().is_relative_to(test) for test in test_roots):
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
                                    'code_line': min([node.lineno]+[d.lineno for d in node.decorator_list]),
                                    'decorators': [ast.unparse(d) for d in node.decorator_list],
                                    'async': isinstance(node, ast.AsyncFunctionDef)}
                    first = min([node.lineno]+[d.lineno for d in node.decorator_list])
                    by_location[(os.path.normcase(str(path.resolve())), first)] = key
        visit(tree.body, [])
    return records, by_location

