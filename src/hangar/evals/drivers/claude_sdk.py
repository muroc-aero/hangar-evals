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

# Tool restriction here is an INTERIM contamination guard, not the final design.
#
# The eval's threat model is *test-set contamination*: the agent must not reach
# privileged context (the-hangar source, the eval scoring code, the Lane-A
# reference answers, or hangar/omd-specific skills/memory). It is NOT about
# forcing the agent through MCP alone — rich file/shell tools are a legitimate
# harness affordance that SHOULD be available once a filesystem sandbox isolates
# the workspace. Two groups, blocked for different reasons:
#
# (1) Filesystem/shell — blocked ONLY because the agent's cwd is currently the
#     the-hangar repo itself (see resolve_hangar_repo), which holds the solver
#     source, scoring code, and reference answers. Under a sandbox with a clean
#     scratch workspace these should be RE-ALLOWED.
_INTERIM_FILESYSTEM_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit", "Task",
]
# (2) Privileged-context / external-knowledge — blocked even under a sandbox,
#     because a filesystem sandbox would NOT stop them (they ride in through the
#     harness or the network): Skill injects privileged procedural knowledge
#     (possibly hangar/omd-specific); WebSearch/WebFetch pull external knowledge
#     (a future "web-allowed" track may re-enable these). ToolSearch is
#     deliberately NOT blocked — it only discovers tools the agent is already
#     permitted to use, leaking no privileged context.
_CONTAMINATION_TOOLS = ["Skill", "WebSearch", "WebFetch"]
_DISALLOWED_TOOLS = _INTERIM_FILESYSTEM_TOOLS + _CONTAMINATION_TOOLS


# SDK usage-key names -> the normalized token shape shared with OpenCode.
_USAGE_KEY_MAP = {"input_tokens": "input", "output_tokens": "output"}


def _merge_usage(acc: dict, usage) -> None:
    """Sum one per-message usage dict into ``acc`` (Step 18 cost fallback).

    The SDK reports canonical usage only on the terminal ``ResultMessage``; a
    timed-out or crashed run never gets one, so numeric top-level keys are
    accumulated per message as a fallback. Summing per-call usage matches the
    billed total (each API call bills its own input, cached context included).
    Non-numeric values are dropped, never coerced.
    """
    if not isinstance(usage, dict):
        return
    for key, val in usage.items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            acc[key] = acc.get(key, 0) + val


def _normalize_usage(usage) -> dict | None:
    """``ResultMessage.usage`` -> normalized ``{"input", "output", ...}``.

    Defensive by design: usage field names may drift across claude-agent-sdk
    versions (the manifest's environment block records the SDK version so drift
    is diagnosable), so unknown keys pass through untouched and anything that
    isn't a dict yields None (None != 0 in the record).
    """
    if not isinstance(usage, dict) or not usage:
        return None
    return {_USAGE_KEY_MAP.get(key, key): val for key, val in usage.items()}


def _render_mcp_server(mcp: MCPServerSpec) -> dict:
    """MCPServerSpec -> the SDK's mcp_servers entry.

    The http form (Step 13) is url-only by construction — the streamable-HTTP
    endpoint a host-side omd service exposes; no filesystem path reaches the
    agent's config.
    """
    if mcp.transport == "http":
        return {"type": "http", "url": mcp.url}
    return {"type": "stdio", "command": mcp.command,
            "args": mcp.args, "env": mcp.env}


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


class _StreamState:
    """Mutable accumulator for one SDK message stream.

    Filled in-place as messages arrive so that a wall-clock timeout, which
    cancels the consuming task mid-stream (Step 18), still leaves everything
    seen so far — partial final text, trace, and the per-message usage sums —
    readable by the caller.
    """

    def __init__(self):
        self.final_text: str = ""
        self.cost: float | None = None
        self.num_turns: int | None = None
        self.usage: dict | None = None      # canonical, from ResultMessage
        self.usage_acc: dict = {}           # per-message fallback sums
        self.pending: dict[str, str] = {}   # tool_use_id -> bare tool name
        self.trace: list[ToolCall] = []
        self.timed_out: bool = False


