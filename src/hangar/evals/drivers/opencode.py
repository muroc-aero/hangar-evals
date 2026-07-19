"""OpenCode driver — the local-model arm.

Shells out to the ``opencode`` CLI (an external Homebrew binary, not a Python
dep) pointed at a local OpenAI-compatible endpoint — Ollama by default. Same
sync ``AgentDriver`` contract as the Claude anchor, so the two are
interchangeable in the runner.

Before each run the driver writes an ``opencode.json`` into the workspace: the
hand-authored Ollama provider (OpenCode does not auto-detect Ollama) plus the
omd MCP server rendered from the shared ``MCPServerSpec``.

The run uses ``--format json``, which emits JSONL events (verified by a live
spike, 2026-06-24, resolving the §10 open question). One run yields BOTH:
  * the agent's report — concatenated ``text`` events -> ``final_text``;
  * the tool-call trace — ``tool_use`` events -> ``list[ToolCall]``, where
    ``part.state.output`` carries the omd result/error envelope, so even
    schema-rejected calls (which never reach the provenance DB) are captured.
``step_finish`` events carry token counts and cost (0 for a local model).

Two operational notes learned from the spike:
  * ``opencode run`` BLOCKS on an open stdin in headless use — the subprocess
    MUST close stdin (``stdin=DEVNULL``) or it hangs. This was the cause of an
    earlier multi-minute hang.
  * ``opencode run`` exposes no turn-cap flag, so ``max_turns`` is accepted for
    interface parity but is a no-op.
  * OpenCode names MCP tools ``<server>_<tool>`` (e.g. ``omd_start_session``);
    the parser strips the ``<server>_`` prefix to the bare tool name.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from hangar.evals.drivers.base import AgentResult, MCPServerSpec
from hangar.evals.drivers.sandbox import CONTAINER_WORKSPACE, ContainerSandbox
from hangar.evals.trace import ToolCall, parse_omd_error_code

_CONFIG_SCHEMA = "https://opencode.ai/config.json"


@dataclass(frozen=True)
class OpenCodeRun:
    """Everything parsed from one ``opencode run --format json`` event stream."""

    final_text: str
    tool_calls: list[ToolCall]
    cost_usd: float
    num_turns: int
    tokens: dict | None  # summed over step_finish events; None if none carried any


def _strip_server_prefix(tool: str, server: str) -> str:
    """``omd_start_session`` -> ``start_session`` (bare, harness-neutral name)."""
    prefix = f"{server}_"
    return tool[len(prefix):] if tool.startswith(prefix) else tool


def _accumulate_tokens(acc: dict, tokens) -> None:
    """Sum one ``step_finish`` token dict into ``acc``.

    OpenCode's key names already match the normalized shape (``input``,
    ``output``, ``reasoning``); nested dicts (``cache: {read, write}``) flatten
    to ``cache_read`` / ``cache_write``. Non-numeric values are dropped, never
    coerced.
    """
    if not isinstance(tokens, dict):
        return
    for key, val in tokens.items():
        if isinstance(val, dict):
            for sub, sv in val.items():
                if isinstance(sv, (int, float)):
                    acc[f"{key}_{sub}"] = acc.get(f"{key}_{sub}", 0) + sv
        elif isinstance(val, (int, float)):
            acc[key] = acc.get(key, 0) + val


def parse_opencode_events(stdout: str, server: str) -> OpenCodeRun:
    """Parse OpenCode's ``--format json`` JSONL into report + trace + telemetry.

    Tool classification: a call is OK only when OpenCode reports
    ``state.status == "completed"`` AND its output is not an omd error
    envelope — omd returns ``USER_INPUT_ERROR`` envelopes as normal tool
    OUTPUT (status still "completed"), so the envelope, not the status, is the
    source of truth for schema rejections.
    """
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    cost = 0.0
    turns = 0
    token_sums: dict = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        part = evt.get("part") or {}
        if etype == "text":
            text_parts.append(part.get("text", ""))
        elif etype == "tool_use":
            tool = _strip_server_prefix(part.get("tool", ""), server)
            state = part.get("state") or {}
            output = state.get("output")
            if output is not None and not isinstance(output, str):
                output = json.dumps(output)
            code = parse_omd_error_code(output)
            ok = state.get("status") == "completed" and code is None
            calls.append(ToolCall(tool=tool, ok=ok, error_code=code or (None if ok else "ERROR")))
        elif etype == "step_finish":
            turns += 1
            cost += part.get("cost") or 0.0
            _accumulate_tokens(token_sums, part.get("tokens"))
    return OpenCodeRun("\n".join(text_parts), calls, cost, turns, token_sums or None)


# OpenCode's built-in tools. UNSANDBOXED we disable ALL of them so the local
# agent is restricted to the omd MCP tools only — matching the Claude driver's
# interim blocklist, so both harnesses face the same MCP-only surface (its cwd
# is a host directory; verified: qwen3:8b used `write` 12x on the paraboloid
# task). Disabling via `tools` removes them from the offered set (confirmed by
# spike) rather than merely denying at call time. Version-fragile: a NEW
# built-in OpenCode adds would leak until listed here.
BUILTIN_TOOLS = [
    "bash", "edit", "write", "read", "glob", "grep", "list",
    "patch", "webfetch", "websearch", "task", "todowrite", "todoread",
]

# SANDBOXED (Step 14b) the guard flips from tool-starvation to reachability:
# file/bash built-ins come back at their defaults — they only reach the
# mounted workspace — and ONLY the vectors a filesystem sandbox cannot stop
# stay disabled (the OpenCode spellings of the anchor's _CONTAMINATION_TOOLS).
CONTAMINATION_BUILTINS = ["webfetch", "websearch"]


def _containerize_url(url: str) -> str:
    """Rewrite a host-loopback URL for in-container use.

    colima forwards ``host.docker.internal`` to the host loopback (verified
    live, Step 14a recon), so Ollama's bind never changes.
    """
    return (url.replace("localhost", "host.docker.internal")
               .replace("127.0.0.1", "host.docker.internal"))


def render_opencode_config(
    mcp: MCPServerSpec,
    model: str,
    provider: str = "ollama",
    base_url: str = "http://localhost:11434/v1",
    sandboxed: bool = False,
) -> dict:
    """Build the ``opencode.json`` dict for one run.

    The provider block wires an OpenAI-compatible local endpoint via
    ``@ai-sdk/openai-compatible``; ``tools: true`` flags the model as
    function-calling capable. The mcp block translates the harness-neutral
    ``MCPServerSpec`` into OpenCode's schema — ``type: "local"`` (a single
    ``command`` list plus ``environment``) for stdio, ``type: "remote"``
    (url-only, Step 13) for a host-side HTTP omd service. ``tools`` disables
    OpenCode's built-ins: all of them unsandboxed (the MCP-only track), only
    the contamination vectors sandboxed (Step 14b — the container scopes the
    rest).

    Sandboxed, a stdio spec is refused outright: it would need host paths
    inside the container AND put omd in the agent's privilege domain, making
    the provenance DB — the PRIMARY grading evidence — forgeable.
    """
    if sandboxed and mcp.transport != "http":
        raise ValueError(
            "sandboxed opencode requires an http MCPServerSpec (omd_transport='http')")
    if mcp.transport == "http":
        mcp_entry = {"type": "remote", "enabled": True, "url": mcp.url}
    else:
        mcp_entry = {
            "type": "local",
            "enabled": True,
            "command": [mcp.command, *mcp.args],
            "environment": dict(mcp.env),
        }
    disabled = CONTAMINATION_BUILTINS if sandboxed else BUILTIN_TOOLS
    return {
        "$schema": _CONFIG_SCHEMA,
        "provider": {
            provider: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"{provider} (local)",
                "options": {"baseURL": _containerize_url(base_url) if sandboxed else base_url},
                "models": {model: {"tools": True}},
            },
        },
        "tools": {t: False for t in disabled},
        "mcp": {mcp.name: mcp_entry},
    }


class OpenCodeDriver:
    """Drive a local model through the OpenCode CLI against an MCP server.

    With ``sandbox`` set (Step 14b) the same CLI runs inside the container:
    ``data_root`` is then the scratch WORKSPACE (the only mounted path — the
    runner passes it; omd state stays host-only), the argv is docker-wrapped,
    and the rendered config points at ``host.docker.internal`` for both the
    Ollama endpoint and the omd url. No secrets cross: the local arm passes
    no env vars at all.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        provider: str = "ollama",
        binary: str = "opencode",
        sandbox: ContainerSandbox | None = None,
    ):
        self.base_url = base_url
        self.provider = provider
        self.binary = binary
        self.sandbox = sandbox

    def run(
        self,
        prompt: str,
        mcp: MCPServerSpec,
        data_root: Path,
        model: str = "qwen3:8b",  # pulled floor model (non-MLX); runner overrides per matrix
        max_turns: int = 80,  # accepted for interface parity; OpenCode has no cap flag
    ) -> AgentResult:
        data_root = Path(data_root)
        data_root.mkdir(parents=True, exist_ok=True)

        config = render_opencode_config(
            mcp, model, self.provider, self.base_url,
            sandboxed=self.sandbox is not None)
        (data_root / "opencode.json").write_text(json.dumps(config, indent=2))

        argv = self.build_argv(prompt, data_root, model)
        start = time.monotonic()
        # stdin=DEVNULL is REQUIRED: opencode run blocks on an open stdin.
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(data_root),
            stdin=subprocess.DEVNULL,
        )
        wall = time.monotonic() - start

        # Persist the raw event stream so every run is debuggable after the
        # fact (OpenCode itself does not retain `run`-mode transcripts).
        (data_root / "opencode_events.jsonl").write_text(proc.stdout)

        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode run failed (exit {proc.returncode}) for "
                f"{self.provider}/{model}:\n{proc.stderr}"
            )
        parsed = parse_opencode_events(proc.stdout, mcp.name)
        return AgentResult(
            final_text=parsed.final_text,
            cost_usd=parsed.cost_usd,
            wall_clock_s=wall,
            num_turns=parsed.num_turns,
            tool_call_trace=parsed.tool_calls,
            tokens=parsed.tokens,
        )

    def build_argv(self, prompt: str, data_root: Path, model: str) -> list[str]:
        """The ``opencode run`` invocation. Separate for testability.

        Sandboxed, the inner argv sees only container paths (``--dir
        /workspace``, where the workspace — holding ``opencode.json`` — is
        mounted) and gets docker-wrapped with no env passthrough.
        """
        inner = [
            self.binary,
            "run",
            "-m", f"{self.provider}/{model}",
            "--dir", CONTAINER_WORKSPACE if self.sandbox else str(data_root),
            "--dangerously-skip-permissions",  # auto-approve MCP tool calls headlessly
            "--format", "json",                 # JSONL events: report + tool trace
            prompt,
        ]
        if self.sandbox:
            return self.sandbox.wrap_argv(inner, data_root)
        return inner
