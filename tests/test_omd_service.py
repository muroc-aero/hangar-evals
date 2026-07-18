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


def test_service_lifecycle_two_concurrent(tmp_path):
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    svc_a = OmdHttpService(a_root)
    svc_b = OmdHttpService(b_root)
    with svc_a as spec_a, svc_b as spec_b:
        # Distinct OS-assigned ports — no collision between concurrent seeds.
        assert spec_a.url != spec_b.url
        assert spec_a.transport == "http"
        assert spec_a.url.startswith("http://127.0.0.1:")
        assert _http_alive(spec_a.url) and _http_alive(spec_b.url)
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
