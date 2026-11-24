"""DynaPyt callbacks and committed CPython stores from one execution."""
import ast
import dis
import contextvars
import hashlib
import inspect
import json
import os
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
import pytest
from dynapyt.analyses.BaseAnalysis import BaseAnalysis
from dynapyt.instrument.IIDs import IIDs

ROOT=Path(os.environ['DDG_REPO']).resolve()
OUT=Path(os.environ['DDG_OUT']).resolve()
ANALYSIS=None

@lru_cache(None)
def syntax(file):
    tree=ast.parse(Path(file).read_text(encoding='utf8'))
    return {(n.lineno,n.col_offset,n.end_lineno,n.end_col_offset):n for n in ast.walk(tree) if hasattr(n,'end_lineno')}

@lru_cache(None)
def comprehension_elements(file):
    return {(n.elt.lineno,n.elt.col_offset,n.elt.end_lineno,n.elt.end_col_offset):
            (n.lineno,n.col_offset,n.end_lineno,n.end_col_offset)
            for n in syntax(file).values() if isinstance(n,ast.ListComp)}

@lru_cache(None)
def enclosing_conditions(file):
    """Map source events to the if statements governing their enclosing suites."""
    mapping={}
    def visit(node,guards):
        if hasattr(node,'end_lineno'):
            span=(node.lineno,node.col_offset,node.end_lineno,node.end_col_offset)
            mapping[span]=guards
        if isinstance(node,ast.If):
            visit(node.test,guards)
            for statement in (*node.body,*node.orelse):
                visit(statement,guards+(span,))
        else:
            for child in ast.iter_child_nodes(node):
                visit(child,() if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)) else guards)
    visit(ast.parse(Path(file).read_text(encoding='utf8')),())
    return mapping

@lru_cache(None)
def instructions(code):
    return {op.offset:op for op in dis.get_instructions(code,show_caches=True)}

@lru_cache(None)
def location_map(file):
    return IIDs(file).iid_to_location


def application_frame():
    f=sys._getframe(1)
    while f:
        if f.f_code.co_filename.startswith(str(ROOT)) and not f.f_code.co_filename.endswith('.orig'):
            return f
        f=f.f_back

