import json
import pytest
from specmind_prep.line_mutants import messages, filter_response, eligible_lines


def raw(records):
    return '```json\n'+json.dumps(records)+'\n```'


def test_duplicate_lines_are_applied_independently():
    source='def f(x):\n    x = x + 1\n    x = x + 1\n    return x\n'
    response=raw([{'original_content':'    x = x + 1','dedup_key':1,
                   'mutant':['    x = x - 1','    x = x + 2']}])
    result=filter_response(source,response)
    assert len(result['mutants'])==2
    assert result['mutants'][0]['function_source']=='def f(x):\n    x = x + 1\n    x = x - 1\n    return x\n'
    assert result['mutants'][1]['function_source']=='def f(x):\n    x = x + 1\n    x = x + 2\n    return x\n'


def test_invalid_positions_indentation_syntax_and_duplicates():
    source='def f(x):\n    return x + 1\n'
    result=filter_response(source,raw([
        {'original_content':'    return x + 1','dedup_key':0,'mutant':[
            '    return x - 1','    return x - 1','    return x + 1','   return x - 1','    return (','    return x\n    return 2']},
        {'original_content':'    return x + 1','dedup_key':1,'mutant':['    return x']},
        {'original_content':'    return x + 1','dedup_key':-1,'mutant':['    return x']},
        {'original_content':'    return x + 1','dedup_key':True,'mutant':['    return x']},
    ]))
    assert result['discovered']==9
    assert len(result['mutants'])==1 and len(result['skipped'])==8
    assert result['mutants'][0]['line_record']['source_line']==2


def test_docstring_nested_function_and_header_not_mutated():
    source='def f(\n    x=1,\n):\n    """Description.\n    More.\n    """\n    def inner():\n        return x\n    return x\n'
    assert eligible_lines(source)=={9}
    result=filter_response(source,raw([
        {'original_content':'    x=1,','dedup_key':0,'mutant':['    x=2,']},
        {'original_content':'    More.','dedup_key':0,'mutant':['    Other.']},
        {'original_content':'        return x','dedup_key':0,'mutant':['        return 0']},
        {'original_content':'    return x','dedup_key':0,'mutant':['    return 0']},
    ]))
    assert len(result['mutants'])==1 and len(result['skipped'])==3


def test_prompt_adapts_problem_without_tests_or_assertions():
    prompt=messages({'source':'def f(x): return x','document':'API description',
                     'tests':'SECRET_TEST','assertion':'SECRET_ASSERT'})
    assert 'about 5' in prompt[0]['content']
    assert 'API description' in prompt[1]['content']
    assert 'SECRET' not in str(prompt)
    assert 'LeetCode' not in prompt[0]['content']


def test_schema_must_be_array():
    with pytest.raises(ValueError,match='array'):
        filter_response('def f(): pass','{}')
