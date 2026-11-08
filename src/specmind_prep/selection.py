"""Step 1-3 source/docs inventory and explicit selection. No target imports or tests."""
import argparse
import ast
import json
from html.parser import HTMLParser
from pathlib import Path

from specmind_eval.common.fingerprint import digest, file_hash, source_environment
from specmind_eval.common.jsonl import read_jsonl, write_jsonl
from specmind_eval.common.storage import read, write
from specmind_eval.common.outputs import separate_output
from specmind_prep.docs_build import validate_docs
from specmind_prep.gather.gather import revision
from specmind_prep.project import load_project
from specmind_prep.public_api import inventory
from specmind_prep.source_index import source_index


class Sections(HTMLParser):
    """Capture each Sphinx object definition, including its signature and description."""
    def __init__(self):
        super().__init__()
        self.sections = {}
        self.stack = []
        self.pending = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'dl':
            self.stack.append({'parts': [], 'ids': []})
        if tag == 'dt' and 'sig-object' in attrs.get('class', '').split() and self.stack:
            if attrs.get('id'):
                self.stack[-1]['ids'].append(attrs['id'])

    def handle_data(self, text):
        for item in self.stack:
            item['parts'].append(text)

    def handle_endtag(self, tag):
        if tag == 'dl' and self.stack:
            item = self.stack.pop()
            text = ' '.join(' '.join(item['parts']).split())
            for name in item['ids']:
                self.sections[name] = text


def collect_inventory(repo, project, docs, docs_manifest, review_ids):
    proposed, _ = inventory(repo, project)
    index, _ = source_index(repo, project)
    sections = {}
    for page in docs_manifest['config']['pages']:
        parser = Sections()
        parser.feed((docs/'html'/f'{page}.html').read_text(encoding='utf8'))
        for name, text in parser.sections.items():
            sections[name] = (page+'.html#'+name, text)
    rows = []
    for item in proposed['api']:
        if item['kind'] not in ('function', 'method'):
            continue
        key = item['definition']
        evidence = next((sections[n] for n in [key, *item['public_names']] if n in sections), None)
        node = ast.parse(index[key]['source']).body[0]
        status = 'missing_document' if not evidence else 'review' if key in review_ids else 'documented'
        rows.append({'function_id': key, 'commit': project['commit'], 'status': status,
                     'public_names': item['public_names'], 'docstring': ast.get_docstring(node, clean=False),
                     'document_link': evidence[0] if evidence else None,
                     'document': evidence[1] if evidence else None})
    return rows, index


def selected_candidates(rows, selection, index, project, limit=5):
    """Validate the explicitly scoped sample. Never turn pending rows into drop."""
    if not isinstance(limit, int) or not 1 <= limit <= 22:
        raise ValueError('Selection limit must be between 1 and 22')
    if not isinstance(selection, list) or not selection:
        raise ValueError('Selection must contain explicit rows')
    by_id = {r['function_id']: r for r in rows}
    seen, result, errors = set(), [], []
    for row in selection:
        key, decision = row.get('function_id'), row.get('decision')
        if key in seen:
            errors.append('Duplicate selection: '+str(key))
        seen.add(key)
        if key not in by_id:
            errors.append('Unknown function: '+str(key)); continue
        if decision not in {'keep', 'drop'}:
            errors.append('Selection supports keep/drop only: '+key); continue
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append('Selection requires a reason for every decision: '+key)
        if decision == 'drop':
            continue
        if by_id[key]['status'] != 'documented':
            errors.append('Unresolved Step 1 eligibility: '+key); continue
        result.append(dict(index[key], **by_id[key], decision='keep', reason=row['reason'], target=None,
                           task_id=project['name']+'/'+key, _python_path=project['python_path']))
    if errors:
        raise ValueError('\n'.join(errors))
    if not 1 <= len(result) <= limit:
        raise ValueError(f'Selection must keep 1 to {limit} candidates')
    return sorted(result, key=lambda p: p['problem_id'])


