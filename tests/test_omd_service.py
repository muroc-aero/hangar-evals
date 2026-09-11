"""Step 13: host-side omd over HTTP — spec shape, service lifecycle, teardown.

The lifecycle test launches the REAL omd server twice (the test env needs
the-hangar importable anyway) and costs a few seconds of solver-stack import —
kept in the default suite because Step 14's sandbox depends on this exact
launch incantation staying alive. The crash-path test fakes the process so the
failure contract (raise, point at the log, leak nothing) is pinned offline.
"""

from __future__ import annotations

import subprocess
import urllib.error
import urllib.request

import pytest

from hangar.evals.drivers.base import MCPServerSpec
from hangar.evals.omd_service import OmdHttpService

URL = "http://127.0.0.1:9999/mcp"


def test_omd_http_spec_is_url_only():
    # The contamination property itself: nothing but the URL crosses the
    # channel — no OMD_* path, no sys.executable, no the-hangar path.
    spec = MCPServerSpec.omd_http(URL)
    assert spec.transport == "http"
    assert spec.url == URL
    assert spec.command == "" and spec.args == [] and spec.env == {}


def test_stdio_spec_keeps_default_transport(tmp_path):
    spec = MCPServerSpec.omd(tmp_path)
    assert spec.transport == "stdio"
    assert spec.url is None


def _http_alive(url: str) -> bool:
    """True if ANYTHING HTTP answers at url (an error status still counts)."""
    try:
        urllib.request.urlopen(url, timeout=2.0)
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def _http_status(url: str, host_header: str) -> int:
    """Status code for a GET carrying an explicit Host header."""
    req = urllib.request.Request(url, headers={"Host": host_header})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_service_lifecycle_two_concurrent(tmp_path):
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    # svc_b takes the sandbox path (Step 14a): the spec ADVERTISES the
    # container-facing name while the bind (and readiness poll) stays loopback.
    svc_a = OmdHttpService(a_root)
    svc_b = OmdHttpService(b_root, advertise_host="host.docker.internal")
    with svc_a as spec_a, svc_b as spec_b:
        # Distinct OS-assigned ports — no collision between concurrent seeds.
        assert spec_a.url != spec_b.url
        assert spec_a.transport == "http"
        assert spec_a.url.startswith("http://127.0.0.1:")
        assert spec_b.url.startswith("http://host.docker.internal:")
        # Aliveness is checked on the loopback poll URL — the advertised name
        # only resolves inside a container.
        assert _http_alive(spec_a.url) and _http_alive(svc_b._poll_url)
        # The 421 lesson (first live smoke, 2026-07-18): reachability is NOT
        # acceptance. FastMCP's DNS-rebinding guard 421s any Host header it
        # wasn't told about, and the MCP client then hangs at "pending". The
        # advertised name must be admitted on svc_b — while svc_a, which
        # advertises nothing, must still reject it (the guard stays on).
        port_b = svc_b._poll_url.rsplit(":", 1)[1].split("/")[0]
        assert _http_status(svc_b._poll_url, f"host.docker.internal:{port_b}") != 421
        assert _http_status(spec_a.url, "host.docker.internal:9999") == 421
        # State rooted host-side under each run's data_root (the oracle's
        # read path), created at server startup by init_analysis_db().
        assert (a_root / "analysis.db").exists()
        assert (b_root / "analysis.db").exists()
        assert (a_root / "omd_server.log").exists()
    # Teardown: both processes gone, nothing orphaned.
    assert svc_a.proc.poll() is not None
    assert svc_b.proc.poll() is not None


def test_startup_crash_raises_and_points_at_log(tmp_path, monkeypatch):
    class _DeadProc:
        returncode = 3

        def poll(self):
            return 3

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _DeadProc())
    with pytest.raises(RuntimeError, match="omd_server.log"):
        OmdHttpService(tmp_path).__enter__()


def test_teardown_kills_the_children_the_server_spawned(tmp_path):
    """A seed's wall-clock budget must bound the SEED, not just the agent.

    It used to bound only the agent: the driver killed the container, but omd
    kept solving host-side because the solve runs in a child that the parent's
    SIGTERM never reached. An orphaned OpenMDAO solve burns CPU long after the
    seed that asked for it is gone -- pyc_turbojet took 2h26m of wall clock for
    roughly 45 minutes of agent time that way.

    Stands in a sleeping grandchild for the solve: if teardown only reaches the
    parent, it survives.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    from hangar.evals.omd_service import OmdHttpService

    marker = tmp_path / "child.pid"
    # A parent that spawns a long-lived child and then waits, in its own group.
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time, pathlib;"
         "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
         f"pathlib.Path({str(marker)!r}).write_text(str(c.pid));"
         "time.sleep(120)"],
        start_new_session=True)
    try:
        for _ in range(100):          # wait for the child's pid to be published
            if marker.exists():
                break
            time.sleep(0.05)
        child_pid = int(marker.read_text())

        service = OmdHttpService.__new__(OmdHttpService)   # teardown only
        service.proc = parent
        service._teardown()

        time.sleep(0.5)
        with pytest.raises(OSError):   # ESRCH: the grandchild went too
            os.kill(child_pid, 0)
    finally:
        for pid in (parent.pid,):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
