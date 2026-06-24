"""Tests for the OpenCode driver (the local-model arm).

The fast tests are fully offline: they monkeypatch ``subprocess.run`` so the
config rendering, argv construction, and failure path are exercised with no
``opencode`` binary and no Ollama. The live smoke (``-m slow``) runs a real
local model through OpenCode against the omd server and is deselected by
default (and skipped if the binary or Ollama is unavailable).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types

import pytest

from hangar.evals.drivers import MCPServerSpec
from hangar.evals.drivers.opencode import (
    OpenCodeDriver,
    parse_opencode_events,
    render_opencode_config,
)

# Model for the live smoke. Overridable; defaults to the pulled smoke model.
LIVE_MODEL = os.environ.get("HANGAR_EVALS_OPENCODE_MODEL", "qwen3:8b")

# A representative --format json event stream, shaped from the real 2026-06-24
# spike: step_start, a completed tool_use carrying the omd result envelope, the
# assistant text (the report), and step_finish with tokens/cost.
SPIKE_JSONL = "\n".join([
    json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
    json.dumps({"type": "tool_use", "part": {
        "type": "tool", "tool": "omd_start_session", "callID": "call_1",
        "state": {"status": "completed", "input": {},
                  "output": '{"session_id": "sess-abc", "joined": false}'},
    }}),
    json.dumps({"type": "text", "part": {
        "type": "text", "text": 'report:\n```json\n{"status": "done"}\n```'}}),
    json.dumps({"type": "step_finish", "part": {
        "type": "step-finish", "tokens": {"output": 219}, "cost": 0}}),
])


# ---------------------------------------------------------------------------
# Config rendering (pure)
# ---------------------------------------------------------------------------


def test_render_config_provider_and_mcp(tmp_path):
    spec = MCPServerSpec.omd(tmp_path)
    cfg = render_opencode_config(spec, "qwen3:8b")

    assert cfg["$schema"] == "https://opencode.ai/config.json"

    prov = cfg["provider"]["ollama"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"] == "http://localhost:11434/v1"
    assert prov["models"]["qwen3:8b"] == {"tools": True}

    # MCPServerSpec -> OpenCode's mcp schema (type/command-list/environment).
    omd = cfg["mcp"]["omd"]
    assert omd["type"] == "local"
    assert omd["enabled"] is True
    assert omd["command"] == [sys.executable, "-m", "hangar.omd.server"]
    assert omd["environment"]["OMD_DB_PATH"] == str(tmp_path / "analysis.db")


def test_render_config_disables_builtin_tools(tmp_path):
    # MCP-only restriction: every built-in is disabled so only omd_* remain,
    # matching the Claude driver's disallowed_tools.
    cfg = render_opencode_config(MCPServerSpec.omd(tmp_path), "qwen3:8b")
    assert cfg["tools"]["write"] is False
    assert cfg["tools"]["bash"] is False
    assert all(v is False for v in cfg["tools"].values())


def test_render_config_custom_provider_and_url(tmp_path):
    spec = MCPServerSpec.omd(tmp_path)
    cfg = render_opencode_config(
        spec, "my-model", provider="mlx", base_url="http://localhost:8080/v1"
    )
    prov = cfg["provider"]["mlx"]
    assert prov["name"] == "mlx (local)"
    assert prov["options"]["baseURL"] == "http://localhost:8080/v1"
    assert "mlx" in cfg["provider"]
    assert cfg["mcp"]["omd"]["environment"]["OMD_DATA_ROOT"] == str(tmp_path / "omd_data")


# ---------------------------------------------------------------------------
# argv construction (pure)
# ---------------------------------------------------------------------------


def test_build_argv_uses_json_format(tmp_path):
    driver = OpenCodeDriver()
    argv = driver.build_argv("do the task", tmp_path, "qwen3:8b")
    assert argv == [
        "opencode", "run",
        "-m", "ollama/qwen3:8b",
        "--dir", str(tmp_path),
        "--dangerously-skip-permissions",
        "--format", "json",
        "do the task",
    ]


# ---------------------------------------------------------------------------
# parse_opencode_events (pure) — grounded in the real spike schema
# ---------------------------------------------------------------------------


def test_parse_events_report_trace_and_cost():
    run = parse_opencode_events(SPIKE_JSONL, server="omd")
    assert run.final_text == 'report:\n```json\n{"status": "done"}\n```'
    assert [c.tool for c in run.tool_calls] == ["start_session"]  # omd_ prefix stripped
    assert run.tool_calls[0].ok is True
    assert run.cost_usd == 0.0
    assert run.num_turns == 1


def test_parse_events_schema_error_envelope_is_not_ok():
    # omd returns USER_INPUT_ERROR as tool OUTPUT with status still "completed".
    jsonl = json.dumps({"type": "tool_use", "part": {
        "type": "tool", "tool": "omd_run_plan",
        "state": {"status": "completed",
                  "output": '{"error": {"code": "USER_INPUT_ERROR"}}'},
    }})
    run = parse_opencode_events(jsonl, server="omd")
    call = run.tool_calls[0]
    assert call.tool == "run_plan"
    assert call.ok is False
    assert call.error_code == "USER_INPUT_ERROR"


# ---------------------------------------------------------------------------
# run() against a monkeypatched subprocess
# ---------------------------------------------------------------------------


def test_run_writes_config_parses_events_and_closes_stdin(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(argv, capture_output, text, cwd, stdin):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["stdin"] = stdin
        return types.SimpleNamespace(returncode=0, stdout=SPIKE_JSONL, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    spec = MCPServerSpec.omd(tmp_path)
    result = OpenCodeDriver().run("do the task", spec, tmp_path, model="qwen3:8b")

    # Config landed in the workspace and is the rendered dict.
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert cfg["provider"]["ollama"]["models"]["qwen3:8b"] == {"tools": True}
    assert cfg["mcp"]["omd"]["command"][0] == sys.executable

    # stdin MUST be closed (the hang fix), and json events get parsed.
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["cwd"] == str(tmp_path)
    assert result.final_text == 'report:\n```json\n{"status": "done"}\n```'
    assert [c.tool for c in result.tool_call_trace] == ["start_session"]
    assert result.cost_usd == 0.0
    assert result.num_turns == 1
    assert result.wall_clock_s is not None and result.wall_clock_s >= 0

    # Raw events persisted for debuggability.
    assert (tmp_path / "opencode_events.jsonl").read_text() == SPIKE_JSONL


def test_run_nonzero_exit_raises(monkeypatch, tmp_path):
    def fake_run(argv, capture_output, text, cwd, stdin):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="provider not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="opencode run failed"):
        OpenCodeDriver().run("x", MCPServerSpec.omd(tmp_path), tmp_path)


# ---------------------------------------------------------------------------
# Live smoke — opt-in (`pytest -m slow`); needs the opencode binary + Ollama.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_opencode_live_smoke(tmp_path):
    if shutil.which("opencode") is None:
        pytest.skip("opencode binary not on PATH")
    # Tempt a built-in file write — the restriction should leave it unavailable,
    # so the model can only act through omd_* tools.
    prompt = (
        "Create a file named notes.txt with the word hello using your "
        "file-writing tool; if you cannot, call the omd start_session tool "
        "instead. Then reply DONE."
    )
    result = OpenCodeDriver().run(
        prompt, MCPServerSpec.omd(tmp_path), tmp_path, model=LIVE_MODEL,
    )
    # Driver captured structured output, and NO non-omd built-in was usable.
    assert result.tool_call_trace or result.final_text.strip(), "driver captured nothing"
    assert result.num_turns is not None
    builtins_used = [c.tool for c in result.tool_call_trace
                     if c.tool in {"write", "bash", "read", "edit"}]
    assert not builtins_used, f"built-in tools leaked: {builtins_used}"
    # Raw events were persisted for debugging.
    assert (tmp_path / "opencode_events.jsonl").exists()
