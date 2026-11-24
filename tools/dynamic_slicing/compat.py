"""Preserve augmented assignments without DynaPyt's eager preview operation."""
import ast
from pathlib import Path

def preserve_augmented_assignments(file):
    path=Path(file)
    data=path.read_bytes()
    lines=data.splitlines(keepends=True);offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    def pos(n):return offsets[n.lineno-1]+n.col_offset,offsets[n.end_lineno-1]+n.end_col_offset
    replacements=[]
    for n in ast.walk(ast.parse(data)):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='_rt' and n.func.attr=='_aug_assign_':
            if len(n.args)!=5:raise ValueError('unrecognized augmented assignment wrapper')
            start,end=pos(n);a,b=pos(n.args[-1])
            replacements.append((start,end,data[a:b]))
    for start,end,value in sorted(replacements,reverse=True):data=data[:start]+value+data[end:]
    compile(data,str(path),'exec')
    path.write_bytes(data)
    return len(replacements)