class Analysis(BaseAnalysis):
    def __init__(self):
        super().__init__()
        global ANALYSIS
        ANALYSIS=self
        self.test=None
        self.reset()
    def reset(self):
        self.nodes=[];self.edges=[];self.latest={};self.vars={};self.pending={}
        self.memory={};self.objects={};self.calls=[];self.criteria=[];self.gaps=Counter({'augmented assignment effects not modeled':1})
        self.frame_ids={};self.prepared={}
        self.comprehensions={}
        self.boolean_operands={}
        self.conditions={}
        self.context_sources={}
        self.decorator_values={}
        self.context_tokens={}
        self.store_cursor={}
    def frame_id(self,f):
        if f not in self.frame_ids:self.frame_ids[f]=len(self.frame_ids)
        return self.frame_ids[f]
    @lru_cache(None)
    def info(self,file,iid):
        if isinstance(iid,tuple):return iid,syntax(file).get(iid)
        loc=location_map(file)[iid]
        key=(loc.start_line,loc.start_column,loc.end_line,loc.end_column)
        return key,syntax(file).get(key)
    def node(self,kind,file,iid,parents=(),**kw):
        if not self.test:return None
        f=application_frame()
        if f is None:return None
        key,n=self.info(file,iid)
        idx=len(self.nodes)
        path=Path(file).relative_to(ROOT).as_posix().removesuffix('.orig')
        self.nodes.append(dict(id=idx,kind=kind,file=path,line=key[0],span=key,frame=self.frame_id(f),**kw))
        for p in dict.fromkeys(parents):
            if p is not None and p<idx:self.edges.append([p,idx,kind])
        for guard in enclosing_conditions(file).get(key,()):
            condition=self.conditions.get((f,guard))
            if condition is not None:self.edges.append([condition,idx,'control'])
        self.latest[f,key]=idx
        runtime=f.f_back
        if (f.f_code.co_name=='<lambda>' and runtime is not None
                and runtime.f_code.co_name=='_binary_op_'
                and runtime.f_globals.get('__name__')=='dynapyt.runtime'):
            self.boolean_operands.setdefault(runtime,{})[f]=idx
        comp=comprehension_elements(file).get(key)
        if comp is not None and f.f_code.co_name=='<listcomp>':
            self.comprehensions.setdefault((f.f_back,comp),[]).append(idx)
        return idx
    def deps(self,file,iid):
        f=application_frame();_,n=self.info(file,iid)
        if n is None:return []
        parents=[]
        for child in ast.iter_child_nodes(n):
            if isinstance(child,(ast.Load,ast.Store,ast.operator,ast.cmpop,ast.unaryop,ast.boolop)):continue
            if isinstance(child,ast.keyword):child=child.value
            key=(getattr(child,'lineno',None),getattr(child,'col_offset',None),getattr(child,'end_lineno',None),getattr(child,'end_col_offset',None))
            p=self.latest.get((f,key))
            if p is not None:parents.append(p)
        return parents
    def read_identifier(self,file,iid,val):
        if not self.test:return
        _,n=self.info(file,iid);f=application_frame()
        name=getattr(n,'id',None);p=self.vars.get((f,name))
        if p is None and f.f_code.co_name=='<listcomp>' and name in f.f_code.co_freevars:
            p=self.vars.get((f.f_back,name))
        if p is None:self.gaps['binding outside traced scope']+=1
        self.node('read_name',file,iid,[p],name=name)
    def write(self,file,iid,old_vals,new_val):
        if not self.test:return
        f=application_frame();_,n=self.info(file,iid)
        node=self.node('assignment_pending',file,iid,self.deps(file,iid))
        self.prepared[f]=(node,n)
        self.store_cursor[f]=0
        f.f_trace=trace
        f.f_trace_opcodes=True
        f.f_trace_lines=False
        # Completion is handled only after the actual STORE opcode succeeds.
    def heap_target(self,frame,target):
        """Resolve only operands whose lookup cannot execute application code."""
        def operand(expr):
            if isinstance(expr,ast.Name):
                if expr.id in frame.f_locals:return frame.f_locals[expr.id]
                return frame.f_globals[expr.id]
            if isinstance(expr,ast.Constant):return expr.value
            if isinstance(expr,ast.Attribute):
                owner=operand(expr.value)
                descriptor=inspect.getattr_static(type(owner),expr.attr,None)
                if hasattr(type(descriptor),'__get__'):raise ValueError('descriptor operand')
                namespace=object.__getattribute__(owner,'__dict__')
                return namespace[expr.attr]
            raise ValueError('dynamic operand')
        base=operand(target.value)
        if isinstance(target,ast.Attribute):
            # Properties and custom attribute protocols require their own effects.
            if type(base).__getattribute__ is not object.__getattribute__ or type(base).__setattr__ is not object.__setattr__:
                raise ValueError('custom attribute protocol')
            descriptor=inspect.getattr_static(type(base),target.attr,None)
            if hasattr(type(descriptor),'__set__'):raise ValueError('descriptor')
            key=target.attr
        else:
            key=operand(target.slice)
            if type(base) not in (dict,list) or type(key) not in (str,int):raise ValueError('custom subscript')
            if type(base)is list and type(key)is int and key<0:key+=len(base)
        self.objects[id(base)]=base
        return id(base),key
    def read_subscript(self,file,iid,base,sl,val):
        if not self.test:return
        parents=self.deps(file,iid)
        if len(sl)==1 and type(base) in (dict,list,tuple) and type(sl[0]) in (str,int):
            key=sl[0]
            if type(base) in (list,tuple) and type(key)is int and key<0:key+=len(base)
            p=self.memory.get((id(base),key))
            if p is None:self.gaps['element origin unavailable']+=1
            parents.append(p)
        else:self.gaps['unsupported subscript location']+=1
        node=self.node('read_item',file,iid,parents)
        if self.calls:self.calls[-1]['reads'].append((id(base),sl,node))
    def read_attribute(self,file,iid,base,name,val):
        if not self.test:return
        p=self.memory.get((id(base),name))
        if p is None:self.gaps['attribute origin unavailable']+=1
        self.node('read_attribute',file,iid,[*self.deps(file,iid),p],name=name)
    def operation(self,file,iid,operator,operands,result):
        if not self.test:return
        parents=self.deps(file,iid)
        consumed=[set(),set()]
        if operator in ('Equal','NotEqual') and type(result) is bool and result==(operator=='Equal'):
            # Successful equality between distinct native containers has consumed every
            # corresponding value. Descend only through side-effect-free builtin keys.
            pending=[tuple(operands)]
            while pending:
                left,right=pending.pop()
                if left is right or type(left) is not type(right) or type(left) not in (list,dict,tuple):continue
                if type(left) is dict:
                    if not all(type(k) in (str,int) for k in left) or not all(type(k) in (str,int) for k in right):continue
                    keys=left
                else:keys=range(len(left))
                for k in keys:
                    for side,container in enumerate((left,right)):
                        writer=self.memory.get((id(container),k))
                        if writer is not None:consumed[side].add(writer);parents.append(writer)
                    pending.append((left[k],right[k]))
        if operator in ('And','Or'):
            runtime=sys._getframe(1)
            while runtime and not (runtime.f_code.co_name=='_binary_op_' and runtime.f_globals.get('__name__')=='dynapyt.runtime'):
                runtime=runtime.f_back
            parents=list(self.boolean_operands.pop(runtime,{}).values())
            if not parents:self.gaps['short circuit operand origin unavailable']+=1
        self.node('operation',file,iid,parents,operator=operator,**({'consumed_writes':[sorted(x) for x in consumed]} if any(consumed) else {}))
    def literal(self,file,iid,val):
        if self.test:self.node('literal',file,iid)
    def _list(self,file,iid,value):
        if not self.test:return
        _,n=self.info(file,iid)
        node=self.node('list_identity',file,iid)
        self.objects[id(value)]=value
        f=application_frame()
        if isinstance(n,ast.List):
            for i,e in enumerate(n.elts):
                key=(e.lineno,e.col_offset,e.end_lineno,e.end_col_offset)
                p=self.latest.get((f,key))
                if p is not None:self.memory[id(value),i]=p
        elif isinstance(n,ast.ListComp):
            key=(n.lineno,n.col_offset,n.end_lineno,n.end_col_offset)
            elements=self.comprehensions.pop((f,key),[])
            if len(elements)==len(value):
                for i,parent in enumerate(elements):self.memory[id(value),i]=parent
            else:self.gaps['comprehension element mapping incomplete']+=1
    def dictionary(self,file,iid,items,value):
        if not self.test:return
        self.node('dict_identity',file,iid)
        self.objects[id(value)]=value
        _,n=self.info(file,iid);f=application_frame()
        if isinstance(n,ast.Dict):
            for k,v in zip(n.keys,n.values):
                if isinstance(k,ast.Constant) and type(k.value) in (str,int):
                    p=self.latest.get((f,(v.lineno,v.col_offset,v.end_lineno,v.end_col_offset)))
                    if p is not None:self.memory[id(value),k.value]=p
                else:self.gaps['dynamic dictionary key']+=1
    def pre_call(self,file,iid,function,pos_args,kw_args):
        if not self.test:return
        name=(getattr(function,'__module__',None) or '')+'.'+(getattr(function,'__qualname__',None) or type(function).__name__)
        parents=self.deps(file,iid)
        target=function.__func__ if inspect.ismethod(function) else function
        decorated=self.decorator_values.get(id(target))
        if decorated is not None and decorated[0] is target:
            parents.append(decorated[1])
        node=self.node('call',file,iid,parents,function=name)
        bindings={}
        f=application_frame()
        _,expr=self.info(file,iid)
        if isinstance(expr,ast.Call) and (inspect.isfunction(function) or inspect.ismethod(function) or inspect.isclass(function)):
            if not any(isinstance(x,ast.Starred) for x in expr.args) and not any(x.arg is None for x in expr.keywords):
                def token(x):
                    return self.latest.get((f,(x.lineno,x.col_offset,x.end_lineno,x.end_col_offset)))
                try:
                    bound=inspect.signature(function).bind(*[token(x) for x in expr.args],
                        **{x.arg:token(x.value) for x in expr.keywords})
                    bindings=dict(bound.arguments)
                    if inspect.ismethod(function) and isinstance(expr.func,ast.Attribute):
                        receiver=next(iter(inspect.signature(function.__func__).parameters))
                        bindings[receiver]=token(expr.func.value)
                    elif inspect.isclass(function):
                        initializer=inspect.getattr_static(function,'__init__',None)
                        if inspect.isfunction(initializer):
                            receiver=next(iter(inspect.signature(initializer).parameters))
                            bindings[receiver]=node
                except (ValueError,TypeError):
                    self.gaps['argument binding unsupported']+=1
            else:self.gaps['expanded argument binding unsupported']+=1
        owner=getattr(function,'__self__',None) if inspect.isbuiltin(function) else None
        setdefault_new=(type(owner) is dict and function.__name__=='setdefault' and pos_args
                        and type(pos_args[0]) in (str,int) and all(type(k) in (str,int) for k in owner)
                        and pos_args[0] not in owner)
        self.calls.append(dict(setdefault_new=bool(setdefault_new),file=file,iid=iid,node=node,name=name,caller=f,returned=None,reads=[],bindings=bindings,function=function,argument_ids={id(v) for v in (*pos_args,*kw_args.values())},writes={}))
    def function_enter(self,file,iid,args,name,is_lambda):
        if not self.test:return
        f=application_frame()
        entry=self.node('function_enter',file,iid,function=name)
        if self.calls:
            state=self.calls[-1]
            function=state['function']
            code=getattr(function,'__code__',None)
            if inspect.isclass(function):
                code=getattr(inspect.getattr_static(function,'__init__',None),'__code__',None)
            if code is f.f_code:
                self.edges.append([state['node'],entry,'invocation'])
                state['frame']=f
                for param,parent in state['bindings'].items():
                    if isinstance(parent,int):
                        node=self.node('parameter',file,iid,[parent],name=param)
                        self.vars[f,param]=node
                    else:self.gaps['variadic or missing argument origin']+=1
            else:self.gaps['function entry not matched to direct call']+=1
    def _return(self,file,iid,function_iid,function_name,value):
        if not self.test:return
        node=self.node('return',file,iid,self.deps(file,iid),function=function_name)
        f=application_frame()
        for state in reversed(self.calls):
            if state.get('frame') is f:
                state['returned']=node
                break
    def post_call(self,file,iid,result,call,pos_args,kw_args):
        if not self.test:return
        state=None
        f=application_frame()
        for index in range(len(self.calls)-1,-1,-1):
            candidate=self.calls[index]
            if candidate['caller'] is f and candidate['file']==file and candidate['iid']==iid and candidate['function'] is call:
                state=candidate
                self.gaps['call unwound without successful return']+=len(self.calls)-index-1
                del self.calls[index:]
                break
        parents=[state['returned'] if state['returned'] is not None else state['node']] if state else []
        if call is getattr and len(pos_args)>=2 and type(pos_args[1])is str:
            parents.append(self.memory.get((id(pos_args[0]),pos_args[1])))
        owner=getattr(call,'__self__',None) if inspect.isbuiltin(call) else None
        method=getattr(call,'__name__','') if owner is not None else ''
        if type(owner) is contextvars.ContextVar:
            source=self.context_sources.setdefault(owner,contextvars.ContextVar('specmind_context_source'))
            if method=='get':parents.append(source.get(None))
        node=self.node('call_result',file,iid,parents)
        if type(owner) is contextvars.ContextVar:
            if method=='set':
                self.context_tokens[id(result)]=(result,source.set(node))
            elif method=='reset' and pos_args:
                saved=self.context_tokens.pop(id(pos_args[0]),None)
                if saved is not None:source.reset(saved[1])
                else:source.set(None)
        if state and type(owner) is dict and method=='setdefault' and pos_args and type(pos_args[0]) in (str,int):
            location=id(owner),pos_args[0]
            if state.get('setdefault_new'):
                self.objects[id(owner)]=owner
                self.memory[location]=node
            for active in self.calls:
                if id(owner) in active['argument_ids']:
                    if state.get('setdefault_new'):active['writes'][location]=node
                    if type(result) in (dict,list):active['argument_ids'].add(id(result))
        if type(owner) is list and method=='append':
            self.objects[id(owner)]=owner
            self.memory[id(owner),len(owner)-1]=node
        if type(owner) is list and method in ('clear','pop','insert','remove','sort','reverse'):
            for key in list(self.memory):
                if key[0]==id(owner):del self.memory[key]
        if call is getattr and len(pos_args)>=2 and type(pos_args[1])is str:
            self.nodes[node]['attribute']=pos_args[1]
        if os.environ.get('DDG_DIAG') and call is getattr:
            self.nodes[node]['lookup']=[id(pos_args[0]),pos_args[1],parents]
        if state:
            self.criteria.append(dict(function=state['name'],call=state['node'],result=node,mutations=[writer for loc,writer in state['writes'].items() if self.memory.get(loc)==writer]))
        if not inspect.isfunction(call) and not inspect.ismethod(call):
            self.gaps['external call effects unmodeled']+=1
    def enter_if(self,file,iid,cond_value):
        if self.test:
            key,stmt=self.info(file,iid)
            if isinstance(stmt,ast.If):
                expr=stmt.test
                test_span=(expr.lineno,expr.col_offset,expr.end_lineno,expr.end_col_offset)
                f=application_frame()
                self.conditions[f,key]=self.node('condition',file,iid,[self.latest.get((f,test_span))])
            else:self.gaps['unmodeled conditional expression']+=1
    def enter_for(self,file,iid,next_value,iterable,iterator):
        if self.test:self.node('iteration',file,iid)
    def exception(self,file,iid,exceptions,caught):
        if self.test:
            self.node('exception',file,iid,type=type(caught).__name__)
            # Retain calls whose callee is this handler's frame or a live ancestor.
            live=set()
            f=application_frame()
            while f:
                live.add(f)
                f=f.f_back
            self.calls[:]=[state for state in self.calls if state.get('frame') in live]
            self.gaps['exception call binding incomplete']+=1