class ClaudeAgentSDKDriver:
    """Drive an agent via the Claude Agent SDK against an MCP server."""

    # Grace period for closing the SDK stream (and its CLI child) after a
    # timeout; asyncio.run's shutdown_asyncgens is the backstop beyond it.
    _CLOSE_TIMEOUT_S = 15.0

    def run(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        data_root: Path,
        model: str | None = None,
        max_turns: int = 80,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> AgentResult:
        cwd = cwd or resolve_hangar_repo()
        start = time.monotonic()
        state = asyncio.run(
            self._run_async(prompt, mcp, model, max_turns, cwd, timeout_s)
        )
        return AgentResult(
            final_text=state.final_text,
            cost_usd=state.cost,
            wall_clock_s=time.monotonic() - start,
            num_turns=state.num_turns,
            tool_call_trace=state.trace,
            # Canonical ResultMessage usage when the run finished; the
            # per-message accumulation when it didn't (cost stays None then —
            # the SDK prices only at result delivery; null is honest).
            tokens=_normalize_usage(state.usage or state.usage_acc),
            timed_out=state.timed_out,
        )

    async def _run_async(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        model: str | None,
        max_turns: int,
        cwd: Path,
        timeout_s: float | None,
    ) -> _StreamState:
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
            # Starve ambient context: do NOT load CLAUDE.md / user|project memory
            # / settings from disk. They may carry hangar/omd discussion or prior
            # task formulations (test-set contamination) that a filesystem
            # sandbox would not stop — the harness injects them. The task comes
            # from the prompt alone.
            setting_sources=[],
            mcp_servers={mcp.name: _render_mcp_server(mcp)},
            allowed_tools=[f"mcp__{mcp.name}"],
            disallowed_tools=_DISALLOWED_TOOLS,
        )

        sdk_types = (AssistantMessage, ResultMessage, TextBlock,
                     ToolResultBlock, ToolUseBlock)
        state = _StreamState()
        stream = query(prompt=prompt, options=options)
        try:
            await asyncio.wait_for(
                self._consume(stream, mcp.name, state, sdk_types), timeout_s)
        except asyncio.TimeoutError:
            state.timed_out = True
            # Close the stream so the SDK tears down its CLI subprocess;
            # bounded, because a transport wedged enough to time out may also
            # wedge on close.
            try:
                await asyncio.wait_for(stream.aclose(), self._CLOSE_TIMEOUT_S)
            except Exception:
                pass
        return state

    async def _consume(self, stream, server: str, state: _StreamState, sdk_types):
        (AssistantMessage, ResultMessage, TextBlock,
         ToolResultBlock, ToolUseBlock) = sdk_types
        async for message in stream:
            # Fallback usage accumulation (Step 18): whatever usage any
            # non-terminal message carries, summed as we go, so a killed run
            # still reports tokens. ResultMessage's canonical total wins below.
            if not isinstance(message, ResultMessage):
                _merge_usage(state.usage_acc, getattr(message, "usage", None))
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        state.final_text = block.text
                    elif isinstance(block, ToolUseBlock):
                        state.pending[block.id] = _normalize_tool_name(
                            block.name, server)
            elif isinstance(message, ResultMessage):
                if message.result:
                    state.final_text = message.result
                state.cost = message.total_cost_usd
                state.num_turns = getattr(message, "num_turns", None)
                state.usage = getattr(message, "usage", None)
            else:
                # Tool results arrive on the following (user) message's content.
                for block in getattr(message, "content", None) or []:
                    if isinstance(block, ToolResultBlock):
                        tool = state.pending.get(block.tool_use_id, "<unknown>")
                        ok, code = _classify_tool_result(
                            bool(getattr(block, "is_error", False)), block.content
                        )
                        state.trace.append(
                            ToolCall(tool=tool, ok=ok, error_code=code))
