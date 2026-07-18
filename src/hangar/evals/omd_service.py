"""Host-side omd MCP service over HTTP — launch, readiness, teardown (Step 13).

The sandbox (Step 14) must keep omd OUT of the agent's privilege domain: run
as the agent's stdio child inside a container, every file omd can write —
including ``analysis.db``, the provenance DB that is the PRIMARY grading
evidence since Step 11 — would be agent-writable. So omd runs here, on the
host, with its state rooted under a host-only ``data_root``; the agent's
harness receives only ``http://<host>:<port>/mcp``.

Lifecycle is a context manager that never leaks: the server is torn down on
``__exit__`` (SIGTERM, then SIGKILL) even when the body raises, and a startup
failure tears down before raising. Startup is bounded but generous — the
solver-stack import makes a cold boot slow — and polls the endpoint rather
than sleeping blind. Server output goes to ``<data_root>/omd_server.log``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from hangar.evals.drivers.base import MCPServerSpec

_TERM_GRACE_S = 10.0
_POLL_INTERVAL_S = 0.2


def _free_port(host: str) -> int:
    """An OS-assigned free port. Small bind→launch race, harmless per-run."""
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class OmdHttpService:
    """``with OmdHttpService(data_root) as spec:`` — ``spec.url`` is live omd.

    The server binds (and readiness-polls) ``host`` — loopback by default;
    ``host`` is a parameter so Step 14 can bind an interface the colima VM
    reaches. State lands under ``data_root`` via the same ``OMD_*`` env the
    stdio spec uses, so the oracle reads ``analysis.db`` from the identical
    place regardless of transport.
    """

    def __init__(self, data_root: Path, host: str = "127.0.0.1",
                 startup_timeout_s: float = 120.0):
        self.data_root = Path(data_root).resolve()
        self.host = host
        self.startup_timeout_s = startup_timeout_s
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None

    def __enter__(self) -> MCPServerSpec:
        self.data_root.mkdir(parents=True, exist_ok=True)
        port = _free_port(self.host)
        self.url = f"http://{self.host}:{port}/mcp"
        env = {
            **os.environ,
            **MCPServerSpec.omd(self.data_root).env,
            # The server autostarts a range-safety dashboard on a FIXED port
            # (7655): concurrent per-run services would collide on it.
            "RS_DASHBOARD_AUTOSTART": "off",
        }
        log = (self.data_root / "omd_server.log").open("w")
        try:
            # cwd=data_root: any cwd-relative default the server family has
            # (e.g. ./hangar_data) lands in the run's root, not the runner's.
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "hangar.omd.server",
                 "--transport", "http", "--host", self.host, "--port", str(port)],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env,
                cwd=str(self.data_root),
            )
        finally:
            log.close()  # the child holds its own copy of the fd
        self._wait_ready()
        return MCPServerSpec.omd_http(self.url)

    def __exit__(self, *exc) -> bool:
        self._teardown()
        return False

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"omd server exited with code {self.proc.returncode} during "
                    f"startup; see {self.data_root / 'omd_server.log'}"
                )
            try:
                urllib.request.urlopen(self.url, timeout=1.0)
                return
            except urllib.error.HTTPError:
                return  # any HTTP response means the transport is up
            except OSError:
                time.sleep(_POLL_INTERVAL_S)  # not accepting connections yet
        self._teardown()
        raise TimeoutError(
            f"omd server not ready after {self.startup_timeout_s:.0f}s; "
            f"see {self.data_root / 'omd_server.log'}"
        )

    def _teardown(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=_TERM_GRACE_S)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
