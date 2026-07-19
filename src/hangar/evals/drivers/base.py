"""Driver interface: AgentDriver, MCPServerSpec, AgentResult.

This module is pure — it imports no harness or model SDK, so it is always
importable (the concrete drivers lazy-import their heavy deps). Everything an
agent run needs to be described and reported flows through these three types.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class MCPServerSpec:
    """An MCP server an agent connects to — stdio child or remote HTTP.

    Harness-neutral: the Claude SDK consumes the fields directly; the OpenCode
    driver renders them into ``opencode.json``. ``MCPServerSpec.omd`` builds
    the stdio omd wiring used across the suite; ``MCPServerSpec.omd_http``
    the client side of a host-run HTTP omd service (Step 13).
    """

    name: str
    command: str
    args: list[str]
    env: dict[str, str]
    transport: str = "stdio"      # "stdio" | "http"
    url: str | None = None        # set iff transport == "http"

    @classmethod
    def omd(cls, data_root: Path) -> "MCPServerSpec":
        """The omd stdio server, with all state rooted under ``data_root``.

        Mirrors ``eval_lane_c.py``: ``<python> -m hangar.omd.server`` with the
        four ``OMD_*`` paths the server reads. ``data_root`` is created by the
        caller (one temp root per run).

        Paths are made absolute: the MCP server subprocess inherits the
        harness's cwd (OpenCode runs it under ``--dir``), so a relative
        ``OMD_DB_PATH`` would resolve under that cwd and nest — the DB would
        land somewhere the scorer never looks.
        """
        data_root = Path(data_root).resolve()
        return cls(
            name="omd",
            command=sys.executable,
            args=["-m", "hangar.omd.server"],
            env={
                "OMD_DATA_ROOT": str(data_root / "omd_data"),
                "OMD_DB_PATH": str(data_root / "analysis.db"),
                "OMD_PLAN_STORE": str(data_root / "plans"),
                "OMD_RECORDINGS_DIR": str(data_root / "recordings"),
                # sdk-level state (artifact/study stores, session provenance)
                # defaults to ./hangar_data RELATIVE TO THE SERVER'S CWD —
                # pin it under data_root so no run scatters state into
                # whatever directory the server happened to start in.
                "HANGAR_DATA_DIR": str(data_root / "hangar_data"),
            },
        )

    @classmethod
    def omd_http(cls, url: str) -> "MCPServerSpec":
        """The client side of a HOST-run omd HTTP service (Step 13).

        Carries ONLY the URL. That absence is the contamination property the
        sandbox (Step 14) relies on: no filesystem path — the ``OMD_*`` roots,
        ``sys.executable``, the-hangar — crosses the channel into the agent's
        rendered config, and the server's state stays outside the agent's
        privilege domain (see ``omd_service.OmdHttpService``).
        """
        return cls(name="omd", command="", args=[], env={},
                   transport="http", url=url)


@dataclass
class AgentResult:
    """The outcome of one agent run.

    ``final_text`` is the agent's last message (the scorer parses the fenced
    JSON report from it in a later step). ``tool_call_trace`` and ``num_turns``
    are left unset for now — tool-use metrics come from the omd provenance DB
    in Step 5, not the harness self-report.
    """

    final_text: str
    cost_usd: float | None = None
    wall_clock_s: float | None = None
    num_turns: int | None = None
    tool_call_trace: list | None = field(default=None)
    # Normalized token counts: {"input": int, "output": int, ...} — extra
    # provider keys pass through. None means the harness reported nothing
    # (None != 0; values are never invented).
    tokens: dict | None = None
    # The run hit its wall-clock budget and was killed (Step 18). Everything
    # above is then PARTIAL — whatever the harness emitted before expiry —
    # but the record still grades: the effect oracle reads the provenance DB,
    # not this result.
    timed_out: bool = False


class AgentDriver(Protocol):
    """Run an agent against a task and report the result.

    Synchronous by contract: async harnesses (the Claude SDK) wrap their event
    loop internally so the runner and subprocess-based drivers stay sync.
    """

    def run(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        data_root: Path,
        model: str | None = None,
        max_turns: int = 80,
        timeout_s: float | None = None,
    ) -> AgentResult:
        ...
