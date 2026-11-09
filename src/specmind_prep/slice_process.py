"""Bounded output for the one-test slicing subprocess, not a general sandbox."""
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


def _stop_tree(proc):
    if os.name == "nt":
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def run_bounded(command, *, cwd, env, log_path, result_path, timeout, max_bytes):
    """Keep at most max_bytes of stdout/stderr; drain while the process runs.

    The plugin also bounds its JSON writer. Monitoring the result here catches
    an oversized replacement. This does not limit arbitrary files written by tests.
    """
    result_path = Path(result_path)
    exceeded, drained = threading.Event(), threading.Event()
    errors = []
    reason = None
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {
        "start_new_session": True
    }
    deadline = time.monotonic() + timeout
    with Path(log_path).open("xb") as log:
        proc = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, **options,
        )

        def drain():
            remaining = max_bytes
            try:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    if len(chunk) > remaining:
                        log.write(chunk[:remaining])
                        exceeded.set()
                        break
                    log.write(chunk)
                    remaining -= len(chunk)
            except (OSError, ValueError) as exc:
                errors.append(type(exc).__name__)
            finally:
                drained.set()

        reader = threading.Thread(target=drain, name="slice-output", daemon=True)
        reader.start()
        try:
            while True:
                if exceeded.is_set():
                    reason = "output_budget_exceeded"
                    break
                if result_path.exists() and result_path.stat().st_size > 3 * max_bytes:
                    reason = "output_budget_exceeded"
                    break
                if errors:
                    reason = "output_read_error"
                    break
                if drained.is_set() and proc.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    reason = "timeout"
                    break
                exceeded.wait(0.01)
        finally:
            if reason is not None or proc.poll() is None:
                _stop_tree(proc)
            reader.join(timeout=2)
            proc.stdout.close()
        if reader.is_alive():
            reason = "output_read_error"
        elif reason is None and exceeded.is_set():
            reason = "output_budget_exceeded"
        elif reason is None and errors:
            reason = "output_read_error"
        if reason:
            return {reason: True, "exitcode": proc.returncode}
        return {"exitcode": proc.returncode}
