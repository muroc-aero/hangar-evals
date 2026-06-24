"""Claude Agent-SDK driver — the frontier anchor.

A faithful port of ``packages/omd/examples/agent_eval/eval_lane_c.py``'s
``run_agent``, behind the ``AgentDriver`` interface. The agent is restricted to
the MCP tools (no filesystem, shell, or web), runs the task, and returns its
final message plus cost and wall-clock.

The ``claude-agent-sdk`` package is an optional dependency (``[anchor]`` extra)
and is imported lazily inside ``run`` so the rest of hangar-evals stays
importable without it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from hangar.evals.drivers.base import AgentResult, MCPServerSpec
from hangar.evals.hangar_ref import resolve_hangar_repo

# Tools the anchor is never allowed to use: it must work through MCP alone, so
# its tool-use is comparable to a local model with the same restriction.
_DISALLOWED_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "NotebookEdit",
]


class ClaudeAgentSDKDriver:
    """Drive an agent via the Claude Agent SDK against an MCP server."""

    def run(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        data_root: Path,
        model: str | None = None,
        max_turns: int = 80,
        cwd: Path | None = None,
    ) -> AgentResult:
        cwd = cwd or resolve_hangar_repo()
        start = time.monotonic()
        final_text, cost = asyncio.run(
            self._run_async(prompt, mcp, model, max_turns, cwd)
        )
        return AgentResult(
            final_text=final_text,
            cost_usd=cost,
            wall_clock_s=time.monotonic() - start,
        )

    async def _run_async(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        model: str | None,
        max_turns: int,
        cwd: Path,
    ) -> tuple[str, float | None]:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                TextBlock,
                query,
            )
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Install the anchor extra:\n"
                "  uv pip install -e '.[anchor]'   (or pip install claude-agent-sdk)"
            ) from exc

        options = ClaudeAgentOptions(
            cwd=str(cwd),
            model=model,
            max_turns=max_turns,
            permission_mode="bypassPermissions",
            mcp_servers={
                mcp.name: {
                    "type": "stdio",
                    "command": mcp.command,
                    "args": mcp.args,
                    "env": mcp.env,
                },
            },
            allowed_tools=[f"mcp__{mcp.name}"],
            disallowed_tools=_DISALLOWED_TOOLS,
        )

        final_text: str = ""
        cost: float | None = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final_text = block.text
            elif isinstance(message, ResultMessage):
                if message.result:
                    final_text = message.result
                cost = message.total_cost_usd
        return final_text, cost
