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
from hangar.evals.drivers.opencode import OpenCodeDriver, render_opencode_config

# Model for the live smoke. Overridable; defaults to the pulled smoke model.
LIVE_MODEL = os.environ.get("HANGAR_EVALS_OPENCODE_MODEL", "qwen3:8b")


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


def test_build_argv(tmp_path):
    driver = OpenCodeDriver()
    argv = driver.build_argv("do the task", tmp_path, "qwen3:8b")
    assert argv == [
        "opencode", "run",
        "-m", "ollama/qwen3:8b",
        "--dir", str(tmp_path),
        "--dangerously-skip-permissions",
        "do the task",
    ]


# ---------------------------------------------------------------------------
# run() against a monkeypatched subprocess
# ---------------------------------------------------------------------------


def test_run_writes_config_and_captures_stdout(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(argv, capture_output, text, cwd):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return types.SimpleNamespace(
            returncode=0,
            stdout='report:\n```json\n{"status": "done"}\n```',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    spec = MCPServerSpec.omd(tmp_path)
    result = OpenCodeDriver().run("do the task", spec, tmp_path, model="qwen3:8b")

    # Config landed in the workspace and is the rendered dict.
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert cfg["provider"]["ollama"]["models"]["qwen3:8b"] == {"tools": True}
    assert cfg["mcp"]["omd"]["command"][0] == sys.executable

    # Invocation + captured result.
    assert captured["argv"][:5] == ["opencode", "run", "-m", "ollama/qwen3:8b", "--dir"]
    assert captured["cwd"] == str(tmp_path)
    assert result.final_text == 'report:\n```json\n{"status": "done"}\n```'
    assert result.cost_usd is None
    assert result.wall_clock_s is not None and result.wall_clock_s >= 0


def test_run_nonzero_exit_raises(monkeypatch, tmp_path):
    def fake_run(argv, capture_output, text, cwd):
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
    prompt = (
        "Using ONLY the omd MCP tools, call start_session, then stop. "
        'End your final message with one fenced ```json block: '
        '{"status": "done"}.'
    )
    result = OpenCodeDriver().run(
        prompt, MCPServerSpec.omd(tmp_path), tmp_path, model=LIVE_MODEL,
    )
    assert result.final_text.strip(), "opencode produced no output"
