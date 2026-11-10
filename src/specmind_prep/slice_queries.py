"""Occurrence queries on a validated DDG; no dependencies inferred from values."""

def closure(nodes, roots, *, direction):
    by = {n['id']: n for n in nodes}
    if len(by) != len(nodes) or any(type(i) is not int for i in by):
        raise ValueError('Unique integer event IDs required')
    adjacent = {i: set() for i in by}
    for node in nodes:
        for parent in node['depends_on']:
            if parent not in by or parent >= node['id']:
                raise ValueError('DDG requires earlier dependency occurrences')
            adjacent[node['id'] if direction == 'backward' else parent].add(parent if direction == 'backward' else node['id'])
    if direction not in {'backward','forward'}:
        raise ValueError('Unknown direction')
    found, pending = set(), list(roots)
    if not set(pending) <= by.keys():
        raise ValueError('Unknown criterion occurrence')
    while pending:
        ident = pending.pop()
        if ident not in found:
            found.add(ident)
            pending.extend(adjacent[ident])
    return [by[i] for i in sorted(found)]


def invocation_contexts(nodes, target):
    """Auto-select actual-value producers and returned-value uses for EVERY call.

    Input roots are argument evaluation occurrences (not a variable guessed by
    name). Output roots are call_result bridges. Return roots never share state
    across invocation frames. Validated call-model DDG required from load_trace.
    """
    by = {n['id']: n for n in nodes}
    contexts = []
    for call in nodes:
        if call['kind'] != 'invocation' or call['callee'] != target:
            continue
        results = [n for n in nodes if n['kind']=='call_result' and n.get('links',{}).get('invocation')==call['id']]
        if len(results) != 1:
            raise ValueError('Expected exactly one return bridge for completed invocation')
        arguments = []
        for name, root in call['arguments'].items():
            if root not in by or by[root]['kind']!='argument' or by[root]['frame']!=call['frame']:
                raise ValueError('Argument criterion differs from caller boundary')
            arguments.append({'parameter': name, 'root': root, 'when':'after_argument_evaluation_before_invocation',
                              'nodes': closure(nodes,[root,*call.get('heap_argument_roots',{}).get(name,[])],direction='backward')})
        result = results[0]
        forward = closure(nodes,[result['id']],direction='forward')
        mutations = [{'location':m['location'],'root':m['root'],'deleted':m['deleted'],
                      'nodes':[n for n in closure(nodes,[m['root']],direction='forward') if n['id']>result['id']]}
                     for m in result.get('heap_mutations',[])]
        contexts.append({'invocation':call['id'], 'frame':call['frame'], 'site':call['site'],
                         'inputs':arguments, 'output':{'root':result['id'],'nodes':forward},
                         'mutations':mutations if 'heap_mutations' in result else None,
                         'scope':'selected caller/target exact dict/list heap DDG' if 'heap_mutations' in result else 'selected caller/target primitive DDG; mutation criteria not supported'})
    if not contexts:
        raise ValueError('No observed target invocation')
    return contexts


def query_invocations(trace, repo):
    from specmind_prep.slicing import load_trace, CALL_MODEL, HEAP_CALL_MODEL
    nodes, sites, manifest = load_trace(trace, repo)
    if manifest['model'] not in {CALL_MODEL,HEAP_CALL_MODEL} or manifest['trace_status'] != 'complete_supported':
        raise ValueError('Automatic criteria require a complete supported caller/target DDG')
    return {'schema':'automatic-call-context-v1', 'trace_fingerprint':manifest['fingerprint'],
            'target':manifest['function'], 'caller':manifest['caller'],
            'calls':invocation_contexts(nodes,manifest['function'])}
