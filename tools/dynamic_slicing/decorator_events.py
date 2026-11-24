"""Record Python's implicit decorator application at its original source span."""
import ast
import re
from pathlib import Path

def instrument_decorators(path):
    path=Path(path)
    original=Path(str(path)+'.orig')
    if not original.exists():return
    data=path.read_bytes()
    # DynaPyt embeds an absolute source path. Resolve the sibling .orig in copied workspaces.
    data,count=re.subn(rb'(?m)^_dynapyt_ast_ = r".*?" [+] "[.]orig"',b'_dynapyt_ast_ = __file__ + ".orig"',data,count=1)
    if count:path.write_bytes(data)
    if b'__spm_decorators_runtime__' in data:return
    old=ast.parse(original.read_bytes())
    new=ast.parse(data)
    def definitions(tree):
        return sorted((n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.decorator_list),key=lambda n:n.lineno)
    before,after=definitions(old),definitions(new)
    if [(n.name,len(n.decorator_list)) for n in before]!=[(n.name,len(n.decorator_list)) for n in after]:
        raise ValueError(f'Decorator mapping changed in {path}')
    offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    def interval(n):return offsets[n.lineno-1]+n.col_offset,offsets[n.end_lineno-1]+n.end_col_offset
    changes=[]
    for a,b in zip(before,after):
        for src,dst in zip(a.decorator_list,b.decorator_list):
            if not isinstance(src,(ast.Name,ast.Call)):continue
            start,end=interval(dst)
            span=(src.lineno,src.col_offset,src.end_lineno,src.end_col_offset)
            header=f'_dynapyt_ast_, {span!r}, '.encode()
            expr=data[start:end]
            if isinstance(src,ast.Call) and isinstance(dst,ast.Call) and isinstance(src.func,ast.Name) and isinstance(dst.func,ast.Name):
                f0,f1=interval(dst.func)
                expr=b'__spm_decorators_runtime__.factory('+header+data[f0:f1]+b')'+data[f1:end]
            changes.append((start,end,b'__spm_decorators_runtime__.application('+header+expr+b')'))
    if not changes:return
    for start,end,replacement in reversed(sorted(changes)):data=data[:start]+replacement+data[end:]
    anchor=data.index(b'from dynapyt.runtime import RuntimeEngine')
    data=data[:anchor]+b'import decorator_events as __spm_decorators_runtime__\n'+data[anchor:]
    compile(data,str(path),'exec')
    path.write_bytes(data)

def decorator_wrapper(file,span,fn,apply):
    import unified
    a=unified.ANALYSIS
    if a is None or not a.test:return fn
    def invoke(*args,**kwargs):
        parents=[]
        if apply:
            origin=a.decorator_values.get(id(fn))
            if origin is not None and origin[0] is fn:parents.append(origin[1])
        caller=unified.application_frame()
        name=(getattr(fn,'__module__','') or '')+'.'+getattr(fn,'__qualname__',type(fn).__name__)
        node=a.node('decorator_apply' if apply else 'decorator_factory',file,span,parents,function=name)
        state=dict(file=file,iid=span,caller=caller,node=node,name=name,function=fn,returned=None,reads=[],bindings={},argument_ids={id(v) for v in (*args,*kwargs.values())},writes={})
        a.calls.append(state)
        try:result=fn(*args,**kwargs)
        finally:
            for i in range(len(a.calls)-1,-1,-1):
                if a.calls[i] is state:
                    del a.calls[i:]
                    break
        outcome=a.node('decorator_result',file,span,[state['returned'],node],function=name)
        a.decorator_values[id(result)]=(result,outcome)
        a.criteria.append(dict(function=name,call=node,result=outcome,mutations=list(state['writes'].values())))
        return result
    return invoke

def application(file,span,fn):
    return decorator_wrapper(file,span,fn,True)

def factory(file,span,fn):
    return decorator_wrapper(file,span,fn,False)
