import copy
import pytest
from specmind_eval.common.fingerprint import require_source_environment


def test_equal_environment_is_accepted_without_mutation():
    env = {'repo': 'hash', 'python': '3.11.6', 'packages': [['pytest', '8.4.2']]}
    saved = copy.deepcopy(env)
    assert require_source_environment(env, copy.deepcopy(env), 'Dataset') is None
    assert env == saved


@pytest.mark.parametrize('before,after,detail', [
    ([], [['extra', '1']], 'packages added=1, removed=0'),
    ([['extra', '1']], [], 'packages added=0, removed=1'),
    ([['pytest', '8']], [['pytest', '9']], 'packages added=1, removed=1'),
    ([['pip', '1'], ['pip', '1']], [['pip', '1']], 'packages added=0, removed=1'),
])
def test_package_changes_still_block_loading(before, after, detail):
    with pytest.raises(ValueError) as info:
        require_source_environment({'packages': before}, {'packages': after}, 'Prepared dataset')
    assert 'source/environment mismatch' in str(info.value)
    assert detail in str(info.value)
    assert 'do not edit the original manifest' in str(info.value)


def test_repository_change_is_not_hidden_by_matching_packages():
    with pytest.raises(ValueError, match='repo changed'):
        require_source_environment({'repo': 'old', 'packages': []}, {'repo': 'new', 'packages': []}, 'Dataset')
