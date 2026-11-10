"""Offline Reduced DDG using Approach 4 immediate-dependency/cycle rules.

Full DDG remains the occurrence oracle and the source for forward queries.
Occurrence mapping is retained for audit; this is not a streaming memory bound.
"""


def reduce_ddg(nodes,sites):
    reduced=[];mapping={};signatures={};oracle={}
    for n in nodes:
        ident=n['id']
        if type(ident) is not int or ident!=len(mapping)+1:
            raise ValueError('Consecutive occurrence IDs required')
        if any(p not in mapping for p in n['depends_on']):
            raise ValueError('Dependencies must precede occurrence')
        statement=sites[n['site']]['statement']
        parents=frozenset(mapping[p] for p in n['depends_on'])
        reach={statement}
        expected={statement}
        for p in n['depends_on']:expected.update(oracle[p])
        for p in parents:reach.update(reduced[p-1]['reachable_statements'])
        key=(statement,parents)
        choice=signatures.get(key)
        if choice is None:
            # Approach 4 cycle merge: new statement already occurs in a direct
            # descendant and all transitive dependencies are contained there.
            choice=next((p for p in sorted(parents) if statement in reduced[p-1]['reachable_statements']
                         and reach<=set(reduced[p-1]['reachable_statements'])),None)
        if choice is None:
            choice=len(reduced)+1
            reduced.append({'id':choice,'statements':[statement],'depends_on':sorted(parents),
                            'reachable_statements':sorted(reach)})
        elif reach!=set(reduced[choice-1]['reachable_statements']):
            raise ValueError('Reduction changed reachable source statements')
        mapping[ident]=choice
        signatures[key]=choice
        oracle[ident]=expected
        if expected!=set(reduced[choice-1]['reachable_statements']):
            raise ValueError('RDDG differs from full-DDG backward oracle')
    return {'schema':'reduced-dynamic-dependence-v1','method':'Approach 4 immediate-dependency and cycle-subset reduction',
            'nodes':reduced,'occurrence_to_reduced':{str(k):v for k,v in mapping.items()},
            'verified_occurrences':len(mapping),'full_nodes':len(nodes),'reduced_nodes':len(reduced),
            'forward_source':'full occurrence DDG','storage':'offline graph plus occurrence audit map'}
