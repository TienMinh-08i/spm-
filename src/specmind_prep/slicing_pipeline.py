"""Automatic offline caller-slicing workflow; no LLM/mutation generation."""
from pathlib import Path
from collections import Counter
import traceback
from specmind_eval.common.storage import read,write
from specmind_eval.common.fingerprint import digest,file_hash,source_environment,implementation_digest
from specmind_eval.inputs.prepared import load_prepared_dataset
from specmind_eval.inputs.dataset import pytest_nodeid
from specmind_prep.project import validate_project
from specmind_prep.source_index import source_index
from specmind_prep.references import references
from specmind_prep.slicing import trace_function,load_trace,_call_specs
from specmind_prep.injected.slice_instrumentation import analyze,Unsupported
from specmind_prep.slice_queries import query_invocations
from specmind_prep.slice_reduced import reduce_ddg
from specmind_prep.caller_context import render_automatic_context,MODEL


def prepare(dataset,repo,project,output,*,preflight_only=False,max_tests=1,timeout=60):
    repo,output=Path(repo).resolve(),Path(output).resolve()
    project=validate_project(read(project) if isinstance(project,(str,Path)) else project,repo)
    if output.exists() or output==repo or repo in output.parents:
        raise ValueError('Choose a new output outside checkout')
    if type(max_tests) is not int or max_tests<1:raise ValueError('max_tests must be positive')
    problems,tests,dm=load_prepared_dataset(dataset,repo)
    index,_=source_index(repo,project)
    output.mkdir(parents=True)
    observed = {}
    if not preflight_only:
        from specmind_prep.slice_discovery import discover
        discovery = discover(dataset, repo, project, output/'discovery', timeout=max(timeout, 180),
                             tests_per_task=max_tests, _prepared=(problems, tests, dm))
        if not discovery['execution_complete']:
            raise ValueError('Caller discovery did not finish successfully; inspect discovery/pytest.log')
        observed = {row['task']: row for row in read(output/'discovery/coverage.json')}
    by_location = {}
    for identity, record in index.items():
        by_location.setdefault((record['repo_path'], record.get('code_line', record['line'])), []).append(identity)
    packs,coverage={},[]
    for number,p in enumerate(problems):
        key,target=p['function_id'],p['problem_id']
        row={'task':key,'target':target,'status':'unsupported','attempts':[]}
        coverage.append(row)
        record=index[target]
        try:
            if record.get('async'):
                raise Unsupported('Methods/async functions not supported by selected heap model')
            _call_specs(repo, {target: record}, target, heap=True)
        except Unsupported as exc:
            row['reason']='Target: '+str(exc);continue
        caller_tests = {}
        if preflight_only:
            candidates=sorted({r['caller'] for r in references(repo,index,key,context_lines=0)['callers'] if r['caller']!=target})
        else:
            for invocation in observed.get(key, {}).get('calls', []):
                if invocation['role'] != 'target': continue
                # The nearest modeled source frame outside API/target is the
                # caller. Do not resolve a method by its terminal name.
                for parent in invocation['parents']:
                    names = by_location.get((parent['file'], parent['definition_line']), [])
                    matches = [name for name in names if name not in {key,target}
                               and name == index[name]['module'] + '.' + parent['qualname']]
                    if len(matches) == 1:
                        caller_tests.setdefault(matches[0], set()).add(invocation['test'])
                        break
            candidates = sorted(caller_tests)
            row['caller_selection'] = 'observed runtime file/definition/qualified-name binding'
            row['observed_target_calls'] = sum(c['role']=='target' for c in observed.get(key,{}).get('calls',[]))
        if not candidates:
            row['reason']='No resolved package caller in source index; test/nested/dynamic caller unsupported'
            continue
        supported=[]
        for caller in candidates:
            try:_call_specs(repo,{caller:index[caller],target:record, key:index[key]},caller,heap=True)
            except Unsupported as exc:
                row['attempts'].append({'caller':caller,'status':'unsupported','reason':str(exc)})
            else:supported.append(caller)
        if not supported:
            row['reason']='All resolved callers blocked at AST preflight';continue
        if preflight_only:
            row.update(status='preflight_supported',reason='Runtime verification not requested',callers=supported)
            continue
        selected_tests=sorted({pytest_nodeid(t) for t in tests[key]})[:max_tests]
        row['test_policy']={'selection':'observed caller/test pairs from the selected prepared-test sample','max_tests':max_tests,
                            'total_prepared_tests':len(tests[key])}
        finished=False
        for ci,caller in enumerate(supported):
            for ti,test in enumerate(sorted(caller_tests.get(caller, selected_tests))):
                directory=output/'traces'/f'task_{number:03d}_caller_{ci:03d}_test_{ti:03d}'
                try:
                    result=trace_function(repo,project,test,target,directory,caller=caller,helpers=[key] if key not in {caller,target} else [],heap=True,timeout=timeout)
                except Exception as exc:
                    directory.mkdir(parents=True,exist_ok=True)
                    (directory/'ERROR.txt').write_text(traceback.format_exc(),encoding='utf8')
                    row['attempts'].append({'caller':caller,'test':test,'status':'error','reason':type(exc).__name__+': '+str(exc),
                                            'trace':str(directory.relative_to(output))})
                    continue
                row['attempts'].append({'caller':caller,'test':test,'trace':str(directory.relative_to(output)),
                                        'status':result['status'],'issues':result['issues']})
                if result['status']!='complete_supported':continue
                nodes,sites,manifest=load_trace(directory,repo)
                reduced=reduce_ddg(nodes,sites)
                write(directory/'reduced.review.json',reduced)
                automatic=query_invocations(directory,repo)
                write(directory/'automatic.review.json',automatic)
                criterion,text=render_automatic_context(automatic,sites,heap_supported=True)
                packs[key]={'analysis_target':target,'repository_context':text,'criterion':criterion['criterion'],
                            'metadata':{'role':'caller_slice','model':MODEL,'status':'complete_supported',
                                        'direction':'backward_inputs_forward_output','actual_chars':len(text),
                                        'trace_model':manifest['model'],'heap':True},
                            'trace_fingerprint':manifest['fingerprint']}
                row.update(status='complete_supported',caller=caller,test=test,
                           trace_fingerprint=manifest['fingerprint'],full_nodes=len(nodes),reduced_nodes=reduced['reduced_nodes'],
                           verified_rddg_occurrences=reduced['verified_occurrences'],
                           artifact_hashes={f:file_hash(directory/f) for f in ('reduced.review.json','automatic.review.json')},
                           selection='first supported caller/test pair in deterministic order; all its target occurrences')
                finished=True;break
            if finished:break
        if not finished:row['reason']='No supported verified execution among selected candidate/test pairs'
    write(output/'packs.json',packs);write(output/'coverage.json',coverage)
    summary={'tasks':len(problems),'supported':len(packs),'counts':dict(Counter(r['status'] for r in coverage)),
             'preflight_only':preflight_only,'llm_calls':0,'pending_functions':len(problems)-len(packs)}
    write(output/'summary.json',summary)
    manifest={'schema':'prepared-slice-context-v1','complete':len(packs)==len(problems) and bool(problems),
              'model':MODEL,'dataset_fingerprint':dm['fingerprint'],'source_environment':source_environment(repo),
              'implementation':implementation_digest('specmind_prep.slicing_pipeline','specmind_prep.slicing'),
              'provenance':coverage,'files':{'packs.json':file_hash(output/'packs.json')}}
    manifest['fingerprint']=digest(manifest);write(output/'manifest.json',manifest)
    lines=['# Automatic caller slicing coverage','',str(summary),'',
           '| Task | Status | Reason |','|---|---|---|']
    for r in coverage:lines.append(f"| {r['task']} | {r['status']} | {r.get('reason','Verified execution; see coverage.json')} |")
    lines+=['','Input origins: backward actual values before invocation. Output/mutation uses: forward after call.',
            'RDDG is offline Approach 4 reduction, checked against full-DDG backward source closure at every occurrence.',
            'Forward queries retain full occurrence DDG; occurrence map is retained, so no streaming memory bound is claimed.',
            'Unsupported tasks are not replaced with heuristic context. Incomplete manifest cannot be used as a complete C/D corpus.']
    (output/'REPORT.md').write_text('\n'.join(lines),encoding='utf8')
    return summary
