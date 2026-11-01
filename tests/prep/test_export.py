import pytest

from specmind_prep.mutants.mutmut_export import parse_mutmut_result_ids, remove_workspace
from specmind_eval.common.storage import run_lock


def test_result_ids_parser_accepts_ranges_and_ignores_headings():
    assert parse_mutmut_result_ids("Killed 99\n1-3, 7\n7, 10\n") == ["1", "2", "3", "7", "10"]


def test_cleanup_cannot_remove_root_or_escape(tmp_path):
    with pytest.raises(ValueError):
        remove_workspace(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        remove_workspace(tmp_path.parent, tmp_path)


def test_run_lock_released_after_exception(tmp_path):
    with pytest.raises(RuntimeError, match="test interruption"):
        with run_lock(tmp_path):
            raise RuntimeError("test interruption")
    with run_lock(tmp_path):
        pass
