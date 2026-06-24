"""OpenCode driver — the local-model arm.

Shells out to the ``opencode`` CLI (an external Homebrew binary, not a Python
dep) pointed at a local OpenAI-compatible endpoint — Ollama by default. Same
sync ``AgentDriver`` contract as the Claude anchor, so the two are
interchangeable in the runner.

Before each run the driver writes an ``opencode.json`` into the workspace: the
hand-authored Ollama provider (OpenCode does not auto-detect Ollama) plus the
omd MCP server rendered from the shared ``MCPServerSpec``. The agent's stdout
transcript is captured verbatim as ``final_text``; the Step-5 scorer extracts
the fenced-JSON report from it, and tool-use metrics come from the omd
provenance DB under the same workspace — not from OpenCode's own output.

Two honest limitations:
  * ``opencode run`` exposes no turn-cap flag, so ``max_turns`` is accepted for
    interface parity but is currently a no-op.
  * OpenCode surfaces MCP tools to the model as ``omd_<tool>`` (not the
    ``mcp__omd__<tool>`` form the Claude SDK uses). OpenCode handles that
    naming itself; nothing to translate here.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from hangar.evals.drivers.base import AgentResult, MCPServerSpec

_CONFIG_SCHEMA = "https://opencode.ai/config.json"


def render_opencode_config(
    mcp: MCPServerSpec,
    model: str,
    provider: str = "ollama",
    base_url: str = "http://localhost:11434/v1",
) -> dict:
    """Build the ``opencode.json`` dict for one run.

    The provider block wires an OpenAI-compatible local endpoint via
    ``@ai-sdk/openai-compatible``; ``tools: true`` flags the model as
    function-calling capable. The mcp block translates the harness-neutral
    ``MCPServerSpec`` into OpenCode's schema (``type: "local"``, a single
    ``command`` list, and ``environment``).
    """
    return {
        "$schema": _CONFIG_SCHEMA,
        "provider": {
            provider: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"{provider} (local)",
                "options": {"baseURL": base_url},
                "models": {model: {"tools": True}},
            },
        },
        "mcp": {
            mcp.name: {
                "type": "local",
                "enabled": True,
                "command": [mcp.command, *mcp.args],
                "environment": dict(mcp.env),
            },
        },
    }


class OpenCodeDriver:
    """Drive a local model through the OpenCode CLI against an MCP server."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        provider: str = "ollama",
        binary: str = "opencode",
    ):
        self.base_url = base_url
        self.provider = provider
        self.binary = binary

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

        config = render_opencode_config(mcp, model, self.provider, self.base_url)
        (data_root / "opencode.json").write_text(json.dumps(config, indent=2))

        argv = self.build_argv(prompt, data_root, model)
        start = time.monotonic()
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(data_root))
        wall = time.monotonic() - start

        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode run failed (exit {proc.returncode}) for "
                f"{self.provider}/{model}:\n{proc.stderr}"
            )
        return AgentResult(
            final_text=proc.stdout,
            cost_usd=None,  # local model, no API cost
            wall_clock_s=wall,
        )

    def build_argv(self, prompt: str, data_root: Path, model: str) -> list[str]:
        """The ``opencode run`` invocation. Separate for testability."""
        return [
            self.binary,
            "run",
            "-m", f"{self.provider}/{model}",
            "--dir", str(data_root),
            "--dangerously-skip-permissions",  # auto-approve MCP tool calls headlessly
            prompt,
        ]
