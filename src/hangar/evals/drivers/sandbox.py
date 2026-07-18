"""Container sandbox — per-run external workspace + docker-run wrapper (Step 14a).

Closes threat (e) of the §4b model structurally: the agent's writable world is
one scratch **workspace**, created OUTSIDE both repos and mounted as the ONLY
volume; omd state (``data_root``) is never mounted — the agent reaches omd over
HTTP via ``host.docker.internal``, which colima forwards to the host loopback
(verified live 2026-07-18, no ``--add-host`` needed, loopback binds intact).

The workspace root MUST live under ``$HOME``: colima's VM mounts only ``$HOME``
and ``/tmp/colima``, and a bind-mount from anywhere else (e.g. python's default
``/var/folders/...`` temp) appears silently EMPTY in the container — no error.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_ROOT = Path.home() / ".cache" / "hangar-evals" / "workspaces"
CONTAINER_WORKSPACE = "/workspace"
# node:22-slim + @anthropic-ai/claude-code pinned to the host CLI version
# (containers/anchor.Dockerfile). Recorded per-record so image drift is visible.
ANCHOR_IMAGE = "hangar-harness:anchor-2.1.212"


def make_workspace(prefix: str) -> Path:
    """A fresh per-seed workspace under the mountable root.

    Retained after the run (like ``results/run_data``) so the agent's scratch
    files and the driver's debug artifacts stay inspectable; cleanup is manual.
    """
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{prefix}_", dir=str(WORKSPACE_ROOT))).resolve()


@dataclass(frozen=True)
class ContainerSandbox:
    """Renders the ``docker run`` wrapper for one containerized agent run.

    ``env_passthrough`` names host env vars forwarded into the container via
    bare ``-e VAR`` — the docker client reads the VALUE from its own
    environment, so secrets (the Claude Code OAuth token) never appear in argv.
    """

    image: str = ANCHOR_IMAGE
    env_passthrough: tuple[str, ...] = ("CLAUDE_CODE_OAUTH_TOKEN",)

    def wrap_argv(self, inner: list[str], workspace: Path) -> list[str]:
        """``docker run --rm ... <image> <inner>`` — ONLY the workspace mounted."""
        argv = ["docker", "run", "--rm"]
        for var in self.env_passthrough:
            argv += ["-e", var]
        argv += [
            "-v", f"{Path(workspace).resolve()}:{CONTAINER_WORKSPACE}",
            "-w", CONTAINER_WORKSPACE,
            self.image,
            *inner,
        ]
        return argv
