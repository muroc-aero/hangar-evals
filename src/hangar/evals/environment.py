"""Environment capture for the run manifest (Step 12) — best-effort, never fatal.

``capture_environment`` snapshots everything a reader needs to interpret (or
distrust) a result later: both repos' git SHAs + dirty flags, the Python and
platform versions, and the versions of the three external moving parts
(claude-agent-sdk, the opencode CLI, the ollama CLI). It is OBSERVED, not
configured — the block lives in the manifest beside ``RunConfig``, and
reproduction *compares* it rather than replaying it, so it is deliberately not
part of the config.

Every field degrades independently to ``"unavailable"`` — a missing CLI, a
non-git checkout, or a hung subprocess must never take a run down with it.
"""

from __future__ import annotations

import platform
import subprocess
from importlib import metadata
from pathlib import Path

from hangar.evals.hangar_ref import resolve_hangar_repo

UNAVAILABLE = "unavailable"

# This file is src/hangar/evals/environment.py; parents[3] is the repo root in
# an editable install (the only place a git SHA exists to capture anyway).
_REPO_ROOT = Path(__file__).resolve().parents[3]

_CLI_TIMEOUT_S = 10


def _git_info(repo: Path) -> dict | str:
    """``{"sha": ..., "dirty": ...}`` for a checkout, or ``"unavailable"``."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT_S,
        )
        if sha.returncode != 0:
            return UNAVAILABLE
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT_S,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return {"sha": sha.stdout.strip(), "dirty": dirty}
    except Exception:
        return UNAVAILABLE


def _package_version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except Exception:
        return UNAVAILABLE


def _cli_version(*argv: str) -> str:
    """First line of a CLI's ``--version`` output, or ``"unavailable"``."""
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True,
            timeout=_CLI_TIMEOUT_S, stdin=subprocess.DEVNULL,
        )
        out = (proc.stdout or proc.stderr).strip()
        if proc.returncode != 0 or not out:
            return UNAVAILABLE
        return out.splitlines()[0]
    except Exception:
        return UNAVAILABLE


def capture_environment() -> dict:
    """Snapshot the run environment. Best-effort on every field; never raises."""
    try:
        hangar = _git_info(resolve_hangar_repo())
    except Exception:
        hangar = UNAVAILABLE
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hangar_evals": _git_info(_REPO_ROOT),
        "the_hangar": hangar,
        "claude_agent_sdk": _package_version("claude-agent-sdk"),
        "opencode": _cli_version("opencode", "--version"),
        "ollama": _cli_version("ollama", "--version"),
    }
