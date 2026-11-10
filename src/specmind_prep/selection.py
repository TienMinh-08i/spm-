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


def reviewed_inventory(rows, eligibility):
    """Apply explicit Step 1 review; omitted proposals are never implicit exclusions."""
    if not isinstance(eligibility, list) or any(not isinstance(r, dict) for r in eligibility):
        raise ValueError('Eligibility must contain explicit objects')
    proposed = {r['function_id']: r for r in rows}
    ids = [r.get('function_id') for r in eligibility]
    if len(ids) != len(set(ids)) or set(ids) != set(proposed):
        raise ValueError('Eligibility must cover every inventory function exactly once')
    included = set()
    for row in eligibility:
        if type(row.get('included')) is not bool or not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('Eligibility requires boolean included and an evidence reason')
        key = row['function_id']
        if row['included']:
            if proposed[key]['status'] != 'documented':
                raise ValueError('Unresolved Step 1 eligibility: '+key)
            included.add(key)
    return [r for r in rows if r['function_id'] in included]


def selected_candidates(rows, selection, index, project, limit=5):
    """Validate the explicitly scoped sample. Never turn pending rows into drop."""
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('Selection limit must be a positive integer')
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
        if decision not in {'keep', 'drop', 'delegate'}:
            errors.append('Selection supports keep/drop/delegate only: '+key); continue
        if decision == 'drop' and (not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            errors.append('Selection requires a reason for drop: '+key)
        target = row.get('target')
        if decision != 'delegate' and target is not None:
            errors.append('Target must be null for keep/drop: '+key)
        if decision == 'drop':
            continue
        if by_id[key]['status'] != 'documented':
            errors.append('Unresolved Step 1 eligibility: '+key); continue
        analyzed = target if decision == 'delegate' else key
        if not isinstance(analyzed, str) or analyzed not in index or (decision == 'delegate' and analyzed == key):
            errors.append('Delegate target must be a different source function: '+key); continue
        result.append(dict(index[analyzed], **by_id[key], decision=decision, reason=row.get('reason'), target=target,
                           task_id=project['name']+'/'+key, _python_path=project['python_path']))
    if errors:
        raise ValueError('\n'.join(errors))
    if not result or (limit is not None and len(result) > limit):
        raise ValueError(f'Selection must keep 1 to {limit} candidates')
    return sorted(result, key=lambda p: (p['problem_id'], p['function_id']))


def validate_selection(rows, selection, index, project, limit=None, *, require_complete=True):
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
        if 'target' not in row:
            errors.append(f'Row {i}: missing target')
    if any(not isinstance(row, dict) for row in selection):
        raise ValueError('\n'.join(errors))
    if require_complete:
        selected = {r.get('function_id') for r in selection}
        if selected != set(by_id):
            errors.append('Selection must match finalized Step 1: missing='+str(sorted(set(by_id)-selected))+
                          ', extra='+str(sorted(selected-set(by_id), key=str)))
    try:
        candidates = selected_candidates(rows, selection, index, project, limit)
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        raise ValueError('\n'.join(errors))
    return candidates


def prepare_selection(repo, project_path, docs, selection_path, output, *, review_ids=(), tests=None, limit=None,
                      eligibility_path=None, require_complete=True):
    """Seal a validated manual selection as candidates without collecting any tests."""
    repo, docs, selection_path = [Path(p).resolve() for p in (repo, docs, selection_path)]
    output = separate_output(output, repo, docs, selection_path)
    project = load_project(project_path, repo, documented_only=True)
    if revision(repo) != project['commit']:
        raise ValueError('Source commit mismatch')
    dm = validate_docs(docs, project['commit'])
    rows, index = collect_inventory(repo, project, docs, dm, set(review_ids))
    eligibility = read_jsonl(eligibility_path) if eligibility_path is not None else None
    eligible = reviewed_inventory(rows, eligibility) if eligibility is not None else rows
    selection = read_jsonl(selection_path)
    candidates = validate_selection(eligible, selection, index, project, limit, require_complete=require_complete)
    from specmind_prep.delegation import bind_delegates
    candidates = bind_delegates(candidates, index, repo)
    if output.exists():
        raise ValueError('Output exists; choose a new selection directory')
    output.mkdir(parents=True)
    write_jsonl(output/'inventory.jsonl', rows)
    write_jsonl(output/'selection.jsonl', selection)
    write_jsonl(output/'problems.jsonl', candidates)
    if eligibility is not None:
        write_jsonl(output/'eligibility.jsonl', eligibility)
    write(output/'docs_reference.json', {'path': str(docs), 'output_digest': dm['output_digest'],
                                       'review_ids': list(review_ids)})
    summary = {'candidate_functions': len(candidates), 'inventory_functions': len(rows),
               'public_api_functions': len(eligible),
               'decisions': {d: sum(r['decision'] == d for r in selection) for d in ('keep', 'delegate', 'drop')},
               'unique_analysis_targets': len({p['problem_id'] for p in candidates}),
               'documented_functions': sum(r['status'] == 'documented' for r in rows), 'output': str(output)}
    write(output/'summary.json', summary)
    files = {n: file_hash(output/n) for n in ('inventory.jsonl', 'selection.jsonl', 'problems.jsonl',
                                             'docs_reference.json', 'summary.json')}
    if eligibility is not None:
        files['eligibility.jsonl'] = file_hash(output/'eligibility.jsonl')
    selection_policy = {'eligibility_reviewed': eligibility is not None, 'selection_complete': require_complete}
    manifest = {'schema': 'candidates-v1', 'complete': True, 'producer': 'manual-selection-v3',
                'eligibility_reviewed': eligibility is not None,
                'selection_complete': require_complete,
                'project': project, 'repo': str(repo), 'repo_commit': project['commit'],
                'source_environment': source_environment(repo), 'test_selection': tests,
                'files': files, 'lineage': {'docs_digest': dm['output_digest']},
                'fingerprint': digest({'files': files, 'project': project, 'tests': tests,
                                       'environment': source_environment(repo), 'selection_policy': selection_policy})}
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
    if manifest.get('eligibility_reviewed'):
        if 'eligibility.jsonl' not in manifest['files']:
            raise ValueError('Manual candidates missing eligibility evidence')
        rows = reviewed_inventory(rows, read_jsonl(directory/'eligibility.jsonl'))
    candidates = validate_selection(rows, read_jsonl(directory/'selection.jsonl'), index, manifest['project'],
                                    require_complete=manifest.get('selection_complete', manifest['producer'] != 'manual-selection-v1'))
    if manifest['producer'] == 'manual-selection-v3':
        from specmind_prep.delegation import bind_delegates
        candidates = bind_delegates(candidates, index, repo)
    if candidates != read_jsonl(directory/'problems.jsonl'):
        raise ValueError('Candidates differ from validated selection')
    payload = {'files': manifest['files'], 'project': manifest['project'],
               'tests': manifest.get('test_selection'), 'environment': manifest['source_environment']}
    if manifest['producer'] in {'manual-selection-v2', 'manual-selection-v3'}:
        payload['selection_policy'] = {k: manifest[k] for k in ('eligibility_reviewed', 'selection_complete')}
    expected = digest(payload)
    if manifest['fingerprint'] != expected:
        raise ValueError('Manual candidate fingerprint mismatch')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'project', 'docs', 'selection', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--review-id', action='append', default=[])
    parser.add_argument('--test', action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--eligibility', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_selection(args.repo, args.project, args.docs, args.selection, args.output,
                                      review_ids=args.review_id, tests=args.test, limit=args.limit,
                                      eligibility_path=args.eligibility), indent=2))
