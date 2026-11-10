"""Auditable static inventory of RST API pages and Python declarations.

This is an explicit source policy, not a Sphinx rendering/emulation engine.
"""
import ast
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from specmind_prep.references import FUNCTIONS, dotted, module_bindings


def declarations(repo, project):
    repo = Path(repo).resolve()
    source = repo / project['source_root']
    paths = sorted({p for package in project['packages']
                    for p in (source / package.replace('.', '/')).rglob('*.py')})
    modules, symbols, aliases, hashes = {}, {}, {}, {}
    for path in paths:
        if any(path.resolve().is_relative_to((repo / t).resolve()) for t in project['tests']):
            continue
        rel = path.relative_to(repo).as_posix()
        name = path.relative_to(source).with_suffix('').as_posix().replace('/', '.')
        name = name.removesuffix('.__init__')
        text = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(text, filename=rel)
        bindings, star = module_bindings(tree, name, path.name == '__init__.py')
        exported = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == '__all__':
                        try:
                            value = ast.literal_eval(node.value)
                            if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
                                exported = value
                        except (ValueError, TypeError):
                            pass
                    elif isinstance(target, ast.Name) and dotted(node.value):
                        value = dotted(node.value)
                        root, _, suffix = value.partition('.')
                        aliases[name + '.' + target.id] = bindings.get(root, name + '.' + root) + ('.' + suffix if suffix else '')
        for local, value in bindings.items():
            if value != name + '.' + local:
                aliases[name + '.' + local] = value
        modules[name] = {'tree': tree, 'file': rel, 'text': text, 'bindings': bindings,
                         'star': star, 'exports': exported}
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

        def walk(nodes, owner):
            for node in nodes:
                if not isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                    continue
                key = owner + '.' + node.name
                decorators = [dotted(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list]
                kind = 'class' if isinstance(node, ast.ClassDef) else 'method' if owner != name else 'function'
                if 'property' in decorators or any(d and d.endswith(('.setter', '.getter')) for d in decorators):
                    kind = 'property'
                symbols[key] = {'definition': key, 'kind': kind, 'module': name, 'owner': owner,
                                'file': rel, 'line': node.lineno, 'location': f'{rel}:{node.lineno}',
                                'docstring': ast.get_docstring(node) or '',
                                'bases': [ast.unparse(b) for b in node.bases] if kind == 'class' else []}
                if isinstance(node, ast.ClassDef):
                    walk(node.body, key)
        walk(tree.body, name)
    return {'modules': modules, 'symbols': symbols, 'aliases': aliases, 'hashes': hashes}


def canonical(name, aliases):
    seen = set()
    while name not in seen:
        seen.add(name)
        match = next((p for p in sorted(aliases, key=len, reverse=True)
                      if name == p or name.startswith(p + '.')), None)
        if match is None:
            return name
        name = aliases[match] + name[len(match):]
    return None


def directives(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r'^(\s*)\.\.\s+([\w:-]+)::\s*(.*)$', line)
        if not match:
            continue
        indent = len(match[1])
        body = []
        for following in lines[i + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following.strip())
        options = {}
        for entry in body:
            option = re.match(r'^:([\w-]+):\s*(.*)$', entry)
            if option:
                options[option[1]] = option[2]
        yield match[2], match[3].strip(), options, body, i + 1


def inventory(repo, project, *, api_index='api_reference.rst', public_caption='API Reference'):
    repo = Path(repo).resolve()
    docs = (repo / project['docs_dir']).resolve()
    root = (docs / api_index).resolve()
    if not root.is_relative_to(docs):
        raise ValueError('API index must stay inside docs_dir')
    model = declarations(repo, project)
    symbols, aliases = model['symbols'], model['aliases']
    selected, excluded, unresolved, pages, evidence_hashes = {}, [], [], [], {}
    private_pages = []

    def page_path(parent, entry):
        target = entry.rsplit('<', 1)[-1].rstrip('>').strip()
        path = (parent / (target if target.endswith('.rst') else target + '.rst')).resolve()
        if not path.is_relative_to(docs):
            raise ValueError('Documentation link outside docs_dir: ' + entry)
        return path

    root_text = root.read_text(encoding='utf8')
    queue = []
    for directive, value, options, body, line in directives(root_text):
        if directive != 'toctree':
            continue
        links = [x for x in body if x and not x.startswith(':')]
        if options.get('caption') == public_caption:
            queue.extend(page_path(root.parent, x) for x in links)
        else:
            private_pages.extend((page_path(root.parent, x)).relative_to(repo).as_posix() for x in links)
    if not queue:
        raise ValueError('No toctree with public caption: ' + public_caption)
    evidence_hashes[root.relative_to(repo).as_posix()] = hashlib.sha256(root.read_bytes()).hexdigest()

    def add(name, evidence, opts, expand=False):
        key = canonical(name, aliases)
        row = symbols.get(key)
        if row is None:
            unresolved.append({'category': 'api_symbol', 'symbol': name, 'evidence': evidence,
                               'reason': 'No callable/class declaration; may be data, module, external or dynamic'})
            return
        short = name.rsplit('.', 1)[-1]
        if short.startswith('_') or ':meta private:' in row['docstring'] or row['kind'] == 'property':
            excluded.append({'symbol': name, 'kind': row['kind'], 'evidence': evidence,
                             'reason': 'Private-named/meta-private member or non-callable property'})
            return
        if key not in selected:
            selected[key] = {k: v for k, v in row.items() if k not in {'docstring', 'bases'}}
            selected[key].update(public_names=[], evidence=[], experimental='.experimental.' in key)
        record = selected[key]
        if name not in record['public_names']:
            record['public_names'].append(name)
        if evidence not in record['evidence']:
            record['evidence'].append(evidence)
        if expand and row['kind'] == 'class':
            members = opts.get('members', '')
            wanted = {x.strip() for x in members.split(',') if x.strip()}
            excluded_names = {x.strip() for x in opts.get('exclude-members', '').split(',')}
            for member, details in symbols.items():
                child = member.rsplit('.', 1)[-1]
                if details['owner'] != key or child in excluded_names or (wanted and child not in wanted):
                    continue
                if details['docstring'] or 'undoc-members' in opts or child in wanted:
                    add(name + '.' + child, evidence, opts)

    seen = set()
    while queue:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        rel = page.relative_to(repo).as_posix()
        text = page.read_text(encoding='utf8')
        pages.append(rel)
        evidence_hashes[rel] = hashlib.sha256(page.read_bytes()).hexdigest()
        current = ''
        for directive, value, opts, body, line in directives(text):
            evidence = f'{rel}:{line}'
            if directive == 'toctree':
                queue.extend(page_path(page.parent, x) for x in body if x and not x.startswith(':'))
            elif directive == 'currentmodule':
                current = value
            elif directive == 'automodule':
                module = model['modules'].get(value)
                if module is None:
                    unresolved.append({'category': 'api_module', 'symbol': value, 'evidence': evidence})
                    continue
                if 'members' not in opts:
                    continue
                explicit = [x.strip() for x in opts['members'].split(',') if x.strip()]
                names = explicit or module['exports'] or [k.rsplit('.', 1)[-1] for k, r in symbols.items() if r['owner'] == value]
                excluded_names = {x.strip() for x in opts.get('exclude-members', '').split(',')}
                for name in names:
                    key = canonical(value + '.' + name, aliases)
                    row = symbols.get(key)
                    if name in excluded_names or name.startswith('_'):
                        continue
                    if row and (row['docstring'] or 'undoc-members' in opts or name in explicit):
                        # Module members recursively include the class's documented own members.
                        add(value + '.' + name, evidence, {**opts, 'members': ''}, expand=True)
                    elif module['exports'] and key not in symbols:
                        unresolved.append({'category': 'export_non_callable', 'symbol': value + '.' + name,
                                           'evidence': evidence, 'reason': 'Export is data/module or unresolved; not in callable denominator'})
            elif directive in {'autoclass', 'autoexception', 'autofunction', 'automethod', 'autoattribute'}:
                name = value.lstrip('~').split('(')[0]
                if '.' not in name and current:
                    name = current + '.' + name
                add(name, evidence, opts, expand='members' in opts or 'private-members' in opts)
            elif directive == 'autosummary':
                unresolved.append({'category': 'unsupported_directive', 'evidence': evidence,
                                   'reason': 'Standalone autosummary requires review'})
    for row in selected.values():
        row['public_names'].sort()
        row['evidence'].sort()
    result = {'schema': 'public-api-v1', 'policy': 'public-named documented own callables; classes separate; no inherited expansion',
              'api': sorted(selected.values(), key=lambda r: r['definition']), 'pages': sorted(pages),
              'excluded_pages': sorted(private_pages), 'excluded_members': excluded,
              'unresolved': unresolved, 'documentation_sha256': evidence_hashes}
    return result, model


def analyze(repo, project, output, *, api_index='api_reference.rst'):
    from specmind_prep.references import public_calls
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if output.is_relative_to(repo) or repo.is_relative_to(output):
        raise ValueError('Output must be outside analyzed checkout')
    if output.exists():
        raise ValueError('Output already exists; choose a new directory')
    proc = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=30, check=True)
    commit = proc.stdout.strip()
    if project.get('commit') and project['commit'] != commit:
        raise ValueError('Project commit does not match checkout')
    api, model = inventory(repo, project, api_index=api_index)
    calls, unresolved, scan = public_calls(repo, project, api['api'], model)
    rows = []
    for item in api['api']:
        key = item['definition']
        matched = [c for c in calls if c['target'] == key]
        possible = [c for c in unresolved if key in c.get('candidate_targets', [])]
        rows.append({'api': key, 'kind': item['kind'], 'experimental': item['experimental'],
                     'public_names': ';'.join(item['public_names']), 'location': item['location'],
                     'source_callers': len({c['caller_id'] for c in matched if c['scope'] == 'source' and c['caller_kind'] == 'function'}),
                     'test_callers': len({c['caller_id'] for c in matched if c['scope'] == 'tests' and c['caller_kind'] == 'function'}),
                     'source_sites': sum(c['scope'] == 'source' for c in matched),
                     'test_sites': sum(c['scope'] == 'tests' for c in matched),
                     'non_function_sites': sum(c['caller_kind'] != 'function' for c in matched),
                     'explicit_call_sites': sum(c['invocation'] == 'call' for c in matched),
                     'implicit_decorator_sites': sum(c['invocation'] == 'decorator' for c in matched),
                     'possible_sites': len(possible)})
    function_rows = [r for r in rows if r['kind'] in {'function', 'method'}]
    counts = {'api_entries': len(rows), 'kinds': dict(Counter(r['kind'] for r in rows)),
              'functions_methods': len(function_rows),
              'functions_methods_with_resolved_sites': sum(r['source_sites'] + r['test_sites'] > 0 for r in function_rows),
              'functions_methods_with_function_callers': sum(r['source_callers'] + r['test_callers'] > 0 for r in function_rows),
              'functions_methods_only_possible': sum((r['source_sites'] + r['test_sites']) == 0 and r['possible_sites'] > 0 for r in function_rows),
              'functions_methods_no_candidates': sum((r['source_sites'] + r['test_sites']) == 0 and not r['possible_sites'] for r in function_rows),
              'resolved_sites': len(calls), 'unresolved_sites': len(unresolved),
              'resolved_source_sites': sum(c['scope'] == 'source' for c in calls),
              'resolved_test_sites': sum(c['scope'] == 'tests' for c in calls),
              'resolved_function_method_sites': sum(r['source_sites'] + r['test_sites'] for r in function_rows),
              'resolved_class_construction_sites': sum(r['source_sites'] + r['test_sites'] for r in rows if r['kind'] == 'class'),
              'classes_with_resolved_sites': sum(r['kind'] == 'class' and r['source_sites'] + r['test_sites'] > 0 for r in rows),
              'experimental_entries': sum(r['experimental'] for r in rows)}
    limitations = [
        'Static source inventory, not a Sphinx build or proof of a complete runtime API.',
        'Only the selected API Reference toctree is included; Private API pages are excluded.',
        'Class construction is counted separately from function/method calls; properties and private/dunder members are excluded.',
        'Only documented own members are inventoried; inherited API aliases and dynamic extension hooks need review.',
        'Static name resolution assumes no runtime monkey-patching; instance dispatch and same-name matches are possible, not confirmed.',
        'Only source packages and configured tests are scanned, including nested functions and module/class bodies; not external applications or documentation examples.',
        'Bare decorators are implicit invocations and have separate counts; passing a callable as a value is not counted as a call.',
        'Possible-site counts overlap across API targets and must not be summed as unique call sites.',
        'No tests of the analyzed project, Docker, LLM, program slicing or prompt changes are performed.',
    ]
    dirty = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain'], capture_output=True, text=True, timeout=30, check=True).stdout
    hashes = {**model['hashes'], **scan['hashes'], **api['documentation_sha256']}
    conf = repo / project['docs_dir'] / 'conf.py'
    if conf.exists():
        hashes[conf.relative_to(repo).as_posix()] = hashlib.sha256(conf.read_bytes()).hexdigest()
    for name, expected in hashes.items():
        if hashlib.sha256((repo / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Input changed during analysis: ' + name)
    output.mkdir(parents=True)
    def dump(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    dump('public_api.json', api)
    for name, values in [('calls.jsonl', calls), ('unresolved.jsonl', api['unresolved'] + unresolved)]:
        (output / name).write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values), encoding='utf8')
    with (output / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['api'])
        writer.writeheader()
        writer.writerows(rows)
    report = ['# Public API and caller audit', '', f'Checkout: `{repo}`', f'Commit: `{commit}`', '',
              '## Counts', '', '```json', json.dumps(counts, indent=2), '```', '',
              '## Public callable inventory', '', '| API | Kind | Source callers | Test callers | Resolved sites | Possible sites |',
              '|---|---|---:|---:|---:|---:|']
    report += [f"| {r['api']} | {r['kind']} | {r['source_callers']} | {r['test_callers']} | {r['source_sites'] + r['test_sites']} | {r['possible_sites']} |" for r in rows]
    report += ['', '## Interpretation and limits', ''] + ['- ' + x for x in limitations]
    report += ['', '## Functions/methods without resolved sites', '',
               '| API | Possible sites (name only) |', '|---|---:|']
    report += [f"| {r['api']} | {r['possible_sites']} |" for r in function_rows if not r['source_sites'] + r['test_sites']]
    report += ['', '## Example resolved evidence', '', '| API | Caller | Call site | Expression |', '|---|---|---|---|']
    for item in function_rows:
        examples = [c for c in calls if c['target'] == item['api']][:2]
        report += [f"| {c['target']} | {c['caller']} | {c['location']} | `{c['expression'].replace('|', '/').replace(chr(10), ' ')}` |" for c in examples]
    report += ['', '## Documentation exclusions', ''] + ['- ' + p for p in api['excluded_pages']]
    report += ['', '## Review items', '', f"Documentation unresolved/non-callable entries: {len(api['unresolved'])}.",
               f"Private/property member exclusions: {len(api['excluded_members'])}.",
               'See unresolved.jsonl and public_api.json for exact evidence. No caller found does not mean unused.', '']
    (output / 'REPORT.md').write_text('\n'.join(report), encoding='utf8')
    dump('manifest.json', {'schema': 'public-api-callers-v1', 'complete': True,
                           'created_utc': datetime.now(timezone.utc).isoformat(), 'repo': str(repo),
                           'commit': commit, 'dirty_status': dirty, 'project': project, 'api_index': api_index,
                           'counts': counts, 'scan': {k: v for k, v in scan.items() if k != 'hashes'},
                           'input_sha256': hashes, 'limitations': limitations,
                           'implementation_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), Path(__file__).with_name('references.py')]},
                           'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}})
    return {'output': str(output), **counts}
