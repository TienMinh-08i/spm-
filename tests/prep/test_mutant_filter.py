import pytest
from specmind_prep.mutant_filter import classify


def arm(status='pass', checks=1, target_errors=0, reports=None):
    return {'status':status,'detail':{'events':[]},'cases':{'test':{
        'checks':checks,'target_errors':target_errors,'mutation_calls':1,
        'mutation_errors':0,'runtime_errors':0,'violations':0,
        'reports':reports or {'setup':'passed','call':'passed','teardown':'passed'}}}}


def test_keeps_test_killed_mutant_when_postcondition_reached():
    a=arm('fail',reports={'setup':'passed','call':'failed','teardown':'passed'})
    assert classify(a,['test'])=='keep'


def test_exception_without_postcondition_is_excluded():
    assert classify(arm('fail',checks=0,target_errors=1),['test'])=='target_exception_before_assertion'


@pytest.mark.parametrize('status,reason',[('timeout','timeout'),('collection_error','collection_error'),('error','execution_error')])
def test_incomplete_run_is_not_kept(status,reason):
    assert classify(arm(status),['test'])==reason


def test_requires_all_selected_tests_and_teardown():
    assert classify(arm(),['test','missing'])=='incomplete_test_execution'
    a=arm();a['cases']['test']['reports']['teardown']='failed'
    assert classify(a,['test'])=='incomplete_test_execution'


def test_requires_mutation_reach_and_no_harness_errors():
    a=arm();a['cases']['test']['mutation_calls']=0
    assert classify(a,['test'])=='mutation_not_reached'
    a=arm();a['cases']['test']['runtime_errors']=1
    assert classify(a,['test'])=='instrumentation_error'
