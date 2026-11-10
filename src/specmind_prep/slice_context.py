"""Export validated occurrence slices as offline assertion context."""
from pathlib import Path
from specmind_prep.slicing import query_slice, load_trace
from specmind_eval.common.storage import read,write
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.fingerprint import file_hash,source_environment,digest


def prepare_context(spec,repo,output):
    spec=Path(spec).resolve();repo=Path(repo).resolve();output=separate_output(output,repo,spec)
    if output.exists():raise ValueError('Choose a new slice context output')
    packs={};provenance=[]
    for row in read(spec):
        trace=(spec.parent/row['trace']).resolve()
        _,_,manifest=load_trace(trace,repo)
        sliced=query_slice(trace,repo,row['event'],row['variable'],when=row.get('when','after'))
        if sliced['status']!='complete_supported':raise ValueError('Unsupported slice: '+row['function'])
        nodes=sliced['nodes']; criterion=row['event']
        graph,_,_=load_trace(trace,repo)
        event=next(n for n in graph if n['id']==criterion)
        target=row['analysis_target']
        if event.get('function',manifest.get('target')) != target:
            raise ValueError('Slice criterion belongs to a different target')
        key=row['function']
        if key in packs:raise ValueError('Duplicate context task')
        lines=['Observed backward dynamic slice (Agrawal-Horgan DDG).',
               'This is evidence from one test execution, not the full API contract. Do not copy test-specific constants into assertions.',
               'Source snippets below are context, not additional permitted assertion calls.',
               'Criterion: '+str(sliced['criterion']), 'Scope: '+str(sliced['scope'])]
        for caller in sorted({str(n['external_caller']) for n in nodes if n.get('external_caller')}):
            lines.append('Observed caller (body not necessarily sliced): '+caller)
        lines.extend(f"{s['file']}:{s['line']}: {s['source']}" for s in sliced['source_statements'])
        lines.extend(sliced['limitations']);text='\n'.join(lines)
        packs[key]={'analysis_target':target,'repository_context':text,
                    'metadata':{'status':'complete_supported','actual_chars':len(text),'direction':'backward'},
                    'criterion':sliced['criterion'],'trace_fingerprint':manifest['fingerprint']}
        provenance.append({'task':key,'trace':str(trace),'fingerprint':manifest['fingerprint']})
    write(output/'packs.json',packs)
    m={'schema':'prepared-slice-context-v1','complete':True,'source_environment':source_environment(repo),
       'provenance':provenance,'files':{'packs.json':file_hash(output/'packs.json')}}
    m['fingerprint']=digest(m);write(output/'manifest.json',m)
    return {'tasks':len(packs),'output':str(output)}
