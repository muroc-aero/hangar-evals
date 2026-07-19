"""Timeout-hardened subprocess execution for the CLI drivers (Step 18).

``subprocess.run(timeout=...)`` kills only the direct child; the harness CLIs
spawn trees (opencode -> MCP server child; docker client -> nothing it owns),
so expiry must kill the whole process group and still hand back whatever the
child wrote — a timed-out run's partial event stream is real evidence (the
tool trace and report may be complete even when the harness hangs at exit).

Docker is the one tree this cannot reach: SIGKILL on the docker CLI leaves the
container running. Drivers that wrap in docker pass a ``--name`` and issue
``docker kill`` themselves after a timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcOutcome:
    """What one harness subprocess produced, timeout or not."""

    returncode: int | None      # None when the run was killed on timeout
    stdout: str
    stderr: str
    timed_out: bool


def run_process(
    argv: list[str],
    timeout_s: float | None = None,
    cwd: str | None = None,
) -> ProcOutcome:
    """Run ``argv`` to completion or ``timeout_s``, killing its process group.

    Always ``stdin=DEVNULL`` (the OpenCode hang fix — see the driver) and
    ``start_new_session=True`` so the child leads its own process group and
    SIGKILL reaches grandchildren. On timeout the partial stdout/stderr are
    drained and returned rather than discarded.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return ProcOutcome(proc.returncode, out, err, timed_out=False)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)   # pid == pgid (new session)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        return ProcOutcome(None, out or "", err or "", timed_out=True)
