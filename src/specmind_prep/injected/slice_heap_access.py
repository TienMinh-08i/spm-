"""AST hooks for bounded dict/list accesses; heap events are not a full DDG.

This opt-in layer preserves native evaluation order for supported operations.
Local-variable and predicate edges still belong to the main DDG instrumenter.
"""
import ast
from contextlib import contextmanager
from specmind_prep.injected.slice_heap import HeapMemory, UnsupportedHeap, is_heap_object, instance_dict

HELPER = '__specmind_heap_access__'


class HeapAccessRecorder:
    def __init__(self, *, max_events=10000, recorder=None, **limits):
        self.memory = HeapMemory(**limits)
        self.recorder = recorder
        self.frame = None
        self.events = recorder.events if recorder is not None else []
        self.max_events = max_events

    @contextmanager
    def bind(self, frame):
        """Use a frame owned by the main recorder; nesting restores caller frame."""
        if self.recorder is None or frame not in self.recorder.frames:
            raise ValueError('Bind an existing main-recorder frame')
        previous=self.frame
        self.frame=frame
        try:yield self
        finally:self.frame=previous

    def _append(self, event):
        if self.recorder is None:
            self.events.append(event)
            return
        if self.frame not in self.recorder.frames:
            raise ValueError('Heap access needs a bound recorder frame')
        state=self.recorder.frames[self.frame]
        pending=state['pending']
        argument_heap=state.get('argument_heap_stack',[])
        reads=dict(state['argument_stack'][-1]) if state.get('argument_stack') else dict(pending['reads']) if pending else {}
        inputs=set(pending['inputs']) if pending else set()
        details={'heap_reads':{},'heap_writes':[],
                 'heap_dependencies':sorted(argument_heap[-1]) if argument_heap else sorted(pending.get('heap_dependencies',())) if pending else []}
        if event['kind']=='heap_read':
            details['heap_reads']={event['location']:event['depends_on'][0]}
        elif event['kind']=='heap_origin':
            obj=next(r for r in self.memory.objects.values() if r['identity']==event['object'])
            details['heap_writes']=[self.memory.describe((id(obj['object']),k)) for k in self.memory.observed[id(obj['object'])]]
            details['heap_object']=event['object']
        else:
            details['heap_writes']=[event['location']]
            details['heap_deleted']=event['kind']=='heap_delete'
        ident=self.recorder.emit(self.frame,event['site'],event['kind'],reads,inputs,details=details)
        if ident!=event['id']:raise ValueError('Heap/main event IDs diverged')
        if event['kind'] in {'heap_read','heap_origin'}:
            if argument_heap:argument_heap[-1].add(ident)
            elif pending is not None:pending.setdefault('heap_dependencies',set()).add(ident)

    def _next(self):
        if self.recorder is not None and self.frame not in self.recorder.frames:
            raise ValueError('Heap access needs a bound recorder frame')
        if self.recorder is not None and len(self.events)>=self.recorder.config['max_events']:
            self.recorder.limited=True
            raise UnsupportedHeap('Main recorder event budget exceeded')
        if len(self.events)>=self.max_events:
            raise UnsupportedHeap('Heap event budget exceeded')
        return len(self.events)+1

    def origin(self, value, site, kind='allocation'):
        if not is_heap_object(value):return value
        existing=id(value) in self.memory.objects
        event=self._next() if not existing else self.memory.last_event
        identity=self.memory.register(value,event)
        if not existing:
            self._append({'id':event,'kind':'heap_origin','site':site,
                                'object':identity,'origin':kind,'depends_on':[]})
        return value

    def input(self, value, site):
        pending=[value];seen=set()
        while pending:
            obj=pending.pop()
            if not is_heap_object(obj) or id(obj) in seen:continue
            seen.add(id(obj))
            self.origin(obj,site,'input')
            pending.extend(v for _,v in self.memory._entries(obj) if is_heap_object(v))

    def read(self, obj, key, site, *, attribute=False):
        if attribute == (type(obj) in (dict, list)):
            raise UnsupportedHeap("Attribute/item access domain mismatch")
        event=self._next()
        value,observed=self.memory.read(obj,key)
        self._append({'id':event,'kind':'heap_read','site':site,
                            'location':observed['location'],'depends_on':[observed['producer']]})
        return value

    def write(self, value, obj, key, site, *, attribute=False):
        if attribute == (type(obj) in (dict, list)):
            raise UnsupportedHeap("Attribute/item access domain mismatch")
        # Python assignment evaluates RHS before the target container and key.
        event=self._next()
        observed=self.memory.write(obj,key,value,event)
        self._append({'id':event,'kind':'heap_write','site':site,
                            'location':observed['location'],'depends_on':[]})

    def delete(self, obj, key, site):
        event=self._next()
        observed=self.memory.delete(obj,key,event)
        self._append({'id':event,'kind':'heap_delete','site':site,
                            'location':observed['location'],'depends_on':[]})


