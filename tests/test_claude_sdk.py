"""Contamination-policy guards + telemetry capture for the Claude anchor driver.

The policy tests pin the *intent* of the tool restriction (block
privileged-context / external-knowledge vectors; leave benign tool discovery
alone) so a future edit to ``_DISALLOWED_TOOLS`` can't silently re-open a leak.
The telemetry tests run the driver against a FAKE ``claude_agent_sdk`` module
(injected into ``sys.modules`` — the real import is lazy inside ``run``), so
everything here runs in the base test env without the ``[anchor]`` extra.
"""

from __future__ import annotations

import sys
import types

from hangar.evals.drivers import claude_sdk
from hangar.evals.drivers.base import MCPServerSpec
from hangar.evals.drivers.claude_sdk import ClaudeAgentSDKDriver, _normalize_usage


def test_skill_and_web_tools_are_blocked():
    # Skill = privileged procedural context; Web* = external knowledge. A
    # filesystem sandbox would not stop these, so they must stay blocklisted.
    for tool in ("Skill", "WebSearch", "WebFetch"):
        assert tool in claude_sdk._DISALLOWED_TOOLS


def test_toolsearch_is_not_blocked():
    # ToolSearch only discovers tools the agent may already use — no privileged
    # context leaks through it, so blocking it would only hurt without cause.
    assert "ToolSearch" not in claude_sdk._DISALLOWED_TOOLS


def test_interim_filesystem_tools_blocked_while_cwd_is_the_repo():
    # Blocked ONLY as an interim guard (cwd is the the-hangar repo today). This
    # test documents that coupling: it should be RELAXED in the same change that
    # introduces a clean sandboxed workspace.
    for tool in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
        assert tool in claude_sdk._DISALLOWED_TOOLS


# --- token / turn telemetry (Step 12) ------------------------------------------


def test_normalize_usage_maps_and_passes_through():
    usage = {"input_tokens": 12, "output_tokens": 34,
             "cache_read_input_tokens": 5}
    assert _normalize_usage(usage) == {
        "input": 12, "output": 34, "cache_read_input_tokens": 5}
    # None != 0: no usage (or a shape we don't recognize) stays None.
    assert _normalize_usage(None) is None
    assert _normalize_usage({}) is None
    assert _normalize_usage("not a dict") is None


def _fake_sdk(result_msg) -> types.ModuleType:
    """A minimal claude_agent_sdk whose query() yields one ResultMessage."""
    fake = types.ModuleType("claude_agent_sdk")

    class _Opts:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def query(prompt, options):
        yield result_msg

    fake.ClaudeAgentOptions = _Opts
    fake.ResultMessage = type(result_msg)
    fake.query = query
    # Placeholder types the driver isinstance-checks against but never sees here.
    for name in ("AssistantMessage", "TextBlock", "ToolResultBlock", "ToolUseBlock"):
        setattr(fake, name, type(name, (), {}))
    return fake


def test_run_captures_usage_and_num_turns(monkeypatch, tmp_path):
    # The Step-11 live smoke showed turns=None on anchor runs — this pins the
    # fix: num_turns AND usage come off the ResultMessage into the AgentResult.
    class _Result:
        result = "final answer"
        total_cost_usd = 0.42
        num_turns = 7
        usage = {"input_tokens": 100, "output_tokens": 25}

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(_Result()))
    result = ClaudeAgentSDKDriver().run(
        "task", MCPServerSpec.omd(tmp_path), tmp_path,
        model="claude-opus-4-8", cwd=tmp_path,
    )
    assert result.final_text == "final answer"
    assert result.cost_usd == 0.42
    assert result.num_turns == 7
    assert result.tokens == {"input": 100, "output": 25}
