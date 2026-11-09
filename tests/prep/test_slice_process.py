"""Small real processes verify slicing limits without running a dataset."""
import json
import os
import sys

import pytest

from specmind_prep.slice_process import run_bounded
from specmind_prep.injected.slice_trace_plugin import write_bounded_result


def run(tmp_path, source, *, budget=4096, timeout=5):
    return run_bounded(
        [sys.executable, "-u", "-c", source], cwd=tmp_path, env=os.environ.copy(),
        log_path=tmp_path / "run.log", result_path=tmp_path / "result.json",
        timeout=timeout, max_bytes=budget,
    )


@pytest.mark.parametrize("size", [0, 127, 128, 129, 6000])
def test_log_prefix_is_bounded_even_when_process_exits_quickly(tmp_path, size):
    result = run(tmp_path, f"import os;os.write(1,b'x'*{size})", budget=128)
    assert (tmp_path / "run.log").read_bytes() == b"x" * min(size, 128)
    assert result.get("output_budget_exceeded", False) == (size > 128)
    if size <= 128:
        assert result == {"exitcode": 0}


def test_output_budget_stops_a_still_running_process(tmp_path):
    result = run(tmp_path, "import os,time;os.write(2,b'x'*1024);time.sleep(30)", budget=128)
    assert result.get("output_budget_exceeded")
    assert not result.get("timeout")
    assert (tmp_path / "run.log").stat().st_size == 128


def test_timeout_stops_a_silent_process(tmp_path):
    result = run(tmp_path, "import time;time.sleep(30)", timeout=0.2)
    assert result.get("timeout")
    assert not result.get("output_budget_exceeded")


def test_result_monitor_rejects_oversized_output(tmp_path):
    result = run(tmp_path, "from pathlib import Path;import time;Path('result.json').write_bytes(b'x'*4096);time.sleep(30)", budget=256)
    assert result.get("output_budget_exceeded")


def test_result_writer_returns_a_small_incomplete_marker(tmp_path):
    path = tmp_path / "result.json"
    write_bounded_result(path, {"mode": "baseline", "exitcode": 0, "output": "é" * 10000}, 256)
    assert path.stat().st_size <= 256
    assert json.loads(path.read_text()) == {
        "mode": "baseline", "exitcode": 0, "missing_result": True, "output_budget_exceeded": True,
    }


def test_result_writer_keeps_normal_unicode_payload_and_refuses_overwrite(tmp_path):
    path = tmp_path / "result.json"
    payload = {"mode": "instrumented", "exitcode": 1, "output": "tiếng Việt"}
    write_bounded_result(path, payload, 4096)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    with pytest.raises(FileExistsError):
        write_bounded_result(path, {}, 4096)
