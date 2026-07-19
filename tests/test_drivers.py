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
    assert r.timed_out is False


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

    class ToolUseBlock:
        def __init__(self, id, name, input=None):
            self.id, self.name, self.input = id, name, input or {}

    class ToolResultBlock:
        def __init__(self, tool_use_id, content, is_error=False):
            self.tool_use_id, self.content, self.is_error = tool_use_id, content, is_error

    class UserMessage:
        def __init__(self, content):
            self.content = content

    async def query(prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        # ok call -> good result; bad call -> omd error envelope (schema error).
        yield AssistantMessage([
            ToolUseBlock("t1", "mcp__omd__start_session"),
            ToolUseBlock("t2", "mcp__omd__run_plan"),
        ])
        yield UserMessage([
            ToolResultBlock("t1", '{"session_id": "s1"}'),
            ToolResultBlock("t2", '{"error": {"code": "USER_INPUT_ERROR"}}', is_error=True),
        ])
        yield AssistantMessage([TextBlock("...working...")])
        yield ResultMessage(result="FINAL REPORT", total_cost_usd=0.0123)

    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.TextBlock = TextBlock
    mod.AssistantMessage = AssistantMessage
    mod.ResultMessage = ResultMessage
    mod.ToolUseBlock = ToolUseBlock
    mod.ToolResultBlock = ToolResultBlock
    mod.UserMessage = UserMessage
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


def test_driver_captures_tool_call_trace(monkeypatch, tmp_path):
    mod, _ = _fake_sdk()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    result = ClaudeAgentSDKDriver().run(
        "do the task", MCPServerSpec.omd(tmp_path), tmp_path, cwd=tmp_path,
    )

    trace = result.tool_call_trace
    assert [c.tool for c in trace] == ["start_session", "run_plan"]  # prefix stripped
    assert trace[0].ok is True and trace[0].error_code is None
    assert trace[1].ok is False and trace[1].error_code == "USER_INPUT_ERROR"


def test_driver_timeout_returns_partial_state(monkeypatch, tmp_path):
    # Step 18: the fake stream emits real work (with per-message usage), then
    # hangs — the model of both observed SDK failures. The wall-clock budget
    # must kill the run and still hand back everything seen so far.
    mod, _ = _fake_sdk()

    class AssistantWithUsage(mod.AssistantMessage):
        def __init__(self, content, usage):
            super().__init__(content)
            self.usage = usage

    async def hanging_query(prompt, options):
        import asyncio

        yield AssistantWithUsage(
            [mod.TextBlock("partial progress")],
            {"input_tokens": 100, "output_tokens": 10})
        yield AssistantWithUsage(
            [mod.ToolUseBlock("t1", "mcp__omd__start_session")],
            {"input_tokens": 200, "output_tokens": 5})
        yield mod.UserMessage(
            [mod.ToolResultBlock("t1", '{"session_id": "s1"}')])
        await asyncio.sleep(3600)   # the deterministic post-physics hang

    mod.query = hanging_query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    result = ClaudeAgentSDKDriver().run(
        "task", MCPServerSpec.omd(tmp_path), tmp_path, cwd=tmp_path,
        timeout_s=0.5,
    )
    assert result.timed_out is True
    assert result.final_text == "partial progress"
    assert [c.tool for c in result.tool_call_trace] == ["start_session"]
    # Cost fallback (item 5): no ResultMessage ever arrived, so cost is an
    # honest None and tokens come from the per-message accumulation.
    assert result.cost_usd is None
    assert result.tokens == {"input": 300, "output": 15}
    assert result.wall_clock_s < 30


def test_driver_result_message_usage_beats_accumulation(monkeypatch, tmp_path):
    # When the run completes, the ResultMessage's canonical usage wins over
    # whatever was accumulated along the way.
    mod, _ = _fake_sdk()

    class AssistantWithUsage(mod.AssistantMessage):
        def __init__(self, content, usage):
            super().__init__(content)
            self.usage = usage

    class ResultWithUsage(mod.ResultMessage):
        def __init__(self):
            super().__init__(result="done", total_cost_usd=0.5)
            self.num_turns = 2
            self.usage = {"input_tokens": 999, "output_tokens": 111}

    async def query(prompt, options):
        yield AssistantWithUsage([mod.TextBlock("hi")],
                                 {"input_tokens": 1, "output_tokens": 1})
        yield ResultWithUsage()

    mod.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    result = ClaudeAgentSDKDriver().run(
        "task", MCPServerSpec.omd(tmp_path), tmp_path, cwd=tmp_path)
    assert result.timed_out is False
    assert result.tokens == {"input": 999, "output": 111}
    assert result.cost_usd == 0.5


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
