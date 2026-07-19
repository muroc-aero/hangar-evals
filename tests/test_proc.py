"""Tests for the timeout-hardened subprocess helper (Step 18).

Real subprocesses, tiny sleeps: the contract under test is precisely the
process-tree behavior a fake can't exercise — group kill on expiry, partial
output preserved, grandchildren not orphaned.
"""

from __future__ import annotations

import os
import time

import pytest

from hangar.evals.drivers.proc import run_process


def test_completes_and_captures_output():
    out = run_process(["/bin/sh", "-c", "echo hello; echo oops >&2"])
    assert out.returncode == 0
    assert out.stdout == "hello\n"
    assert out.stderr == "oops\n"
    assert out.timed_out is False


def test_nonzero_exit_passes_through():
    out = run_process(["/bin/sh", "-c", "exit 3"])
    assert out.returncode == 3
    assert out.timed_out is False


def test_timeout_kills_and_keeps_partial_output():
    start = time.monotonic()
    out = run_process(["/bin/sh", "-c", "echo partial; sleep 300"], timeout_s=0.5)
    assert time.monotonic() - start < 10   # killed, not waited out
    assert out.timed_out is True
    assert out.returncode is None
    assert out.stdout == "partial\n"       # evidence survives the kill


def test_timeout_kills_the_whole_process_group():
    # The shell backgrounds a grandchild and prints its pid; after expiry the
    # grandchild must be dead too (this is what subprocess.run(timeout=...)
    # gets wrong — it kills only the direct child).
    out = run_process(["/bin/sh", "-c", "sleep 300 & echo $!; wait"], timeout_s=0.5)
    assert out.timed_out is True
    grandchild = int(out.stdout.strip())
    # Killed processes may linger as zombies briefly; poll for disappearance.
    for _ in range(50):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"grandchild {grandchild} survived the group kill")
