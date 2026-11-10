from specmind_prep.slice_reduced import reduce_ddg
from specmind_prep.slice_queries import closure


def test_cycle_reduction_preserves_every_backward_source_slice():
    nodes=[]
    def add(site,parents):nodes.append({'id':len(nodes)+1,'site':site,'depends_on':parents})
    add('input',[]);add('predicate',[1]);add('increment',[2]);add('predicate',[1,3]);add('increment',[4]);add('predicate',[1,5])
    sites={x:{'statement':x} for x in ['input','predicate','increment']}
    reduced=reduce_ddg(nodes,sites)
    assert reduced['reduced_nodes']<len(nodes)
    for node in nodes:
        rid=reduced['occurrence_to_reduced'][str(node['id'])]
        expected={n['site'] for n in closure(nodes,[node['id']],direction='backward')}
        assert set(reduced['nodes'][rid-1]['reachable_statements'])==expected


def test_distinct_branch_occurrences_not_merged_by_source_line():
    nodes=[{'id':1,'site':'a','depends_on':[]},{'id':2,'site':'b','depends_on':[]},
           {'id':3,'site':'use','depends_on':[1]},{'id':4,'site':'use','depends_on':[2]}]
    result=reduce_ddg(nodes,{s:{'statement':s} for s in ['a','b','use']})
    assert result['occurrence_to_reduced']['3']!=result['occurrence_to_reduced']['4']


def test_random_dags_match_independent_backward_traversal():
    import random
    rng=random.Random(8)
    sites={f's{i}':{'statement':f's{i}'} for i in range(8)}
    for _ in range(30):
        nodes=[]
        for i in range(1,35):
            parents=rng.sample(range(1,i),k=min(i-1,rng.randrange(4)))
            nodes.append({'id':i,'site':f's{rng.randrange(8)}','depends_on':parents})
        reduced=reduce_ddg(nodes,sites)
        for node in nodes:
            rid=reduced['occurrence_to_reduced'][str(node['id'])]
            expected={n['site'] for n in closure(nodes,[node['id']],direction='backward')}
            assert set(reduced['nodes'][rid-1]['reachable_statements'])==expected