def validate_selection(rows, selection, index, project, limit=22):
    """Full rows preserve Step 1 evidence; collect all row errors before rejecting."""
    if not isinstance(selection, list) or not selection:
        raise ValueError('Selection must contain explicit rows')
    errors = []
    by_id = {r['function_id']: r for r in rows}
    for i, row in enumerate(selection):
        if not isinstance(row, dict):
            errors.append(f'Row {i}: expected object')
            continue
        original = by_id.get(row.get('function_id'))
        if original:
            for field, expected in original.items():
                if field not in row or row[field] != expected:
                    errors.append(f'Row {i}: changed or missing inventory field {field}')
        if 'target' not in row or row['target'] is not None:
            errors.append(f'Row {i}: target must be null for keep/drop; delegation is unsupported')
    if any(not isinstance(row, dict) for row in selection):
        raise ValueError('\n'.join(errors))
    try:
        candidates = selected_candidates(rows, selection, index, project, limit)
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        raise ValueError('\n'.join(errors))
    return candidates


def prepare_selection(repo, project_path, docs, selection_path, output, *, review_ids=(), tests=None, limit=22):
    """Seal a validated manual selection as candidates without collecting any tests."""
    repo, docs, selection_path = [Path(p).resolve() for p in (repo, docs, selection_path)]
    output = separate_output(output, repo, docs, selection_path)
    project = load_project(project_path, repo, documented_only=True)
    if revision(repo) != project['commit']:
        raise ValueError('Source commit mismatch')
    dm = validate_docs(docs, project['commit'])
    rows, index = collect_inventory(repo, project, docs, dm, set(review_ids))
    selection = read_jsonl(selection_path)
    candidates = validate_selection(rows, selection, index, project, limit)
    if output.exists():
        raise ValueError('Output exists; choose a new selection directory')
    output.mkdir(parents=True)
    write_jsonl(output/'inventory.jsonl', rows)
    write_jsonl(output/'selection.jsonl', selection)
    write_jsonl(output/'problems.jsonl', candidates)
    write(output/'docs_reference.json', {'path': str(docs), 'output_digest': dm['output_digest'],
                                       'review_ids': list(review_ids)})
    summary = {'candidate_functions': len(candidates), 'inventory_functions': len(rows),
               'documented_functions': sum(r['status'] == 'documented' for r in rows), 'output': str(output)}
    write(output/'summary.json', summary)
    files = {n: file_hash(output/n) for n in ('inventory.jsonl', 'selection.jsonl', 'problems.jsonl',
                                             'docs_reference.json', 'summary.json')}
    manifest = {'schema': 'candidates-v1', 'complete': True, 'producer': 'manual-selection-v1',
                'project': project, 'repo': str(repo), 'repo_commit': project['commit'],
                'source_environment': source_environment(repo), 'test_selection': tests,
                'files': files, 'lineage': {'docs_digest': dm['output_digest']},
                'fingerprint': digest({'files': files, 'project': project, 'tests': tests,
                                       'environment': source_environment(repo)})}
    write(output/'manifest.json', manifest)
    return summary


def validate_selection_artifact(directory, repo, manifest):
    """Recheck sealed evidence against live source and the referenced docs artifact."""
    directory = Path(directory)
    required = {'inventory.jsonl', 'selection.jsonl', 'docs_reference.json'}
    if not required <= manifest['files'].keys():
        raise ValueError('Manual candidates missing selection evidence')
    ref = read(directory/'docs_reference.json')
    docs = Path(ref['path'])
    dm = validate_docs(docs, manifest['repo_commit'])
    if dm['output_digest'] != ref['output_digest'] or manifest['lineage']['docs_digest'] != dm['output_digest']:
        raise ValueError('Candidate docs fingerprint mismatch')
    rows, index = collect_inventory(Path(repo), manifest['project'], docs, dm, set(ref['review_ids']))
    if rows != read_jsonl(directory/'inventory.jsonl'):
        raise ValueError('Candidate inventory differs from source/docs')
    candidates = validate_selection(rows, read_jsonl(directory/'selection.jsonl'), index, manifest['project'])
    if candidates != read_jsonl(directory/'problems.jsonl'):
        raise ValueError('Candidates differ from validated selection')
    expected = digest({'files': manifest['files'], 'project': manifest['project'],
                       'tests': manifest.get('test_selection'), 'environment': manifest['source_environment']})
    if manifest['fingerprint'] != expected:
        raise ValueError('Manual candidate fingerprint mismatch')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'project', 'docs', 'selection', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--review-id', action='append', default=[])
    parser.add_argument('--test', action='append')
    parser.add_argument('--limit', type=int, default=22)
    args = parser.parse_args()
    print(json.dumps(prepare_selection(args.repo, args.project, args.docs, args.selection, args.output,
                                      review_ids=args.review_id, tests=args.test, limit=args.limit), indent=2))