def instrument_heap_accesses(source, filename='<heap-access-test>'):
    """Compile an opt-in access recorder; reject unsupported target rewrites.

    This records heap locations only. It must NOT be marked complete_supported
    by the production slicer until local/control/call edges are integrated.
    """
    tree=ast.parse(source,filename=filename)
    if any(isinstance(n,ast.Name) and n.id==HELPER for n in ast.walk(tree)):
        raise UnsupportedHeap('Reserved heap hook identifier')
    parents={id(child):parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for n in ast.walk(tree):
        if (isinstance(n,ast.arg) and n.arg==HELPER) or (isinstance(n,ast.FunctionDef) and n.name==HELPER):
            raise UnsupportedHeap('Reserved heap hook identifier')
        if isinstance(n,ast.alias) and (n.asname or n.name.split('.')[0])==HELPER:
            raise UnsupportedHeap('Reserved heap hook identifier')
        if isinstance(n,ast.Subscript) and isinstance(n.ctx,ast.Store):
            parent=parents[id(n)]
            if not isinstance(parent,ast.Assign) or len(parent.targets)!=1 or parent.targets[0] is not n:
                raise UnsupportedHeap('Only standalone single-target heap assignment is modeled')
        if isinstance(n,(ast.AsyncFunctionDef,ast.Yield,ast.YieldFrom,ast.Await,
                         ast.ClassDef,ast.ListComp,ast.DictComp,ast.SetComp,ast.GeneratorExp)):
            raise UnsupportedHeap('Async/class/comprehension scope not modeled by heap access hooks')
        if isinstance(n,ast.AugAssign) and not isinstance(n.target,ast.Name):
            raise UnsupportedHeap('Heap augmented assignment requires a dedicated read-modify-write model')
        if isinstance(n,ast.Assign) and any(isinstance(x,ast.Subscript) for t in n.targets for x in ast.walk(t)):
            if len(n.targets)!=1 or not isinstance(n.targets[0],ast.Subscript):
                raise UnsupportedHeap('Chained/unpacked heap assignment not modeled')
        if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Subscript):
            raise UnsupportedHeap('Annotated heap assignment not modeled')
        if isinstance(n,ast.Subscript) and isinstance(n.slice,ast.Slice):
            raise UnsupportedHeap('Heap slices not modeled')
        if isinstance(n,ast.Delete) and any(not isinstance(t,ast.Subscript) for t in n.targets):
            raise UnsupportedHeap('Only subscript deletion modeled')
        if isinstance(n,ast.FunctionDef) and (n.args.vararg or n.args.kwarg):
            raise UnsupportedHeap('Variadic input origins not modeled')

    class Hooks(ast.NodeTransformer):
        def call(self,method,node,*args):
            site=ast.Constant(f'{filename}:{node.lineno}:{node.col_offset+1}')
            return ast.copy_location(ast.Call(ast.Attribute(ast.Name(HELPER,ast.Load()),method,ast.Load()),[*args,site],[]),node)

        def visit_FunctionDef(self,node):
            self.generic_visit(node)
            origins=[]
            for arg in (*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs):
                origins.append(ast.copy_location(ast.Expr(self.call('input',node,ast.Name(arg.arg,ast.Load()))),node))
            # Keep the function docstring in its original position.
            offset=int(bool(node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str)))
            node.body[offset:offset]=origins
            return node

        def visit_Dict(self,node):
            return self.call('origin',node,self.generic_visit(node))

        def visit_List(self,node):
            if isinstance(node.ctx,ast.Load):return self.call('origin',node,self.generic_visit(node))
            return self.generic_visit(node)

        def visit_Subscript(self,node):
            if isinstance(node.ctx,ast.Load):
                return self.call('read',node,self.visit(node.value),self.visit(node.slice))
            return self.generic_visit(node)

        def visit_Assign(self,node):
            if len(node.targets)==1 and isinstance(node.targets[0],ast.Subscript):
                t=node.targets[0]
                return ast.copy_location(ast.Expr(self.call('write',node,self.visit(node.value),self.visit(t.value),self.visit(t.slice))),node)
            return self.generic_visit(node)

        def visit_Delete(self,node):
            return [ast.copy_location(ast.Expr(self.call('delete',node,self.visit(t.value),self.visit(t.slice))),node) for t in node.targets]

    return compile(ast.fix_missing_locations(Hooks().visit(tree)),filename,'exec')
