"""Sandboxed anchor driver (Step 14a): argv policy, stream-json parsing, auth.

The argv tests pin the contamination stance of the in-container anchor: the
INTERIM filesystem blocklist is relaxed (that is the point of the sandbox)
while the persistent contamination tools stay blocked, settings are starved,
and only container paths appear. The parser tests feed canned stream-json —
the same protocol the SDK wraps — so no CLI or container is needed.
"""

from __future__ import annotations

import json

import pytest

from hangar.evals.drivers.claude_cli import (
    ClaudeCliDriver,
    parse_stream_json,
    render_mcp_config,
)
from hangar.evals.drivers.base import MCPServerSpec
from hangar.evals.drivers.claude_sdk import _INTERIM_FILESYSTEM_TOOLS


def test_render_mcp_config_is_url_only_and_refuses_stdio(tmp_path):
    cfg = render_mcp_config(MCPServerSpec.omd_http("http://host.docker.internal:8123/mcp"))
    assert cfg == {"mcpServers": {"omd": {
        "type": "http", "url": "http://host.docker.internal:8123/mcp"}}}
    dumped = json.dumps(cfg)
    assert "OMD_" not in dumped and "/Users/" not in dumped
    with pytest.raises(ValueError, match="http"):
        render_mcp_config(MCPServerSpec.omd(tmp_path))


def test_build_argv_relaxes_interim_tools_but_keeps_contamination_guard(tmp_path):
    argv = ClaudeCliDriver().build_argv(
        "solve it", tmp_path, "omd", "claude-opus-4-8", 80)
    blocked = argv[argv.index("--disallowed-tools") + 1:]
    assert blocked == ["Skill", "WebSearch", "WebFetch"]
    # The sandbox is what makes this safe: the interim filesystem tools are
    # AVAILABLE in-container — none of them may reappear in the blocklist.
    assert not set(_INTERIM_FILESYSTEM_TOOLS) & set(blocked)
    # Settings starvation + strict MCP config, the CLI spellings.
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--max-turns") + 1] == "80"
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    # stream-json requires --verbose in print mode.
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    # The mcp config is referenced by its CONTAINER path, not a host path.
    assert argv[argv.index("--mcp-config") + 1] == "/workspace/mcp_config.json"
    # The prompt positional precedes the variadic tool list (which would
    # otherwise swallow it), right after -p.
    assert argv[argv.index("-p") + 1] == "solve it"


def _evt(obj) -> str:
    return json.dumps(obj)


_CANNED = "\n".join([
    _evt({"type": "system", "subtype": "init", "session_id": "s"}),
    _evt({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "working on it"},
        {"type": "tool_use", "id": "t1", "name": "mcp__omd__start_session",
         "input": {}},
    ]}}),
    _evt({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
         "content": [{"type": "text", "text": '{"ok": true}'}]},
    ]}}),
    _evt({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "mcp__omd__run_plan", "input": {}},
    ]}}),
    _evt({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
         "content": [{"type": "text",
                      "text": '{"error": {"code": "USER_INPUT_ERROR"}}'}]},
    ]}}),
    _evt({"type": "result", "subtype": "success", "result": "final report",
          "total_cost_usd": 1.25, "num_turns": 7,
          "usage": {"input_tokens": 900, "output_tokens": 120}}),
])


def test_parse_stream_json_yields_sdk_shaped_results():
    run = parse_stream_json(_CANNED, "omd")
    assert run.final_text == "final report"          # result overrides text
    assert run.cost_usd == 1.25
    assert run.num_turns == 7
    assert run.usage == {"input_tokens": 900, "output_tokens": 120}
    assert [(c.tool, c.ok, c.error_code) for c in run.tool_calls] == [
        ("start_session", True, None),
        ("run_plan", False, "USER_INPUT_ERROR"),
    ]


def test_parse_stream_json_tolerates_garbage_and_unknown_events():
    run = parse_stream_json("not json\n" + _evt({"type": "mystery"}) + "\n", "omd")
    assert run.final_text == "" and run.tool_calls == []


def test_run_without_token_fails_fast_with_guidance(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="claude setup-token"):
        ClaudeCliDriver().run(
            "task", MCPServerSpec.omd_http("http://h:1/mcp"), tmp_path)


def test_run_writes_config_and_events_and_wraps_in_docker(monkeypatch, tmp_path):
    import hangar.evals.drivers.claude_cli as cli_mod

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    seen = {}

    class _Proc:
        returncode = 0
        stdout = _CANNED
        stderr = ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _Proc()

    monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)
    spec = MCPServerSpec.omd_http("http://host.docker.internal:8123/mcp")
    result = ClaudeCliDriver().run("task", spec, tmp_path, model="claude-opus-4-8")

    assert seen["argv"][:3] == ["docker", "run", "--rm"]
    cfg = json.loads((tmp_path / "mcp_config.json").read_text())
    assert cfg["mcpServers"]["omd"]["url"] == "http://host.docker.internal:8123/mcp"
    assert (tmp_path / "claude_events.jsonl").read_text() == _CANNED
    assert result.final_text == "final report"
    assert result.cost_usd == 1.25
    assert result.num_turns == 7
    assert result.tokens == {"input": 900, "output": 120}
    assert [c.tool for c in result.tool_call_trace] == ["start_session", "run_plan"]
