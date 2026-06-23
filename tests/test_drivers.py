"""Tests for the driver layer (base interface + Claude anchor).

The fast tests are fully offline: they inject a fake ``claude_agent_sdk`` module
so ``ClaudeAgentSDKDriver.run`` exercises its real stream-parsing and
option-wiring with no network and no SDK installed. The live smoke
(``-m slow``) drives the real anchor through the omd server and is deselected
by default.
"""

from __future__ import annotations

import sys
import types

import pytest

from hangar.evals.drivers import AgentResult, MCPServerSpec
from hangar.evals.drivers.claude_sdk import ClaudeAgentSDKDriver


# ---------------------------------------------------------------------------
# base.py — spec + result
# ---------------------------------------------------------------------------


def test_mcp_server_spec_omd(tmp_path):
    spec = MCPServerSpec.omd(tmp_path)
    assert spec.name == "omd"
    assert spec.command == sys.executable
    assert spec.args == ["-m", "hangar.omd.server"]
    assert spec.env["OMD_DATA_ROOT"] == str(tmp_path / "omd_data")
    assert spec.env["OMD_DB_PATH"] == str(tmp_path / "analysis.db")
    assert spec.env["OMD_PLAN_STORE"] == str(tmp_path / "plans")
    assert spec.env["OMD_RECORDINGS_DIR"] == str(tmp_path / "recordings")


def test_agent_result_defaults():
    r = AgentResult(final_text="hi")
    assert r.final_text == "hi"
    assert r.cost_usd is None
    assert r.wall_clock_s is None
    assert r.num_turns is None
    assert r.tool_call_trace is None


# ---------------------------------------------------------------------------
# claude_sdk.py — driven against a fake SDK
# ---------------------------------------------------------------------------


def _fake_sdk() -> tuple[types.ModuleType, dict]:
    """A stand-in claude_agent_sdk: real-enough classes + a scripted query()."""
    mod = types.ModuleType("claude_agent_sdk")
    captured: dict = {}

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        def __init__(self, result=None, total_cost_usd=None):
            self.result = result
            self.total_cost_usd = total_cost_usd

    async def query(prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield AssistantMessage([TextBlock("...working...")])
        yield ResultMessage(result="FINAL REPORT", total_cost_usd=0.0123)

    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.TextBlock = TextBlock
    mod.AssistantMessage = AssistantMessage
    mod.ResultMessage = ResultMessage
    mod.query = query
    return mod, captured


def test_driver_parses_stream_and_wires_options(monkeypatch, tmp_path):
    mod, captured = _fake_sdk()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    spec = MCPServerSpec.omd(tmp_path)
    result = ClaudeAgentSDKDriver().run(
        "do the task", spec, tmp_path,
        model="claude-x", max_turns=12, cwd=tmp_path,
    )

    # ResultMessage.result wins as final_text; cost flows through; clock set.
    assert result.final_text == "FINAL REPORT"
    assert result.cost_usd == 0.0123
    assert result.wall_clock_s is not None and result.wall_clock_s >= 0

    # The SDK options reproduce eval_lane_c.py's wiring.
    opts = captured["options"]
    assert captured["prompt"] == "do the task"
    assert opts.model == "claude-x"
    assert opts.max_turns == 12
    assert opts.permission_mode == "bypassPermissions"
    assert opts.allowed_tools == ["mcp__omd"]
    assert "Bash" in opts.disallowed_tools
    omd = opts.mcp_servers["omd"]
    assert omd == {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "hangar.omd.server"],
        "env": spec.env,
    }


def test_driver_without_sdk_raises_clear_error(monkeypatch, tmp_path):
    # A None entry forces ImportError regardless of whether the SDK is installed.
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    with pytest.raises(RuntimeError, match="claude-agent-sdk is not installed"):
        ClaudeAgentSDKDriver().run(
            "x", MCPServerSpec.omd(tmp_path), tmp_path, cwd=tmp_path,
        )


# ---------------------------------------------------------------------------
# Live smoke — opt-in (`pytest -m slow`); needs the SDK, the CLI, and a key.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claude_anchor_live_smoke(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    spec = MCPServerSpec.omd(tmp_path)
    prompt = (
        "Using ONLY the omd MCP tools, call start_session, then stop. "
        'End your final message with one fenced ```json block: '
        '{"status": "done"}.'
    )
    result = ClaudeAgentSDKDriver().run(prompt, spec, tmp_path, max_turns=8)
    assert result.final_text.strip(), "anchor produced no final text"
    assert result.cost_usd is None or result.cost_usd >= 0
