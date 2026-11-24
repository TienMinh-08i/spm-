"""Instrument a pinned copy, execute the linked suite and export both context formats."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import ast
import argparse,hashlib,io,json,os,shutil,subprocess,sys,tempfile,uuid,zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
PIN='60a803821466f3e537fa2729d65e1aaf7df9d2a4'
def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--reuse',type=Path)
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    out=args.out.resolve();out.mkdir()
    problems=[json.loads(s) for s in (args.data/'problems.jsonl').read_text(encoding='utf8').splitlines()]
    links=[json.loads(s) for s in (args.data/'tests.jsonl').read_text(encoding='utf8').splitlines()]
    tests=sorted({x['nodeid'] for x in links})
    if args.limit:tests=tests[:args.limit]
    (out/'selected.json').write_text(json.dumps(tests))
    repo=args.repo.resolve()
    work=args.reuse.resolve() if args.reuse else Path(tempfile.mkdtemp(prefix='spm_dynapyt24_'))
    (out/'work.json').write_text(json.dumps(str(work)))
    base=work/'baseline';inst=work/'instrumented'
    archive=subprocess.check_output(['git','-C',str(repo),'archive','--format=zip',PIN])
    if not args.reuse:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:z.extractall(base)
        shutil.copytree(base,inst)
    # selection.py is shipped alongside this runner.
    env=dict(os.environ,SLICE_TESTS=str(out/'selected.json'),SLICE_OUT=str(out),
             DDG_OUT=str(out),DDG_REPO=str(inst),PYTHONUTF8='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    def suite(folder,label,plugin=None):
        env['PYTHONPATH']=os.pathsep.join([str(HERE),str(folder/'src'),str(folder)])
        cmd=[sys.executable,'-m','pytest','-q','--assert=plain','-p','selection']
        if plugin:cmd+=['-p',plugin]
        cmd+=sorted({x.split('::')[0] for x in tests})
        with (out/(label+'.log')).open('w',encoding='utf8') as log:
            p=subprocess.run(cmd,cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
        if p.returncode:raise RuntimeError((out/(label+'.log')).read_text(encoding='utf8')[-5000:])
        passed=json.loads((out/'passed.json').read_text())
        if set(passed)!=set(tests):raise RuntimeError('selected test mismatch')
        (out/(label+'-passed.json')).write_text(json.dumps(passed))
    suite(base,'baseline')
    os.environ.update(DDG_REPO=str(inst),DDG_OUT=str(out))
    sys.path.insert(0,str(HERE))
    from dynapyt.instrument.instrument import instrument_file
    from dynapyt.utils.hooks import get_hooks_from_analysis
    hooks=get_hooks_from_analysis(['unified.Analysis'])
    files=list((inst/'src/marshmallow').rglob('*.py'))+list((inst/'tests').rglob('*.py'))
    for f in files:
        if '# DYNAPYT: DO NOT INSTRUMENT' in f.read_text(encoding='utf8')[:100]:continue
        if instrument_file(str(f.resolve()),hooks):raise RuntimeError(str(f))
    from compat import preserve_augmented_assignments
    from decorator_events import instrument_decorators
    for file in files:
        preserve_augmented_assignments(file)
        instrument_decorators(file)
    session=uuid.uuid4().hex
    config=Path(tempfile.gettempdir())/('dynapyt_analyses-'+session+'.txt')
    config.write_text('unified.Analysis')
    env['DYNAPYT_SESSION_ID']=session
    def shard(index):
        chosen=tests[index::3]
        directory=out/('shard-'+str(index));directory.mkdir()
        selector=directory/'selected.json';selector.write_text(json.dumps(chosen))
        local=dict(env,SLICE_TESTS=str(selector),SLICE_OUT=str(directory),
                   PYTHONPATH=os.pathsep.join([str(HERE),str(inst/'src'),str(inst)]))
        command=[sys.executable,'-m','pytest','-q','--assert=plain','-p','selection','-p','unified']
        command+=sorted({x.split('::')[0] for x in chosen})
        with (directory/'pytest.log').open('w',encoding='utf8') as log:
            result=subprocess.run(command,cwd=inst,env=local,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
        if result.returncode:raise RuntimeError((directory/'pytest.log').read_text(encoding='utf8')[-5000:])
        passed=json.loads((directory/'passed.json').read_text())
        if set(passed)!=set(chosen):raise RuntimeError('shard mismatch')
        return passed
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            passed=[t for part in pool.map(shard,range(3)) for t in part]
        if set(passed)!=set(tests) or len(passed)!=len(tests):raise RuntimeError('aggregate mismatch')
        (out/'dynapyt-passed.json').write_text(json.dumps(sorted(passed)))
    finally:config.unlink(missing_ok=True)
    export(out,base,problems)

def export(out,base,problems):
    @lru_cache(None)
    def source_lines(file):
        return file.read_text(encoding="utf8").splitlines()
    entries={}
    for file in (base/'src/marshmallow').rglob('*.py'):
        tree=ast.parse(file.read_text(encoding='utf8'))
        def walk(body,prefix):
            for n in body:
                if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    api='.'.join([*prefix,n.name])
                    entries[file.relative_to(base).as_posix(),n.lineno]=api
                elif isinstance(n,ast.ClassDef):
                    walk(n.body,[*prefix,n.name])
        module=list(file.relative_to(base/'src').with_suffix('').parts)
        walk(tree.body,module)
    rows={p['function_id']:dict(api=p['function_id'],tests=set(),calls=0,examples=[],gaps={}) for p in problems}
    for path in sorted((out/'traces').glob('*.json')):
        j=json.loads(path.read_text())
        nodes={n['id']:n for n in j['nodes']}
        prev={};nxt={}
        for a,b,label in j['edges']:
            assert a in nodes and b in nodes and a<b
            prev.setdefault(b,[]).append(a);nxt.setdefault(a,[]).append(b)
        def reach(seed,adj):
            todo=[seed];seen=set()
            while todo:
                n=todo.pop()
                if n not in seen:seen.add(n);todo+=adj.get(n,[])
            return seen
        returns_by_frame={}
        for n in nodes.values():
            if n['kind']=='return':returns_by_frame.setdefault(n['frame'],[]).append(n)
        criteria=list(j['criteria'])
        represented={(c['function'],c['result']) for c in criteria}
        for entry in nodes.values():
            if entry['kind']!='function_enter':continue
            api=entries.get((entry['file'],entry['line']))
            if api not in rows:continue
            returns=[n for n in returns_by_frame.get(entry['frame'],[]) if n['id']>entry['id']]
            if returns:
                result=returns[-1]['id']
                # Explicit-call and function-entry views may observe the same occurrence.
                if any(c['function']==api and c['call']<=entry['id'] and c['result']>=result for c in criteria):continue
                criteria.append(dict(function=api,call=entry['id'],result=result,mode='function_entry'))
        for c in criteria:
            if c['function'] not in rows:continue
            r=rows[c['function']];r['tests'].add(j['test']);r['calls']+=1
            call=nodes[c['call']]
            name=c['function'].rsplit('.',1)[-1]
            direct=call['file'].startswith('tests/')
            test_names=j['test'].split('::')[-1].split('[')[0]
            after=tuple((nodes[i]['file'],nodes[i]['line']) for i in nxt.get(c['result'],[])[:3])
            signature=(call['file'],call['line'],tuple(nodes[w]['line'] for w in c.get('mutations',())),after)
            quality=(int(test_names=='test_'+name),int(direct),int(bool(c.get('mutations'))),int(bool(after)))
            prior=next((e for e in r['examples'] if e['signature']==signature),None)
            if prior is None and len(r['examples'])>=3:prior=min(r['examples'],key=lambda e:e['quality'])
            if prior is not None:
                if prior['quality']>=quality:continue
                r['examples'].remove(prior)
            backwards=reach(c['call'],prev)|reach(c['result'],prev)
            forwards=reach(c['result'],nxt)-{c['result']}
            for writer in c.get('mutations',[]):
                backwards.update(reach(writer,prev))
                forwards.update(i for i in reach(writer,nxt) if i>c['result'])
            lines=[]
            seen_lines=set()
            for i in sorted(backwards|forwards):
                n=nodes[i];file=base/n['file']
                if not file.is_file():continue
                raw=source_lines(file)
                line=n['line']
                if line<1 or line>len(raw):raise RuntimeError('source mapping')
                code=raw[line-1]
                fragment=None
                if n['file'].startswith('tests/') and 'assert ' in code:
                    if i not in forwards or not n.get('consumed_writes'):continue
                    begin,col,end,last=n['span']
                    if begin!=end:continue
                    expr=ast.parse(code.encode('utf8')[col:last].decode('utf8'),mode='eval').body
                    if not isinstance(expr,ast.Compare) or len(expr.comparators)!=1:continue
                    operands=(expr.left,expr.comparators[0])
                    matched=[op for op,origins in zip(operands,n['consumed_writes'])
                             if isinstance(op,ast.Name) and set(origins).intersection(c.get('mutations',()))]
                    if len(matched)!=1:continue
                    fragment=[col+matched[0].col_offset,col+matched[0].end_col_offset]
                    code=matched[0].id
                section='caller use' if i in forwards else 'input origin' if i<c['call'] else 'API execution'
                location=n['file'],line,section
                if location not in seen_lines:
                    lines.append(dict(file=n['file'],line=line,code=code,node=i,section=section,fragment=fragment))
                    seen_lines.add(location)
            r['examples'].append(dict(test=j['test'],call=c['call'],result=c['result'],signature=signature,quality=quality,mutations=c.get('mutations',[]),lines=lines,evidence=path.relative_to(out).as_posix()))
            r['gaps']=j['gaps']
    report=['# DynaPyt 24 API','', '| API | Test có bằng chứng trả về | Lượt có bằng chứng trả về | Ví dụ |','|---|---:|---:|---:|']
    for folder in ('contexts','code_contexts'):(out/folder).mkdir(exist_ok=True)
    for r in rows.values():
        r['tests']=sorted(r['tests']);r['status']='PARTIAL' if r['examples'] else 'NO_SUCCESSFUL_CALL_EVIDENCE'
        report.append(f"| {r['api']} | {len(r['tests'])} | {r['calls']} | {len(r['examples'])} |")
        code=['# '+r['api']];desc=['# '+r['api'],'','Status: '+r['status']]
        for e in r['examples']:
            code+=['','## '+e['test']]
            for section in ('input origin','API execution','caller use'):
                code+=['','### '+section]
                selected=[n for n in e['lines'] if n['section']==section]
                if not selected:code+=['','No source line exported for this section.']
                for n in selected:code+=['',f"{n['file']}:{n['line']}",'','```python',n['code'],'```']
            desc+=['',e['test'],f"Trace: {e['evidence']}; call {e['call']}, result {e['result']}."]
            trace=json.loads((out/e['evidence']).read_text())
            names=[trace['nodes'][e['call']],trace['nodes'][e['result']]]
            desc += [f"Observed entry/call at {names[0]['file']}:{names[0]['line']}; successful result at {names[1]['file']}:{names[1]['line']}.",
                     f"Selected {len(e['lines'])} original source lines through recorded edges."]
        desc+=['','Trace limitations',json.dumps(r['gaps'],ensure_ascii=False)]
        (out/'code_contexts'/(r['api']+'.md')).write_text('\n'.join(code),encoding='utf8')
        (out/'contexts'/(r['api']+'.md')).write_text('\n'.join(desc),encoding='utf8')
    (out/'CONTEXTS.md').write_text('# Contexts for 24 API\n\n| API | Description | Code |\n|---|---|---|\n'+'\n'.join(
        f"| {r['api']} | [Description](contexts/{r['api']}.md) | [Code](code_contexts/{r['api']}.md) |" for r in rows.values())+'\n',encoding='utf8')
    (out/'api_contexts.json').write_text(json.dumps(list(rows.values()),indent=2),encoding='utf8')
    (out/'REPORT.md').write_text('\n'.join(report),encoding='utf8')
    print('API with context',sum(bool(r['examples']) for r in rows.values()),'/',len(rows))
if __name__=='__main__':run()
