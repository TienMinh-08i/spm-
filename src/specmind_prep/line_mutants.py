"""Line-level protocol adapted from user-supplied generate_leetcode_mutants.py.

The upstream prompts request about five alternatives per executable line.
Only one line is changed in each candidate. Existing corpus validation is reused.
"""
import ast
import json
import re

# Prompt constants below are populated from the supplied file without importing it.
SYSTEM_PROMPT = """\
You are an expert Python programmer and software testing specialist.

You will be given a Python library function and its API documentation.
Use the supplied implementation as the mutation baseline. Passing tests does not prove correctness for the whole API.

Your task: identify meaningful executable lines in the solution, and for each
such line, propose about 5 buggy mutant alternatives. The mutants should have
capability to detect loose, trivial specifications: they should introduce bugs
that weak specifications may fail to catch.

Output a single ```json block — one JSON array. Each item must have:
  - "original_content": verbatim original line (with EXACT leading whitespace)
  - "dedup_key": occurrence index of that exact line content in the solution, starting from 0.
    Use this to differentiate duplicate source lines with the same exact content:
    first occurrence = 0, second occurrence = 1, third occurrence = 2, etc.
  - "mutant": list of about 5 distinct mutant strings (with the SAME exact leading whitespace)

Example output format:
```json
[
  {
    "original_content": "        c = a + b",
    "dedup_key": 0,
    "mutant": [
      "        c = a - b",
      "        c = a * b"
    ]
  }
]
```

Additional rules:
  - Preserve ALL identifiers (variable names, function names) — do NOT rename
  - Every mutant must be syntactically valid Python
  - Every mutant must introduce a different semantic bug
  - Skip: comment lines, blank lines, `class`/`def`/`import`/`from`/decorator lines
  - Skip lines with no meaningful semantic variation (e.g. bare `pass`, `return` with trivial expr)
  - Cover lines from the BEGINNING of the function through the whole function body
  - This is a SHARED mutant pool for the whole task, so do not specialize mutants to one assertion/checkpoint

Respond ONLY with the ```json block — no prose, no explanation.
"""
USER_TEMPLATE = """\
### Problem
{problem}

### Correct solution:
```python
{completion}
```

Propose about 5 mutant alternatives for each meaningful executable code line in
the function body.

Output a single ```json block: an array of objects. Each object has
`original_content`, `dedup_key`, and `mutant`.

`dedup_key` is required when the same source line content appears multiple
times. Count occurrences of the exact `original_content` line from top to
bottom, starting at 0.

Example:
```json
[
  {{
    "original_content": "        c = a + b",
    "dedup_key": 0,
    "mutant": [
      "        c = a - b",
      "        c = a * b"
    ]
  }}
]
```

For longer functions, generate mutants across all meaningful executable lines
rather than concentrating all mutants on only one or two lines.
"""
REFERENCE_SHA256 = '33e922f0e730dfd28cc90e1595937bb2d40c229c1df10ff4f1e29da25dedb33d'


def messages(target):
    description = target.get('document') or target.get('docstring') or ''
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': USER_TEMPLATE.format(problem=description, completion=target['source'])}]


def eligible_lines(source):
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError('Expected exactly one function baseline')
    root = tree.body[0]
    blocked = set(range(1, root.body[0].lineno))
    for node in ast.walk(root):
        if node is not root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            blocked.update(range(min([node.lineno]+[d.lineno for d in node.decorator_list]), node.end_lineno+1))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            blocked.update(range(node.lineno, node.end_lineno+1))
    skip = re.compile(r'^\s*(#|$|class\s|(?:async\s+)?def\s|import\s|from\s|@|pass\s*$)')
    return {i for i,line in enumerate(source.splitlines(),1) if i not in blocked and not skip.match(line)}


def filter_response(source, content):
    # Accept the required fenced JSON list, plus a bare list as a transport fallback.
    match = re.search(r'```json\s*\n?(.*?)```', content, re.S | re.I)
    payload = json.loads(match.group(1).strip() if match else content.strip())
    if not isinstance(payload, list):
        raise ValueError('Line mutation response must be a JSON array')
    lines = source.splitlines()
    eligible = eligible_lines(source)
    occurrences = {}
    for i,line in enumerate(lines,1):
        occurrences.setdefault(line, []).append(i)
    proposals, rejected, covered = [], [], set()
    for ri,record in enumerate(payload):
        if not isinstance(record, dict) or not isinstance(record.get('mutant'), list):
            raise ValueError(f'Record {ri} must contain a mutant list')
        original, occurrence = record.get('original_content'), record.get('dedup_key')
        positions = occurrences.get(original, []) if isinstance(original,str) else []
        reason = None
        if type(occurrence) is not int or occurrence < 0 or occurrence >= len(positions):
            reason = 'Original line or occurrence index does not match source'
        else:
            line = positions[occurrence]
            if line not in eligible:
                reason = 'Line outside eligible function body (signature/docstring/nested definition/skip line)'
        for vi,variant in enumerate(record['mutant']):
            identity = f'{ri}:{vi}'
            error = reason
            if not isinstance(variant,str) or not variant.strip() or '\n' in variant or '\r' in variant:
                error = error or 'Variant must be one nonempty physical line'
            if error is None:
                indent = re.match(r'^\s*',original).group()
                if re.match(r'^\s*',variant).group() != indent:
                    error = 'Leading whitespace differs from original line'
            if error:
                rejected.append({'id':identity,'reason':error,'record_index':ri,'variant_index':vi})
                continue
            changed = list(lines)
            changed[line-1] = variant
            mutated = '\n'.join(changed)+ ('\n' if source.endswith('\n') else '')
            covered.add(line)
            proposals.append({'function_source':mutated,'reason':f'Line {line}: {original.strip()} -> {variant.strip()}',
                              'line_record':{'original_content':original,'dedup_key':occurrence,
                                             'replacement':variant,'source_line':line,
                                             'record_index':ri,'variant_index':vi,'record_id':identity}})
    # Imported lazily to avoid a module cycle; this parser never calls an LLM.
    from specmind_prep.llm_mutants import filter_response as validate_functions
    checked = validate_functions(source,json.dumps({'mutants':proposals}),len(proposals))
    for item in checked['mutants']:
        meta = proposals[int(item['id'])]['line_record']
        item.update(id=meta['record_id'],line_record=meta,mutation_method='line')
    for item in checked['skipped']:
        meta = proposals[int(item['id'])]['line_record']
        item.update(id=meta['record_id'],line_record=meta)
    return {'records':payload,'discovered':sum(len(r['mutant']) for r in payload),
            'mutants':checked['mutants'],'skipped':rejected+checked['skipped'],
            'coverage':{'eligible_lines':sorted(eligible),'proposed_lines':sorted(covered),
                        'accepted_lines':sorted({m['line_record']['source_line'] for m in checked['mutants']}),
                        'uncovered_lines':sorted(eligible-covered)}}
