"""Check occurrence-specific return and caller edges against original Python expressions."""
import ast
import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import sys

def span(n):
    return n.lineno,n.col_offset,n.end_lineno,n.end_col_offset

def audit(out):
    base=Path(json.loads((out/'work.json').read_text()))/'baseline'
    rows=json.loads((out/'api_contexts.json').read_text())
    stats={r['api']:Counter() for r in rows}
    evidence=defaultdict(dict)
    @lru_cache(None)
    def index(file):
        tree=ast.parse((base/file).read_text(encoding='utf8'))
        exprs={span(n):n for n in ast.walk(tree) if isinstance(n,(ast.expr,ast.stmt))}
        parents={id(child):n for n in ast.walk(tree) for child in ast.iter_child_nodes(n)}
        return exprs,parents
    definitions={}
    for api in stats:
        parts=api.split('.')
        for cut in range(len(parts)-1,0,-1):
            file='src/'+('/'.join(parts[:cut]))+'.py'
            if (base/file).exists():break
        body=ast.parse((base/file).read_text(encoding='utf8')).body
        for name in parts[cut:]:
            fn=next(n for n in body if getattr(n,'name',None)==name)
            body=fn.body
        definitions[file,fn.lineno]=api
    for file in (out/'traces').glob('*.json'):
        trace=json.loads(file.read_text());nodes=trace['nodes'];prev=defaultdict(list);following=defaultdict(list)
        for a,b,k in trace['edges']:
            if k!='control':
                prev[b].append(a);following[a].append(b)
        def record(api,key,ids):
            stats[api][key]+=1
            evidence[api].setdefault(key,{'test':trace['test'],'trace':'traces/'+file.name,'nodes':ids})
        frames={}
        for n in nodes:
            loc=n['file'],n['line']
            if n['kind']=='function_enter' and loc in definitions:
                frames[n['frame']]=definitions[loc];record(definitions[loc],'entered',[n['id']])
            api=frames.get(n['frame'])
            if not api or n['kind']!='return':continue
            expr=index(n['file'])[0].get(tuple(n['span']))
            if not isinstance(expr,ast.Return) or expr.value is None:continue
            origins=[a for a in prev[n['id']] if tuple(nodes[a]['span'])==span(expr.value) and nodes[a]['frame']==n['frame']]
            record(api,'return_expression_confirmed' if origins else 'return_expression_missing',origins+[n['id']])
        for c in trace['criteria']:
            api=c['function']
            if api not in stats:continue
            n=nodes[c['result']];exprs,parents=index(n['file']);expr=exprs.get(tuple(n['span']))
            parent=parents.get(id(expr))
            if isinstance(parent,(ast.Assign,ast.AnnAssign,ast.Return,ast.Compare,ast.BoolOp,ast.UnaryOp,ast.BinOp,ast.Attribute,ast.Subscript)):
                uses=[b for b in following[n['id']] if nodes[b]['file']==n['file'] and tuple(nodes[b]['span'])==span(parent)]
                record(api,'caller_expression_confirmed' if uses else 'caller_expression_missing',[n['id']]+uses)
            if n['kind']=='decorator_result':
                uses=[b for b in following[n['id']] if nodes[b]['kind'] in ('decorator_apply','call')]
                record(api,'decorator_value_reused' if uses else 'decorator_value_no_use',[n['id']]+uses[:1])
            if api.endswith('Context.get'):
                values=[a for a in prev[n['id']] if nodes[a]['kind']=='return']
                for ret in values:
                    for get in prev[ret]:
                        if nodes[get]['kind']!='call_result':continue
                        for setter in prev[get]:
                            if nodes[setter]['kind']=='call_result':
                                record(api,'context_set_to_get',[setter,get,ret,n['id']])
            for w in c.get('mutations',[]):
                uses=[b for b in following[w] if b>c['result'] and (nodes[b]['kind'] in ('read_item','read_attribute','call_result') or any(w in side for side in nodes[b].get('consumed_writes',[])))]
                record(api,'write_then_read' if uses else 'write_no_direct_read',[w]+uses[:1])
    for row in rows:
        for example in row['examples']:
            assert (out/example['evidence']).is_file()
    (out/'OCCURRENCES.json').write_text(json.dumps({'counts':stats,'examples':evidence},indent=2))
    md=['| API | Entries | Return expression matched / checked | Caller use matched / checked | Writes with later read / tracked writes | Decorator value reused / observed |','|---|---:|---:|---:|---:|---:|']
    for api,c in stats.items():
        def ratio(ok,missing):
            total=c[ok]+c[missing]
            return f'{c[ok]}/{total}' if total else '—'
        md.append(f"| [{api}](code_contexts/{api}.md) | {c['entered']} | {ratio('return_expression_confirmed','return_expression_missing')} | {ratio('caller_expression_confirmed','caller_expression_missing')} | {ratio('write_then_read','write_no_direct_read')} | {ratio('decorator_value_reused','decorator_value_no_use')} |")
    md+=['','## Test and nodes for each checked path','']
    for api,examples in evidence.items():
        md+=['','### '+api,'']
        for kind,example in examples.items():
            if kind=='entered':continue
            ids=' → '.join(map(str,example['nodes']))
            md.append(f"- {kind}: {example['test']} · [{ids}]({example['trace']})")
    (out/'OCCURRENCES.md').write_text('# Source-expression checks\n\n'+ '\n'.join(md)+'\n\nCounts are local edges checked against source expressions on the recorded execution. A dash means no applicable check. Writes cover modeled direct arguments. These checks do not establish complete transitive slices. See OCCURRENCES.json for test IDs and graph evidence.\n',encoding='utf8')
if __name__=='__main__':audit(Path(sys.argv[1]))
