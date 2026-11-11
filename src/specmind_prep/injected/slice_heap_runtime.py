"""Opt-in caller/target heap runtime and bounded behavioral evidence."""
import json
import sys
from specmind_prep.injected.slice_runtime import TraceLimit
from specmind_prep.injected.slice_calls import CallerRecorder, MethodBoundary
import types
from specmind_prep.injected.slice_runtime import primitive
from specmind_prep.injected.slice_heap import UnsupportedHeap, is_heap_object, instance_dict
from specmind_prep.injected.slice_heap_access import HeapAccessRecorder

MODEL='heap-call-boundary-ddg-v1'


class EvidenceSnapshot:
    """Stable boundary-order identities, independent from instrumentation events."""
    def __init__(self):self.refs={}

    def snapshot(self,value):
        nodes={}
        locations=0
        encoded=0
        def visit(v):
            nonlocal locations,encoded
            if not is_heap_object(v):
                result=primitive(v)
                encoded+=len(json.dumps(result,ensure_ascii=True).encode('utf8'))
                if encoded>8000000:raise UnsupportedHeap('Evidence byte budget exceeded')
                return result
            if len(v if type(v) in (dict,list) else instance_dict(v))>10000:raise UnsupportedHeap('Evidence location budget exceeded')
            if type(v) is dict and any(type(k) is not str for k in v):
                raise UnsupportedHeap('Evidence requires string dictionary keys')
            if id(v) not in self.refs:
                if len(self.refs)>=1000:raise UnsupportedHeap('Evidence object budget exceeded')
                self.refs[id(v)]=(f'e{len(self.refs)+1}',v)
            identity=self.refs[id(v)][0]
            if identity not in nodes:
                locations+=len(v if type(v) in (dict,list) else instance_dict(v))
                if locations>10000:raise UnsupportedHeap('Evidence location budget exceeded')
                nodes[identity]=None
                entries=list(v.items()) if type(v) is dict else list(enumerate(v)) if type(v) is list else list(instance_dict(v).items())
                if type(v) is dict:
                    encoded+=sum(len(json.dumps(k,ensure_ascii=True).encode('utf8')) for k,_ in entries)
                    if encoded>8000000:raise UnsupportedHeap('Evidence byte budget exceeded')
                nodes[identity]={'type':type(v).__name__,'items':[[k,visit(x)] for k,x in entries]}
            return {'ref':identity}
        root=visit(value)
        return {'root':root,'objects':nodes}


