import sys
import types
import pytest
from specmind_prep.scope.scope import body_line_count
from specmind_prep.mutants.worker import executable_ast
from specmind_eval.evaluate.injected.mutation_runtime import compile_mutant
from specmind_eval.evaluate.evaluator import Evaluator
from specmind_eval.offline.suite import suite_outcome


def test_scope_threshold_and_non_code_lines():
    assert body_line_count('def f():\n    "doc"\n    # comment\n    a=1; b=2\n    c=3\n    return a+b+c\n') == 4
    assert body_line_count('def f():\n    a=1\n    b=2\n    c=3\n    d=4\n    return a\n') == 5
    assert body_line_count('def f():\n    "doc"\n') == 0


def test_non_executable_mutations_are_excluded():
    assert executable_ast('def f(x: int):\n    "old"\n    return x\n') == executable_ast('def f(x: str):\n    "new"\n    return x\n')
    assert executable_ast('def f(x=1):\n    return x\n') != executable_ast('def f(x=2):\n    return x\n')


def test_class_cell_super_preserved():
    class Base:
        def f(self): return 3
    class Child(Base):
        def f(self): return super().f()+1
    module=sys.modules[__name__]
    mutant=compile_mutant('def f(self):\n    return super().f()+2\n', Child.f, module)
    assert mutant(Child()) == 5


@pytest.mark.parametrize('kind', ['plain','property','private'])
def test_callee_patch_assertion_stays_on_root(tmp_path, kind):
    if kind == 'plain':
        source='def g(x):\n    return x+1\ndef f(x):\n    return g(x)\n'
        target='g'; parent=None; mutation='def g(x):\n    return x+2\n'
        root='f'; root_source='def f(x):\n    return g(x)\n'
        test='from target import f\ndef test_f():\n    f(0)\n'
        original='def g(x):\n    return x+1\n'
    else:
        target='g' if kind=='property' else '__g'
        deco='    @property\n' if kind=='property' else ''
        args='self' if kind=='property' else 'self, x'
        expression='self.g' if kind=='property' else 'self.__g(x)'
        source=f'class C:\n{deco}    def {target}({args}):\n        return 1\n    def f(self, x):\n        return {expression}\n'
        parent='C'; root='f'; root_source=f'def f(self, x):\n    return {expression}\n'
        original=('@property\n' if kind=='property' else '')+f'def {target}({args}):\n    return 1\n'
        mutation=f'def {target}({args}):\n    return 2\n'
        test='from target import C\ndef test_f():\n    C().f(0)\n'
    (tmp_path/'target.py').write_text(source)
    (tmp_path/'test_target.py').write_text(test)
    root_id='target.'+('C.' if parent else '')+root
    target_id='target.'+('C.' if parent else '')+target
    problem={'problem_id':root_id,'module':'target','entry_point':root,'parent_name':parent,
             '_expected_source':root_source,'_expected_source_path':str(tmp_path/'target.py')}
    callee={'problem_id':target_id,'module':'target','entry_point':target,'parent_name':parent,
            '_expected_source':original,'_expected_source_path':str(tmp_path/'target.py')}
    ev=Evaluator(problem,[{'test_path':'test_target.py','test_name':'test_f'}],tmp_path,tmp_path/'out',{'pytest_timeout':30,'batch_size':25})
    assert ev.preflight()['status']=='pass'
    mutant={'id':'m','function_source':mutation,'mutation_problem':callee,'mutation_target':target_id}
    row=ev.mutation('assert return_value == 1',[mutant])['mutants'][0]
    assert row['mutation_reached']
    assert row['assertion_killed'] is True
    assert suite_outcome(ev,mutant)['suite_killed'] is False


def test_unreached_callee_not_survived(tmp_path):
    source='def g():\n    return 1\ndef f(x):\n    return x\n'
    (tmp_path/'target.py').write_text(source)
    (tmp_path/'test_target.py').write_text('from target import f\ndef test_f():\n    f(1)\n')
    problem={'problem_id':'target.f','module':'target','entry_point':'f','_expected_source':'def f(x):\n    return x\n','_expected_source_path':str(tmp_path/'target.py')}
    callee=dict(problem,problem_id='target.g',entry_point='g',_expected_source='def g():\n    return 1\n')
    ev=Evaluator(problem,[{'test_path':'test_target.py','test_name':'test_f'}],tmp_path,tmp_path/'out',{'pytest_timeout':30,'batch_size':25})
    assert ev.preflight()['status']=='pass'
    m={'id':'m','function_source':'def g():\n    return 2\n','mutation_problem':callee,'mutation_target':'target.g'}
    row=ev.mutation('assert return_value == 1',[m])['mutants'][0]
    assert not row['mutation_reached']
    assert row['assertion_killed'] is None
    assert suite_outcome(ev,m)['suite_killed'] is None


def test_class_private_reference_and_keyword_signature_preserved():
    class Hidden:
        def __helper(self, __value): return __value+1
        def f(self, x): return self.__helper(x)
    mutant=compile_mutant('def f(self, x):\n    return self.__helper(x)+2\n', Hidden.f, sys.modules[__name__])
    assert mutant(Hidden(), 1)==4
    private=compile_mutant('def __helper(self, __value):\n    return __value+3\n', Hidden._Hidden__helper, sys.modules[__name__])
    assert private(Hidden(), _Hidden__value=1)==4


def test_implicit_static_new_descriptor_compilation():
    class Factory:
        def __new__(cls): return object.__new__(cls)
    mutant=compile_mutant('def __new__(cls):\n    return object.__new__(cls)\n', Factory.__new__,sys.modules[__name__])
    assert isinstance(mutant(Factory),Factory)
