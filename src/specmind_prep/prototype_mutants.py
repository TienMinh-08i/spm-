"""Timed local worker for pinned mutmut 2.5.1 source operators, not its test runner."""
import argparse
import ast
import difflib
from pathlib import Path
import mutmut
from specmind_eval.common.storage import read, write
from specmind_eval.common.fingerprint import digest
from specmind_prep.mutants.worker import executable_ast


def export(targets):
    if mutmut.__version__ != '2.5.1':
        raise ValueError('Pilot requires mutmut 2.5.1 in an isolated vendor directory')
    result = {}
    for target in targets:
        source = target['source']
        node = ast.parse(source).body[0]
        if node.decorator_list or isinstance(node, ast.AsyncFunctionDef):
            raise ValueError('Pilot worker supports undecorated synchronous functions only')
        signature = ast.dump(node.args)
        original = executable_ast(source)
        ids = mutmut.list_mutations(mutmut.Context(source=source, filename='target.py'))
        mutants, skipped, seen = [], [], {original}
        for i, mutation_id in enumerate(ids):
            mutated, count = mutmut.mutate(mutmut.Context(source=source, filename='target.py', mutation_id=mutation_id))
            try:
                tree = ast.parse(mutated)
                if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
                    raise ValueError('Expected one function')
                changed = tree.body[0]
                if count != 1 or changed.name != node.name or ast.dump(changed.args) != signature:
                    raise ValueError('Signature changed or not exactly one mutation')
                compile('from __future__ import annotations\n'+mutated, '<mutant>', 'exec')
                key = executable_ast(mutated)
                if key in seen:
                    raise ValueError('Unchanged executable AST or duplicate')
                seen.add(key)
            except (SyntaxError, ValueError) as exc:
                skipped.append({'id': str(i), 'reason': str(exc)})
                continue
            mutants.append({'id': str(i), 'function_source': mutated, 'source_sha256': digest(mutated),
                            'diff': ''.join(difflib.unified_diff(source.splitlines(True), mutated.splitlines(True))),
                            'generator': 'mutmut-2.5.1-source-api'})
        result[target['problem_id']] = {'discovered': len(ids), 'mutants': mutants, 'skipped': skipped}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    write(args.output, export(read(args.input)))
