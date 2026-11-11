"""Bounded identity/location provenance for builtin containers and plain instances.

No hooks, properties, custom hashing/equality or deep snapshots are invoked.
This layer is called by instrumentation; it does not discover memory accesses.
Dictionary keys are exact str; lists support indexed replacement only.
"""
import json
import math
import types


class UnsupportedHeap(ValueError):
    pass


def instance_dict(value):
    """Access only ordinary instance storage, never user descriptors/hooks."""
    cls = type(value)
    mro = type.__getattribute__(cls, "__mro__")
    namespaces = [type.__getattribute__(base, "__dict__") for base in mro]
    for operation in ("__getattribute__", "__setattr__", "__delattr__"):
        implementation = next((ns[operation] for ns in namespaces if operation in ns), None)
        if implementation is not getattr(object, operation):
            raise UnsupportedHeap("Custom attribute access requires an effect model")
    dictionary = next((ns["__dict__"] for ns in namespaces if "__dict__" in ns), None)
    if type(dictionary) is not types.GetSetDescriptorType:
        raise UnsupportedHeap("Ordinary instance dictionary required")
    result = object.__getattribute__(value, "__dict__")
    if type(result) is not dict:
        raise UnsupportedHeap("Ordinary instance dictionary required")
    return result


def is_heap_object(value):
    if type(value) in (dict, list):
        return True
    if type(value) in (type(None), bool, int, float, str):
        return False
    try:
        instance_dict(value)
        return True
    except UnsupportedHeap:
        return False


def attribute_storage(value, name):
    data = instance_dict(value)
    for base in type.__getattribute__(type(value), "__mro__"):
        namespace = type.__getattribute__(base, "__dict__")
        if name in namespace:
            descriptor = namespace[name]
            for owner in type.__getattribute__(type(descriptor), "__mro__"):
                members = type.__getattribute__(owner, "__dict__")
                if "__set__" in members or "__delete__" in members:
                    raise UnsupportedHeap("Data descriptor access requires an effect model")
            break
    return data


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
        if is_heap_object(value):
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
        refs={id(v):v for v in values if is_heap_object(v)}
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
        entries = instance_dict(obj)
        if len(entries) > self.max_locations or any(type(k) is not str for k in entries):
            raise UnsupportedHeap("Instance attribute location budget/type exceeded")
        return list(entries.items())

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
        if len(set(self.objects)|set(self.references)|{native}|{id(v) for _,v in entries if is_heap_object(v)})>self.max_objects:
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
        if type(obj) is not list:
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
        if type(obj) in (dict, list):
            value = obj[location[1]]
        else:
            data = attribute_storage(obj, key)
            if key not in data:
                raise UnsupportedHeap("Inherited/missing attribute requires a descriptor model")
            value = object.__getattribute__(obj, key)
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
        if type(obj) in (dict, list):
            obj[location[1]] = value
        else:
            attribute_storage(obj, key)
            object.__setattr__(obj, key, value)
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
