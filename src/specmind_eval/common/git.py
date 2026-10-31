"""Checkout revision verification."""
import subprocess
from pathlib import Path

def verify_commit(repo: Path, expected: str | None):
    if expected:
        actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        if actual != expected:
            raise ValueError(f"Checkout commit mismatch: expected {expected}, got {actual}")

