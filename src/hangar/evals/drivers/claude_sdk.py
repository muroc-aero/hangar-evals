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
from hangar.evals.trace import HALLUCINATED_CODE, ToolCall, parse_omd_error_code

# Tools the anchor is never allowed to use: it must work through MCP alone, so
# its tool-use is comparable to a local model with the same restriction.
_DISALLOWED_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "NotebookEdit",
]


def _normalize_tool_name(name: str, server: str) -> str:
    """Strip the SDK's ``mcp__<server>__`` prefix to the bare tool name."""
    prefix = f"mcp__{server}__"
    return name[len(prefix):] if name.startswith(prefix) else name


def _result_text(content) -> str | None:
    """Flatten a ToolResultBlock's content (str | list of blocks) to text."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(getattr(item, "text", "")))
        return "".join(parts)
    return str(content)


def _classify_tool_result(is_error: bool, content) -> tuple[bool, str | None]:
    """Map an SDK tool result to (ok, error_code).

    Prefers the omd error envelope's ``error.code``; falls back to a
    hallucinated-tool heuristic, then a generic ERROR.
    """
    if not is_error:
        return True, None
    text = _result_text(content)
    code = parse_omd_error_code(text)
    if code:
        return False, code
    low = (text or "").lower()
    if "tool" in low and any(s in low for s in ("not found", "no such", "unknown", "not available")):
        return False, HALLUCINATED_CODE
    return False, "ERROR"


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
        final_text, cost, trace = asyncio.run(
            self._run_async(prompt, mcp, model, max_turns, cwd)
        )
        return AgentResult(
            final_text=final_text,
            cost_usd=cost,
            wall_clock_s=time.monotonic() - start,
            tool_call_trace=trace,
        )

    async def _run_async(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        model: str | None,
        max_turns: int,
        cwd: Path,
    ) -> tuple[str, float | None, list[ToolCall]]:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                TextBlock,
                ToolResultBlock,
                ToolUseBlock,
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
        pending: dict[str, str] = {}   # tool_use_id -> bare tool name
        trace: list[ToolCall] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final_text = block.text
                    elif isinstance(block, ToolUseBlock):
                        pending[block.id] = _normalize_tool_name(block.name, mcp.name)
            elif isinstance(message, ResultMessage):
                if message.result:
                    final_text = message.result
                cost = message.total_cost_usd
            else:
                # Tool results arrive on the following (user) message's content.
                for block in getattr(message, "content", None) or []:
                    if isinstance(block, ToolResultBlock):
                        tool = pending.get(block.tool_use_id, "<unknown>")
                        ok, code = _classify_tool_result(
                            bool(getattr(block, "is_error", False)), block.content
                        )
                        trace.append(ToolCall(tool=tool, ok=ok, error_code=code))
        return final_text, cost, trace
