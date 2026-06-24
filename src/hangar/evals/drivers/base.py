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
    """A stdio MCP server an agent connects to.

    Harness-neutral: the Claude SDK consumes the fields directly; the OpenCode
    driver (later) renders them into ``opencode.json``. ``MCPServerSpec.omd``
    builds the omd server wiring used across the suite.
    """

    name: str
    command: str
    args: list[str]
    env: dict[str, str]

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
            },
        )


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
    ) -> AgentResult:
        ...
