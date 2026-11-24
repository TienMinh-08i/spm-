"""Find a witnessed path from a pinned return expression to an observed caller use."""
from collections import defaultdict
from pathlib import Path
import json
import sys

# Return lines in the pinned Marshmallow source, reviewed independently of the trace.
RETURNS={
    'post_dump':{172}, 'post_load':{218}, 'pre_dump':{149}, 'pre_load':{193},
    'set_hook':{240,254}, 'validates':{94}, 'validates_schema':{124}, 'get':{72,73},
    'load':{735}, 'dump':{579}, 'dumps':{595}, 'from_dict':{482}, 'loads':{772},
    'validate':{838,839}, 'callable_or_raise':{158}, 'get_attribute':{506},
    'get_value':{109,110}, 'is_collection':{30}, 'is_generator':{15},
    'is_iterable_but_not_string':{20}, 'is_sequence_but_not_string':{25},
    'pluck':{91}, 'set_value':set(), 'timedelta_to_microseconds':{166},
}
def main(out):
    apis={r['api'] for r in json.loads((out/'api_contexts.json').read_text(encoding='utf8'))}
    witnesses={}
    for file in sorted((out/'traces').glob('*.json')):
        j=json.loads(file.read_bytes());nodes=j['nodes'];prev=defaultdict(list);nxt=defaultdict(list)
        for a,b,k in j['edges']:
            if k!='control':prev[b].append(a);nxt[a].append(b)
        for c in j['criteria']:
            api=c['function']
            if api not in apis or api in witnesses:continue
            name=api.rsplit('.',1)[-1];result=c['result'];path=None
            if name=='set_value':
                for w in c.get('mutations',[]):
                    uses=[u for u in nxt[w] if u>result and any(w in side for side in nodes[u].get('consumed_writes',[]))]
                    if uses and prev[w]:path=[prev[w][-1],w,uses[0]];break
            else:
                returns=[r for r in prev[result] if nodes[r]['kind']=='return' and nodes[r]['function']==name and nodes[r]['line'] in RETURNS[name]]
                for r in returns:
                    expr=[e for e in prev[r] if nodes[e]['frame']==nodes[r]['frame']]
                    uses=[u for u in nxt[result] if nodes[u]['kind'] not in ('function_enter','parameter')]
                    if expr and uses:path=[expr[-1],r,result,uses[0]];break
            if path:
                witnesses[api]=dict(test=j['test'],trace='traces/'+file.name,nodes=[nodes[i] for i in path])
        if len(witnesses)==len(apis):break
    report=['# Đường phụ thuộc đối chứng theo API','','Mỗi dòng kiểm tra một lần chạy. Với API trả kết quả, đường đi gồm biểu thức trong hàm → return đúng dòng source → kết quả tại caller → thao tác dùng kết quả. Với set_value, đường đi gồm vế phải → phép ghi → phép so sánh đọc lại vị trí đó. Các kiểm tra này chỉ bao phủ đoạn đường đã ghi.','','| API | Test | Đường đi trong trace |','|---|---|---|']
    for api in sorted(apis):
        w=witnesses.get(api)
        if not w:
            report.append(f'| {api} | Chưa tìm đủ đường theo tiêu chí trên | |')
            continue
        ids=' → '.join(str(n['id']) for n in w['nodes'])
        report.append(f"| [{api}](code_contexts/{api}.md) | {w['test']} | [{ids}]({w['trace']}) |")
    (out/'API_WITNESSES.json').write_text(json.dumps(witnesses,indent=2),encoding='utf8')
    (out/'API_WITNESSES.md').write_text('\n'.join(report)+'\n',encoding='utf8')
    print(f'Local path verified for {len(witnesses)}/{len(apis)} APIs; missing:',sorted(apis-witnesses.keys()))
if __name__=='__main__':main(Path(sys.argv[1]))
