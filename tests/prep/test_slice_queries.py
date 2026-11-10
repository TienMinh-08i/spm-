"""Independent occurrence oracles for automatic call criteria and both directions."""
import pytest
from specmind_prep.slice_queries import closure, invocation_contexts


def graph():
    return [
        {'id':1,'kind':'assignment','frame':1,'depends_on':[]},
        {'id':2,'kind':'argument','frame':1,'depends_on':[1]},
        {'id':3,'kind':'invocation','callee':'target','frame':1,'arguments':{'x':2},'site':'call','depends_on':[]},
        {'id':4,'kind':'return','frame':2,'depends_on':[2,3]},
        {'id':5,'kind':'call_result','frame':1,'links':{'invocation':3,'return':4},'depends_on':[3,4]},
        {'id':6,'kind':'assignment','frame':1,'depends_on':[5]},
        {'id':7,'kind':'assignment','frame':1,'depends_on':[]},
        {'id':8,'kind':'expression','frame':1,'depends_on':[7]},
    ]


def test_auto_arguments_and_output_uses_exclude_overwrite():
    c=invocation_contexts(graph(),'target')[0]
    assert c['inputs'][0]['parameter']=='x'
    assert [n['id'] for n in c['inputs'][0]['nodes']]==[1,2]
    assert [n['id'] for n in c['output']['nodes']]==[5,6]
    assert c['mutations'] is None


def test_all_invocations_keep_separate_occurrence_criteria():
    nodes=graph()
    second=[]
    for n in graph():
        n=dict(n,id=n['id']+8,depends_on=[x+8 for x in n['depends_on']])
        if 'arguments' in n:n['arguments']={k:v+8 for k,v in n['arguments'].items()}
        if 'links' in n:n['links']={k:v+8 for k,v in n['links'].items()}
        second.append(n)
    calls=invocation_contexts(nodes+second,'target')
    assert [c['invocation'] for c in calls]==[3,11]
    assert [n['id'] for n in calls[1]['inputs'][0]['nodes']]==[9,10]


@pytest.mark.parametrize('nodes,roots',[([{'id':1,'depends_on':[2]}],[1]),([{'id':1,'depends_on':[]}],[2])])
def test_invalid_graph_or_root_rejected(nodes,roots):
    with pytest.raises(ValueError):closure(nodes,roots,direction='forward')
