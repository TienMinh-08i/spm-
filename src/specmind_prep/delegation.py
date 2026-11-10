"""Seal static forwarding bindings without importing the target project."""
import ast
from collections import Counter
from pathlib import Path


def bind_delegates(problems, index, repo):
    for problem in problems:
        if problem.get('decision') != 'delegate':
            continue
        api = index[problem['function_id']]
        node = ast.parse(api['source']).body[0]
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
        if len(body) != 1 or not isinstance(body[0], ast.Return) or not isinstance(body[0].value, ast.Call):
            raise ValueError('Delegate must be a single forwarding return: '+problem['function_id'])
        call = body[0].value
        target = ast.parse(problem['source']).body[0]
        parameters = [a.arg for a in target.args.posonlyargs+target.args.args]
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id in {'self', 'cls'}:
            parameters = parameters[1:]
        constants = {}
        tree = ast.parse((Path(repo)/api['repo_path']).read_text(encoding='utf-8-sig'))
        module_constants = {}
        for assignment in tree.body:
            if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant):
                for name in assignment.targets:
                    if isinstance(name, ast.Name):
                        module_constants[name.id] = assignment.value.value
        api_parameters = {a.arg for a in node.args.posonlyargs+node.args.args+node.args.kwonlyargs}
        api_parameters.update(a.arg for a in (node.args.vararg, node.args.kwarg) if a is not None)
        pairs = list(zip(parameters, call.args))+[(k.arg, k.value) for k in call.keywords]
        argument_map = {}
        for name, value in pairs:
            if name is None:
                raise ValueError('Expanded kwargs need an explicit delegate adapter')
            if isinstance(value, ast.Name) and value.id in api_parameters:
                argument_map[name] = value.id
            elif isinstance(value, ast.Constant):
                constants[name] = value.value
            elif isinstance(value, ast.Name) and value.id in module_constants:
                constants[name] = module_constants[value.id]
            else:
                raise ValueError('Delegate argument is not a parameter or literal constant')
        problem['_delegate_binding'] = {'api_path': api['repo_path'], 'api_code_line': api['code_line'],
                                        'constants': constants, 'argument_map': argument_map,
                                        'require_ancestor': True}
    # A unique literal tag identifies deferred decorator factory calls even
    # after the API frame has returned. Other helpers require the API ancestor.
    tags = Counter((p['problem_id'], p['_delegate_binding']['constants'].get('tag'))
                   for p in problems if '_delegate_binding' in p)
    for p in problems:
        binding = p.get('_delegate_binding')
        if binding and isinstance(binding['constants'].get('tag'), str):
            if tags[(p['problem_id'], binding['constants']['tag'])] != 1:
                raise ValueError('Ambiguous deferred delegate tag')
            binding['require_ancestor'] = False
    return problems
