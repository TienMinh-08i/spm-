"""Install one mutant independently of the assertion target. Pytest-process scoped."""
import ast
import functools
import inspect
import __future__
import sys


def compile_mutant(source, original, module):
    namespace = dict(original.__globals__)
    namespace.update(module.__dict__)
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    if len(nodes)!=1 or len(tree.body)!=1:
        raise ValueError('Mutant must contain exactly one function')
    node = nodes[0]
    node.decorator_list=[]
    cells = dict(zip(original.__code__.co_freevars,original.__closure__ or ()))
    if set(cells)-{'__class__'}:
        raise ValueError('Unsupported non-class closure mutation')
    if '__class__' in cells:
        namespace['_specmind_mutation_class']=cells['__class__'].cell_contents
        first = (node.args.posonlyargs+node.args.args)
        if not first:
            raise ValueError('Class-cell method has no receiver')
        class Super(ast.NodeTransformer):
            def visit_Call(self, call):
                self.generic_visit(call)
                if isinstance(call.func,ast.Name) and call.func.id=='super' and not call.args and not call.keywords:
                    call.args=[ast.Name(id='_specmind_mutation_class',ctx=ast.Load()),ast.Name(id=first[0].arg,ctx=ast.Load())]
                return call
            def visit_Name(self, name):
                if name.id=='__class__' and isinstance(name.ctx,ast.Load):
                    name.id='_specmind_mutation_class'
                return name
        tree=Super().visit(tree)
    # Compile class methods in their lexical class context: Python must retain
    # private-name mangling (e.g. self.__helper -> self._Schema__helper).
    owner_name = original.__qualname__.split('.')[-2] if '.' in original.__qualname__ else None
    if owner_name and owner_name != '<locals>':
        container_node = ast.parse('class '+owner_name+': pass').body[0]
        container_node.body = [node]
        previous = namespace.get(owner_name)
        had_previous = owner_name in namespace
        tree.body = [container_node]
        exec(compile(ast.fix_missing_locations(tree),'<mutant>','exec',flags=__future__.annotations.compiler_flag),namespace,namespace)
        name = node.name
        if name.startswith('__') and not name.endswith('__') and owner_name.lstrip('_'):
            name = '_'+owner_name.lstrip('_')+name
        target = vars(namespace[owner_name])[name]
        if isinstance(target,(staticmethod,classmethod)):
            target = target.__func__
        if had_previous:
            namespace[owner_name] = previous
        else:
            namespace.pop(owner_name,None)
    else:
        exec(compile(ast.fix_missing_locations(tree),'<mutant>','exec',flags=__future__.annotations.compiler_flag),namespace,namespace)
        target=namespace[node.name]
    target.__qualname__=original.__qualname__
    return target


def replace(owner, attr, raw, original, descriptor, function):
    replacement = raw.getter(function) if descriptor is property else descriptor(function) if descriptor else function
    setattr(owner,attr,replacement)
    # Only function aliases, not arbitrary class methods with the same name.
    if not inspect.isclass(owner):
        for module in list(sys.modules.values()):
            if module is None:
                continue
            for name,value in list(vars(module).items()):
                if value is original:
                    setattr(module,name,replacement)
    return replacement


def install_mutant(problem, source, *, resolve, raw_descriptor, unwrap, verify, event, active):
    module,owner,attr=resolve(problem)
    raw=raw_descriptor(owner,attr)
    original,descriptor=unwrap(raw,getattr(owner,attr))
    verify(problem,original,descriptor)
    target=compile_mutant(source,original,module)
    @functools.wraps(original)
    def called(*args,**kwargs):
        if active():
            event(kind='mutation_call',mutation_target=problem.get('problem_id'))
        try:
            return target(*args,**kwargs)
        except Exception as exc:
            if active():
                event(kind='mutation_runtime_error',error=type(exc).__name__,mutation_target=problem.get('problem_id'))
            raise
    if inspect.isgeneratorfunction(target):
        @functools.wraps(original)
        def called(*args,**kwargs):
            if active():
                event(kind='mutation_call',mutation_target=problem.get('problem_id'))
            yield from target(*args,**kwargs)
    if inspect.iscoroutinefunction(target):
        @functools.wraps(original)
        async def called(*args,**kwargs):
            if active():
                event(kind='mutation_call',mutation_target=problem.get('problem_id'))
            return await target(*args,**kwargs)
    replace(owner,attr,raw,original,descriptor,called)
    if target.__globals__.get(original.__name__) is original:
        target.__globals__[original.__name__]=called
    return original
