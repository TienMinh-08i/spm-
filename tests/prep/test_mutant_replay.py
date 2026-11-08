import pytest
from specmind_prep.mutant_replay import classify


@pytest.mark.parametrize('status,calls,events,expected', [
    ('pass', 1, [], 'neutral_pass'),
    ('fail', 1, [], 'test_failed_after_mutation'),
    ('pass', 0, [], 'not_reached'),
    ('error', 0, [{'kind': 'hook_install_error'}], 'installation_error'),
    ('timeout', 1, [], 'timeout'),
])
def test_neutral_replay_does_not_infer_assertion_kills(status, calls, events, expected):
    arm = {'status': status, 'detail': {'events': events}, 'cases': {'test': {'mutation_calls': calls}}}
    assert classify(arm) == expected
