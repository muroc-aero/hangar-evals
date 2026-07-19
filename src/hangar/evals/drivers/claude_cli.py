"""Claude headless-CLI driver — the anchor IN the sandbox container (Step 14a).

Runs ``claude -p`` inside the container over the CLI's stream-json protocol —
the same message stream the claude-agent-sdk wraps — so the parsed
``AgentResult`` keeps the exact shape of the SDK driver's (final text, cost,
turns, normalized tokens, tool trace). The SDK driver stays for unsandboxed
host runs.

Auth is the user's LOCAL CLAUDE CODE subscription auth: macOS-keychain
credentials do not exist inside a linux container, so the long-lived token
minted once by ``claude setup-token`` crosses in as the
``CLAUDE_CODE_OAUTH_TOKEN`` env var (bare ``docker -e`` — the value never
appears in argv).

Contamination: the image holds no ``~/.claude`` state and ONLY the scratch
workspace is mounted, so the memory/settings vector (threat d) closes
structurally; ``--setting-sources ""`` + ``--strict-mcp-config`` keep it
closed even if state appears. ``_CONTAMINATION_TOOLS`` (Skill/Web*) stay
blocked — a filesystem sandbox cannot stop those vectors. The INTERIM
filesystem blocklist is deliberately NOT passed: inside the container
Bash/Read/Write/... only reach the workspace — relaxing them is the payoff
of Step 14a.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from hangar.evals.drivers.base import AgentResult, MCPServerSpec
from hangar.evals.drivers.claude_sdk import (
    _CONTAMINATION_TOOLS,
    _classify_tool_result,
    _normalize_tool_name,
    _normalize_usage,
)
from hangar.evals.drivers.proc import run_process
from hangar.evals.drivers.sandbox import CONTAINER_WORKSPACE, ContainerSandbox
from hangar.evals.trace import ToolCall

_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def render_mcp_config(mcp: MCPServerSpec) -> dict:
    """The ``--mcp-config`` payload — url-only by construction (Step 13).

    A stdio spec is refused outright: it would need host paths inside the
    container AND put omd in the agent's privilege domain (the §2
    determination this whole design exists to avoid).
    """
    if mcp.transport != "http":
        raise ValueError(
            "sandboxed anchor requires an http MCPServerSpec (omd_transport='http')")
    return {"mcpServers": {mcp.name: {"type": "http", "url": mcp.url}}}


@dataclass(frozen=True)
class ClaudeCliRun:
    """Everything parsed from one ``claude -p --output-format stream-json`` run."""

    final_text: str
    cost_usd: float | None
    num_turns: int | None
    usage: dict | None
    tool_calls: list[ToolCall]


def parse_stream_json(stdout: str, server: str) -> ClaudeCliRun:
    """Parse the CLI's stream-json JSONL into report + trace + telemetry.

    Mirrors the SDK driver's message handling: assistant ``text`` blocks set
    the running final text (the ``result`` event overrides it), assistant
    ``tool_use`` blocks open a pending call, ``tool_result`` blocks on user
    events resolve it through the shared omd-envelope classifier. Unknown
    event/block types are skipped — tolerant to CLI event-shape drift.
    """
    final_text = ""
    cost: float | None = None
    num_turns: int | None = None
    usage: dict | None = None
    pending: dict[str, str] = {}   # tool_use_id -> bare tool name
    trace: list[ToolCall] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        content = (evt.get("message") or {}).get("content") or []
        if etype == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    final_text = block.get("text", "")
                elif block.get("type") == "tool_use":
                    pending[block.get("id")] = _normalize_tool_name(
                        block.get("name", ""), server)
        elif etype == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool = pending.get(block.get("tool_use_id"), "<unknown>")
                    ok, code = _classify_tool_result(
                        bool(block.get("is_error", False)), block.get("content"))
                    trace.append(ToolCall(tool=tool, ok=ok, error_code=code))
        elif etype == "result":
            if evt.get("result"):
                final_text = evt["result"]
            cost = evt.get("total_cost_usd")
            num_turns = evt.get("num_turns")
            usage = evt.get("usage")
    return ClaudeCliRun(final_text, cost, num_turns, usage, trace)


class ClaudeCliDriver:
    """Drive the Claude anchor via headless CLI inside the sandbox container."""

    def __init__(self, sandbox: ContainerSandbox | None = None):
        self.sandbox = sandbox or ContainerSandbox()

    def run(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        workspace: Path,
        model: str | None = None,
        max_turns: int = 80,
        timeout_s: float | None = None,
    ) -> AgentResult:
        if not os.environ.get(_TOKEN_VAR):
            raise RuntimeError(
                f"{_TOKEN_VAR} is not set. The sandboxed anchor authenticates with "
                "your local Claude Code subscription: run `claude setup-token` once "
                f"and export the result as {_TOKEN_VAR}."
            )
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "mcp_config.json").write_text(
            json.dumps(render_mcp_config(mcp), indent=2))

        container = f"hangar_{workspace.name}"
        argv = self.build_argv(prompt, workspace, mcp.name, model, max_turns,
                               container=container)
        start = time.monotonic()
        proc = run_process(argv, timeout_s=timeout_s)
        wall = time.monotonic() - start
        if proc.timed_out:
            # Killing the docker client does not stop the container.
            subprocess.run(["docker", "kill", container],
                           capture_output=True, text=True)

        # Persist the raw event stream next to the agent's scratch files so
        # every run is debuggable after the fact.
        (workspace / "claude_events.jsonl").write_text(proc.stdout)

        if not proc.timed_out and proc.returncode != 0:
            raise RuntimeError(
                f"sandboxed claude run failed (exit {proc.returncode}):\n{proc.stderr}")
        parsed = parse_stream_json(proc.stdout, mcp.name)
        return AgentResult(
            final_text=parsed.final_text,
            cost_usd=parsed.cost_usd,
            wall_clock_s=wall,
            num_turns=parsed.num_turns,
            tool_call_trace=parsed.tool_calls,
            tokens=_normalize_usage(parsed.usage),
            timed_out=proc.timed_out,
        )

    def build_argv(
        self,
        prompt: str,
        workspace: Path,
        server: str,
        model: str | None,
        max_turns: int,
        container: str | None = None,
    ) -> list[str]:
        """The docker-wrapped ``claude -p`` invocation. Separate for testability.

        The prompt positional comes BEFORE the variadic ``--disallowed-tools``
        so the tool list can't swallow it. ``--setting-sources ""`` is the CLI
        spelling of the SDK's ``setting_sources=[]`` starvation.
        """
        inner = [
            "claude", "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--max-turns", str(max_turns),
            "--permission-mode", "bypassPermissions",
            "--setting-sources", "",
            "--mcp-config", f"{CONTAINER_WORKSPACE}/mcp_config.json",
            "--strict-mcp-config",
        ]
        if model:
            inner += ["--model", model]
        inner += ["--disallowed-tools", *_CONTAMINATION_TOOLS]
        return self.sandbox.wrap_argv(inner, workspace, name=container)