def trace(frame,event,arg):
    a=ANALYSIS
    if a is None or not a.test:return
    if event=='call':
        return None
    if event=='exception':
        a.pending.pop(frame,None);a.prepared.pop(frame,None)
    if event=='opcode':
        pending=a.pending.pop(frame,None)
        if pending:
            node,kind,location=pending
            if kind=='name':a.vars[frame,location]=node
            elif kind=='heap':
                a.memory[location]=node
                for invocation in a.calls:
                    if location[0] in invocation['argument_ids']:
                        invocation['writes'][location]=node
                if os.environ.get('DDG_DIAG'):a.nodes[node]['heap_write']=location
            elif kind=='unknown_heap':
                if os.environ.get('DDG_DIAG') and node is not None:a.nodes[node]['invalidates']=location
                if location is None:a.memory.clear()
                else:
                    for key in list(a.memory):
                        if key[0]==location:del a.memory[key]
                a.gaps['committed heap write target unresolved']+=1
            statement=a.prepared.get(frame,(None,None))[1]
            targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target] if isinstance(statement,ast.AnnAssign) else []
            if a.store_cursor.get(frame,0)>=len(targets):
                a.prepared.pop(frame,None)
                frame.f_trace=None
                return None
        op=instructions(frame.f_code)[frame.f_lasti]
        if op.opname in ('STORE_FAST','STORE_DEREF'):
            prepared=a.prepared.get(frame)
            if prepared:
                a.pending[frame]=(prepared[0],'name',op.argval)
                a.store_cursor[frame]=a.store_cursor.get(frame,0)+1
        elif op.opname in ('STORE_ATTR','STORE_SUBSCR'):
            prepared=a.prepared.get(frame)
            target=None
            if prepared:
                node,statement=prepared
                targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target] if isinstance(statement,ast.AnnAssign) else []
                cursor=a.store_cursor.get(frame,0)
                if cursor<len(targets):target=targets[cursor]
                a.store_cursor[frame]=cursor+1
                if not isinstance(target,ast.Attribute if op.opname=='STORE_ATTR' else ast.Subscript):target=None
            try:
                if target is None:raise ValueError('target unresolved')
                location=a.heap_target(frame,target)
                a.pending[frame]=(node,'heap',location)
            except (ValueError,KeyError,AttributeError):
                base=None
                if target is not None and isinstance(target.value,ast.Name):
                    name=target.value.id
                    base=frame.f_locals.get(name,frame.f_globals.get(name))
                a.pending[frame]=(prepared[0] if prepared else None,'unknown_heap',id(base) if base is not None else None)
        elif op.opname in ('DELETE_ATTR','DELETE_SUBSCR'):
            a.memory.clear()
            a.gaps['heap write invalidated origins; committed heap target unresolved']+=1
    if event=='return':
        a.pending.pop(frame,None);a.prepared.pop(frame,None)
    return trace

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item,nextitem):
    # Includes fixture setup and teardown in the same test trace.
    from dynapyt.runtime import RuntimeEngine
    rt=RuntimeEngine()
    a=ANALYSIS
    if a is None:raise RuntimeError('DynaPyt analysis not initialized')
    a.reset();a.test=item.nodeid
    old=sys.gettrace();sys.settrace(trace)
    try:yield
    finally:
        sys.settrace(old)
        a.gaps['open calls at end']+=len(a.calls)
        path=OUT/'traces'/(hashlib.sha256(item.nodeid.encode()).hexdigest()+'.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(dict(test=item.nodeid,nodes=a.nodes,edges=a.edges,criteria=a.criteria,gaps=dict(a.gaps))),encoding='utf8')
        a.test=None
