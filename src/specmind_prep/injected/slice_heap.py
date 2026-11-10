"""Bounded identity/location provenance for exact builtin dict/list objects.

No hooks, properties, custom hashing/equality or deep snapshots are invoked.
This layer is called by instrumentation; it does not discover memory accesses.
Dictionary keys are exact str; lists support indexed replacement only.
"""
import json
import math


class UnsupportedHeap(ValueError):
    pass


class HeapMemory:
    def __init__(self, *, max_objects=1000, max_locations=10000):
        self.objects = {}  # Strong references prevent native id reuse during trace.
        self.references = {}  # Keep nested reference identities alive too.
        self.definitions = {}
        self.observed = {}
        self.max_objects = max_objects
        self.max_locations = max_locations
        self.last_event = 0

    def _event(self, event):
        if type(event) is not int or event <= self.last_event:
            raise ValueError('Heap producer must be a new increasing event ID')

    def _token(self, value):
        if type(value) in (dict,list):
            # Validate reference targets lazily; never traverse arbitrary graphs.
            return ('reference',id(value))
        if type(value) not in (type(None),bool,int,float,str):
            raise UnsupportedHeap('Only exact dict/list and primitive values are modeled')
        if type(value) is float and not math.isfinite(value):
            raise UnsupportedHeap('Non-finite float')
        if type(value) is int and value.bit_length()>4096:
            raise UnsupportedHeap('Integer exceeds snapshot bound')
        if type(value) is str and len(value)>16384:
            raise UnsupportedHeap('String exceeds snapshot bound')
        return (type(value).__name__,value)

    def _retain(self, values):
        refs={id(v):v for v in values if type(v) in (dict,list)}
        if len(set(self.objects)|set(self.references)|set(refs))>self.max_objects:
            raise UnsupportedHeap('Heap object budget exceeded')
        self.references.update(refs)

    def _entries(self, obj):
        if type(obj) in (dict,list) and len(obj)>self.max_locations:
            raise UnsupportedHeap('Heap location budget exceeded')
        if type(obj) is dict:
            if any(type(k) is not str or len(k)>16384 for k in obj):
                raise UnsupportedHeap('Dictionary requires exact string keys')
            return list(obj.items())
        if type(obj) is list:
            return list(enumerate(obj))
        raise UnsupportedHeap('Only exact builtin dict/list containers are modeled')

    def register(self, obj, producer):
        """Register one allocation/external origin, before any traced mutation.

        Calling register again returns the same identity and NEVER overwrites
        previous writers. Nested objects require their own explicit origins.
        """
        native=id(obj)
        if native in self.objects:
            self._consistent(obj)
            return self.objects[native]['identity']
        self._event(producer)
        entries=self._entries(obj)
        tokens={key:self._token(value) for key,value in entries}
        if len(self.objects)>=self.max_objects or len(self.definitions)+len(entries)>self.max_locations:
            raise UnsupportedHeap('Heap observation budget exceeded')
        if len(set(self.objects)|set(self.references)|{native}|{id(v) for _,v in entries if type(v) in (dict,list)})>self.max_objects:
            raise UnsupportedHeap('Heap object budget exceeded')
        self._retain([v for _,v in entries])
        identity='o'+str(len(self.objects)+1)
        self.objects[native]={'object':obj,'identity':identity,'origin':producer}
        self.observed[native]=tokens
        for key in tokens:self.definitions[(native,key)]=producer
        self.last_event=producer
        return identity

    def _consistent(self,obj):
        native=id(obj)
        if native not in self.objects:
            raise UnsupportedHeap('Container has no observed origin')
        current={key:self._token(value) for key,value in self._entries(obj)}
        if current!=self.observed[native]:
            raise UnsupportedHeap('Uninstrumented mutation detected')

    def location(self,obj,key):
        native=id(obj)
        if native not in self.objects:
            raise UnsupportedHeap('Container has no observed origin')
        if type(obj) is dict:
            if type(key) is not str:raise UnsupportedHeap('Dictionary key must be exact str')
        else:
            if type(key) is not int:raise UnsupportedHeap('List index must be exact int; slices unsupported')
            if key<0:key+=len(obj)
            if key<0 or key>=len(obj):raise IndexError('list index out of range')
        return native,key

    def describe(self,location):
        native,key=location
        return self.objects[native]['identity']+'['+json.dumps(key,ensure_ascii=True)+']'

    def read(self,obj,key):
        """Return value and exact last writer; caller emits the read event."""
        self._consistent(obj)
        location=self.location(obj,key)
        value=obj[location[1]]  # Native KeyError/IndexError remains observable.
        return value,{'location':self.describe(location),'producer':self.definitions[location]}

    def write(self,obj,key,value,producer):
        """Execute one supported store and update producer only on success."""
        self._event(producer)
        self._consistent(obj)
        location=self.location(obj,key)
        token=self._token(value)
        if location not in self.definitions and len(self.definitions)>=self.max_locations:
            raise UnsupportedHeap('Heap observation budget exceeded')
        self._retain([value])
        obj[location[1]]=value
        self.observed[id(obj)][location[1]]=token
        self.definitions[location]=producer
        self.last_event=producer
        return {'location':self.describe(location),'producer':producer}

    def delete(self,obj,key,producer):
        self._event(producer)
        self._consistent(obj)
        if type(obj) is not dict:
            raise UnsupportedHeap('List structural mutation requires an index-shift model')
        location=self.location(obj,key)
        del obj[location[1]]
        del self.observed[id(obj)][location[1]]
        # Keep a tombstone producer for future membership/absence models. read()
        # still raises KeyError and does not present absence as an existing value.
        self.definitions[location]=producer
        self.last_event=producer
        return {'location':self.describe(location),'producer':producer,'deleted':True}