class HeapCallerRecorder(CallerRecorder):
    def __init__(self,config):
        super().__init__(config)
        self.heap=HeapAccessRecorder(recorder=self,max_events=config['max_events'])
        self.evidence=EvidenceSnapshot()
        self.boundary=False
        self.live_arguments={}
        self.snapshot_frame=None

    def emit(self,frame,site,kind,*args,**kwargs):
        if kind.startswith('heap_'):
            pending=self.frames[frame]['pending']
            if pending:
                kwargs['links']=dict(kwargs.get('links') or {},**pending.get('links',{}))
        return super().emit(frame,site,kind,*args,**kwargs)

    def snapshot(self,value):
        if self.boundary:
            if is_heap_object(value):
                with self.heap.bind(self.snapshot_frame):self.heap.input(value,'entry')
            return self.evidence.snapshot(value)
        if is_heap_object(value):
            if id(value) not in self.heap.memory.objects:
                self.issue('Object has no observed allocation/input origin')
                return {'type':'unmodeled'}
            return {'type':type(value).__name__,'object':self.heap.memory.objects[id(value)]['identity']}
        return super().snapshot(value)

    def enter(self,arguments,function):
        if not self.active:return None
        previous=self.boundary,self.snapshot_frame
        self.boundary,self.snapshot_frame=True,len(self.calls)+1
        try:frame=super().enter(arguments,function)
        finally:self.boundary,self.snapshot_frame=previous
        if frame is not None:self.live_arguments[frame]=dict(arguments)
        return frame

    def return_value(self,frame,value):
        if frame is None:return value
        # No new origins may be invented for a return value produced outside the
        # instrumented allocation/input hooks.
        if is_heap_object(value) and id(value) not in self.heap.memory.objects:
            self.issue('Unobserved returned object')
        for record in self.heap.memory.objects.values():self.heap.memory._consistent(record['object'])
        previous=self.boundary,self.snapshot_frame
        self.boundary,self.snapshot_frame=True,frame
        try:
            result=super().return_value(frame,value)
            self.calls[frame-1]['arguments_after']={k:self.evidence.snapshot(v) for k,v in self.live_arguments[frame].items()}
            return result
        finally:self.boundary,self.snapshot_frame=previous

    def heap_read(self,frame,site,obj,key):
        if frame is None:return obj[key]
        with self.heap.bind(frame):return self.heap.read(obj,key,site)

    def heap_attr_read(self,frame,site,obj,name):
        if frame is None:return getattr(obj,name)
        with self.heap.bind(frame):return self.heap.read(obj,name,site,attribute=True)

    def heap_attr_store(self,frame,site,value,obj,name):
        if frame is None:setattr(obj,name,value);return
        with self.heap.bind(frame):self.heap.write(value,obj,name,site,attribute=True)
        self.expression(frame)

    def heap_literal(self,frame,site,value):
        if frame is None:return value
        with self.heap.bind(frame):return self.heap.origin(value,site)

    def heap_store(self,frame,site,value,obj,key):
        if frame is None:obj[key]=value;return
        with self.heap.bind(frame):self.heap.write(value,obj,key,site)
        self.expression(frame)

    def _reachable(self, values):
        pending=list(values);seen=set()
        while pending:
            obj=pending.pop()
            if not is_heap_object(obj) or id(obj) in seen:continue
            if id(obj) not in self.heap.memory.objects:
                raise UnsupportedHeap('Argument object has no observed origin')
            seen.add(id(obj))
            pending.extend(v for _,v in self.heap.memory._entries(obj) if is_heap_object(v))
        return seen

    def _attach(self,event,fields):
        size=len(json.dumps(fields,ensure_ascii=True).encode('utf8'))
        if self.encoded_bytes+size>self.config['max_bytes']:
            self.limited=True
            raise TraceLimit('Heap criterion metadata budget exceeded')
        event.update(fields)
        self.encoded_bytes+=size

    def call_target(self,frame,site,function,positional,keywords):
        if frame is None:return super().call_target(frame,site,function,positional,keywords)
        actual = function.function if type(function) is MethodBoundary else function
        implementation = actual.__func__ if type(actual) is types.MethodType else actual
        selected=next((name for name,(fn,code) in self.targets.items() if fn is implementation),self.config['function'])
        spec=self.config['functions'][selected]
        bound = [function.receiver, *positional] if type(function) is MethodBoundary else positional
        values={n:pair[0] for n,pair in zip(spec['positional'],bound)}
        values.update({n:pair[0] for n,pair in keywords.items()})
        reachable={n:self._reachable([v]) for n,v in values.items()}
        roots={n:sorted({self.heap.memory.objects[i]['origin'] for i in ids} |
                        {producer for (i,key),producer in self.heap.memory.definitions.items()
                         if i in ids and key in self.heap.memory.observed[i]}) for n,ids in reachable.items()}
        start=len(self.events)
        value=super().call_target(frame,site,function,positional,keywords)
        calls=[e for e in self.events[start:] if e['kind']=='invocation' and e['frame']==frame]
        if len(calls)!=1:
            self.issue('Heap boundary requires one selected nonrecursive invocation')
            return value
        invocation=calls[0]
        bridges=[e for e in self.events[start:] if e['kind']=='call_result' and e['links']['invocation']==invocation['id']]
        if len(bridges)!=1:
            self.issue('Missing heap return bridge')
            return value
        after=set().union(*reachable.values()) if reachable else set()
        after.update(self._reachable(values.values()))
        mutations=[]
        for (i,key),producer in self.heap.memory.definitions.items():
            if i in after and producer>invocation['id']:
                mutations.append({'location':self.heap.memory.describe((i,key)), 'root':producer,
                                  'deleted':key not in self.heap.memory.observed[i]})
        self._attach(invocation,{'heap_argument_roots':roots})
        self._attach(bridges[0],{'heap_mutations':mutations})
        return value

    def heap_scalar(self,frame,value):
        if frame is None:return value
        if is_heap_object(value):
            raise UnsupportedHeap('Container operators/truth/membership require explicit content/shape model')
        primitive(value)
        return value

    def predicate(self,frame,value):
        self.heap_scalar(frame,value)
        return super().predicate(frame,value)

    def raised(self,frame):
        exc=sys.exc_info()[1]
        if isinstance(exc,UnsupportedHeap):self.issue(str(exc))
        return super().raised(frame)
