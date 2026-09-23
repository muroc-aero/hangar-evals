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
import sys

import pytest

import hangar.evals.drivers.opencode as opencode_mod
from hangar.evals.drivers import MCPServerSpec
from hangar.evals.drivers.opencode import (
    OpenCodeDriver,
    parse_opencode_events,
    render_opencode_config,
)
from hangar.evals.drivers.proc import ProcOutcome

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


def test_render_config_remote_mcp_is_url_only():
    # Step 13: an http MCPServerSpec renders as OpenCode's remote form —
    # url only. No command/environment key, and the serialized config carries
    # no filesystem path (the contamination property of the channel).
    cfg = render_opencode_config(
        MCPServerSpec.omd_http("http://127.0.0.1:8123/mcp"), "qwen3:8b")
    omd = cfg["mcp"]["omd"]
    assert omd == {"type": "remote", "enabled": True,
                   "url": "http://127.0.0.1:8123/mcp"}
    dumped = json.dumps(cfg)
    assert "OMD_" not in dumped
    assert sys.executable not in dumped
    # Built-ins stay disabled regardless of transport.
    assert all(v is False for v in cfg["tools"].values())


def test_render_config_sandboxed_relaxes_tools_and_containerizes_urls():
    # Step 14b: sandboxed, the guard flips from tool-starvation to
    # reachability — ONLY the contamination vectors stay disabled (file/bash
    # built-ins return at their defaults, scoped by the container) — and the
    # Ollama endpoint is rewritten to host.docker.internal. No host path, no
    # OMD_*, no sys.executable in the serialized config.
    spec = MCPServerSpec.omd_http("http://host.docker.internal:8123/mcp")
    cfg = render_opencode_config(spec, "qwen3:8b", sandboxed=True)
    assert cfg["tools"] == {"webfetch": False, "websearch": False}
    assert (cfg["provider"]["ollama"]["options"]["baseURL"]
            == "http://host.docker.internal:11434/v1")
    assert cfg["mcp"]["omd"] == {"type": "remote", "enabled": True,
                                 "url": "http://host.docker.internal:8123/mcp"}
    dumped = json.dumps(cfg)
    assert "OMD_" not in dumped
    assert sys.executable not in dumped
    assert "localhost" not in dumped and "127.0.0.1" not in dumped


def test_render_config_sandboxed_refuses_stdio(tmp_path):
    # A stdio omd child inside the container would share the agent's
    # privilege domain — the grading evidence would be forgeable.
    with pytest.raises(ValueError, match="http"):
        render_opencode_config(MCPServerSpec.omd(tmp_path), "qwen3:8b",
                               sandboxed=True)


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


def test_build_argv_sandboxed_wraps_in_docker_with_no_env(tmp_path):
    # Step 14b: the docker wrapper mounts ONLY the workspace, passes NO env
    # vars (the local arm has no token), and the inner argv sees container
    # paths only — --dir is /workspace, and the host path appears solely in
    # the mount spec.
    from hangar.evals.drivers.sandbox import (
        CONTAINER_WORKSPACE,
        OPENCODE_IMAGE,
        ContainerSandbox,
    )

    driver = OpenCodeDriver(sandbox=ContainerSandbox(
        image=OPENCODE_IMAGE, env_passthrough=()))
    argv = driver.build_argv("do the task", tmp_path, "qwen3:8b")
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "-e" not in argv
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == [f"{tmp_path.resolve()}:{CONTAINER_WORKSPACE}"]
    inner = argv[argv.index(OPENCODE_IMAGE) + 1:]
    assert inner[:4] == ["opencode", "run", "-m", "ollama/qwen3:8b"]
    assert inner[inner.index("--dir") + 1] == CONTAINER_WORKSPACE
    assert str(tmp_path) not in " ".join(inner)


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
    assert run.tokens == {"output": 219}  # step_finish tokens now kept (Step 12)


def test_parse_events_sums_tokens_across_steps():
    # Multi-step run: per-key sums, nested cache dict flattened to cache_read/_write.
    jsonl = "\n".join([
        json.dumps({"type": "step_finish", "part": {
            "tokens": {"input": 10, "output": 5, "reasoning": 0,
                       "cache": {"read": 2, "write": 0}}, "cost": 0}}),
        json.dumps({"type": "step_finish", "part": {
            "tokens": {"input": 30, "output": 7, "cache": {"read": 1}}, "cost": 0}}),
    ])
    run = parse_opencode_events(jsonl, server="omd")
    assert run.num_turns == 2
    assert run.tokens == {"input": 40, "output": 12, "reasoning": 0,
                          "cache_read": 3, "cache_write": 0}


def test_parse_events_no_tokens_reported_is_none():
    # None != 0: a stream whose steps carry no token fields yields None, so a
    # provider that reports nothing is distinguishable from one reporting zero.
    jsonl = json.dumps({"type": "step_finish", "part": {"cost": 0}})
    assert parse_opencode_events(jsonl, server="omd").tokens is None


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
# run() against a monkeypatched run_process
# ---------------------------------------------------------------------------


def test_run_writes_config_parses_events_and_passes_budgets(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run_process(argv, timeout_s=None, cwd=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["timeout_s"] = timeout_s
        return ProcOutcome(0, SPIKE_JSONL, "", timed_out=False)

    monkeypatch.setattr(opencode_mod, "run_process", fake_run_process)

    spec = MCPServerSpec.omd(tmp_path)
    result = OpenCodeDriver().run("do the task", spec, tmp_path, model="qwen3:8b",
                                  timeout_s=123.0)

    # Config landed in the workspace and is the rendered dict.
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert cfg["provider"]["ollama"]["models"]["qwen3:8b"] == {"tools": True}
    assert cfg["mcp"]["omd"]["command"][0] == sys.executable

    # The wall-clock budget reaches the process layer (which owns stdin/kill).
    assert captured["timeout_s"] == 123.0
    assert captured["cwd"] == str(tmp_path)
    assert result.final_text == 'report:\n```json\n{"status": "done"}\n```'
    assert [c.tool for c in result.tool_call_trace] == ["start_session"]
    assert result.cost_usd == 0.0
    assert result.num_turns == 1
    assert result.tokens == {"output": 219}
    assert result.wall_clock_s is not None and result.wall_clock_s >= 0
    assert result.timed_out is False

    # Raw events persisted for debuggability.
    assert (tmp_path / "opencode_events.jsonl").read_text() == SPIKE_JSONL

    # omd's instructions reach the model through AGENTS.md (OpenCode forwards
    # neither MCP instructions nor resources). Unsandboxed there is no `read`
    # tool, so the two resources are inlined.
    agents = (tmp_path / "AGENTS.md").read_text()
    assert "MDAO analysis plan server" in agents          # the instructions
    assert "## omd://reference" in agents                  # inlined resource
    assert "oas/AeroPoint" in agents                       # ...with real types
    assert (tmp_path / "omd_reference.md").read_text().startswith("# omd MCP Server")
    assert json.loads((tmp_path / "omd_plan_schema.json").read_text())


def test_sandboxed_run_points_agents_md_at_the_resource_files(monkeypatch, tmp_path):
    from hangar.evals.drivers.sandbox import ContainerSandbox
    seen: dict = {}

    def fake_run_process(argv, timeout_s=None, cwd=None):
        # The files must exist BEFORE opencode starts (it reads AGENTS.md at boot).
        seen["agents_at_launch"] = (tmp_path / "AGENTS.md").read_text()
        return ProcOutcome(0, SPIKE_JSONL, "", timed_out=False)

    monkeypatch.setattr(opencode_mod, "run_process", fake_run_process)
    spec = MCPServerSpec.omd_http("http://127.0.0.1:1/mcp")
    OpenCodeDriver(sandbox=ContainerSandbox(image="img")).run("x", spec, tmp_path)
    agents = seen["agents_at_launch"]
    assert "MDAO analysis plan server" in agents
    assert "./omd_reference.md" in agents and "./omd_plan_schema.json" in agents
    assert "## omd://reference" not in agents               # pointed to, not inlined
    assert "oas/AeroPoint" in (tmp_path / "omd_reference.md").read_text()


def test_run_unloads_the_model_after_the_seed(monkeypatch, tmp_path):
    """Ollama's MLX runner leaks ~0.5 GiB per request (2026-09-22); the driver
    drops the runner after every seed unless told not to."""
    monkeypatch.setattr(opencode_mod, "run_process",
                        lambda *a, **k: ProcOutcome(0, SPIKE_JSONL, "", timed_out=False))
    unloaded = []
    monkeypatch.setattr(opencode_mod, "unload_model",
                        lambda base_url, model: unloaded.append((base_url, model)))
    spec = MCPServerSpec.omd(tmp_path)
    OpenCodeDriver(base_url="http://h:11434/v1").run("x", spec, tmp_path, model="m1")
    assert unloaded == [("http://h:11434/v1", "m1")]
    OpenCodeDriver(unload_after_run=False).run("x", spec, tmp_path, model="m1")
    assert len(unloaded) == 1


def test_unload_model_posts_keep_alive_zero_to_the_native_api(monkeypatch):
    import urllib.request
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert opencode_mod.unload_model("http://localhost:11434/v1", "qwen3.6:35b-mlx") is True
    assert seen == {"url": "http://localhost:11434/api/generate",
                    "body": {"model": "qwen3.6:35b-mlx", "keep_alive": 0}}


def test_unload_model_failure_is_reported_not_raised(monkeypatch):
    import urllib.error
    import urllib.request

    def boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert opencode_mod.unload_model("http://localhost:11434/v1", "m") is False


def test_run_nonzero_exit_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        opencode_mod, "run_process",
        lambda argv, timeout_s=None, cwd=None:
            ProcOutcome(1, "", "provider not found", timed_out=False))
    with pytest.raises(RuntimeError, match="opencode run failed"):
        OpenCodeDriver().run("x", MCPServerSpec.omd(tmp_path), tmp_path)


def test_run_timeout_keeps_partial_evidence_and_does_not_raise(monkeypatch, tmp_path):
    # Step 18: expiry is an OUTCOME, not an error — the partial event stream
    # still yields the trace/report, and the record grades from the DB anyway.
    monkeypatch.setattr(
        opencode_mod, "run_process",
        lambda argv, timeout_s=None, cwd=None:
            ProcOutcome(None, SPIKE_JSONL, "", timed_out=True))

    result = OpenCodeDriver().run("x", MCPServerSpec.omd(tmp_path), tmp_path,
                                  timeout_s=1.0)
    assert result.timed_out is True
    assert [c.tool for c in result.tool_call_trace] == ["start_session"]
    assert (tmp_path / "opencode_events.jsonl").read_text() == SPIKE_JSONL


def test_run_sandboxed_timeout_kills_the_container(monkeypatch, tmp_path):
    # SIGKILL on the docker CLI leaves the container running — the driver must
    # docker-kill it by the name it gave the run.
    from hangar.evals.drivers.sandbox import OPENCODE_IMAGE, ContainerSandbox

    monkeypatch.setattr(
        opencode_mod, "run_process",
        lambda argv, timeout_s=None, cwd=None:
            ProcOutcome(None, "", "", timed_out=True))
    killed: list = []
    monkeypatch.setattr(
        opencode_mod.subprocess, "run",
        lambda argv, **kw: killed.append(argv) or None)
    # the state capture that precedes the kill is covered by its own test
    monkeypatch.setattr(opencode_mod, "capture_container_state",
                        lambda name, dest: {})

    driver = OpenCodeDriver(sandbox=ContainerSandbox(
        image=OPENCODE_IMAGE, env_passthrough=()))
    spec = MCPServerSpec.omd_http("http://host.docker.internal:8123/mcp")
    result = driver.run("x", spec, tmp_path, timeout_s=1.0)

    assert result.timed_out is True
    assert killed == [["docker", "kill", f"hangar_{tmp_path.name}"]]


def test_build_argv_sandboxed_names_the_container(tmp_path):
    from hangar.evals.drivers.sandbox import OPENCODE_IMAGE, ContainerSandbox

    driver = OpenCodeDriver(sandbox=ContainerSandbox(
        image=OPENCODE_IMAGE, env_passthrough=()))
    argv = driver.build_argv("task", tmp_path, "qwen3:8b", container="hangar_x1")
    assert argv[argv.index("--name") + 1] == "hangar_x1"
    # Unsandboxed there is no docker wrapper, so no --name either.
    assert "--name" not in OpenCodeDriver().build_argv("task", tmp_path, "qwen3:8b")


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


def test_a_tool_that_raised_is_a_failed_call_not_a_valid_one():
    """Seen on the 2026-09-21 gemma arm: run_plan given a plan DIRECTORY raises
    inside omd; FastMCP renders the exception as plain text with no error
    envelope and OpenCode still marks the call "completed". That is not a
    valid call -- it must count against Valid%, coded TOOL_EXCEPTION."""
    stream = "\n".join([
        json.dumps({"type": "tool_use", "part": {
            "type": "tool", "tool": "omd_run_plan", "callID": "c1",
            "state": {"status": "completed", "input": {"plan_path": "plans/x"},
                      "output": "Error executing tool run_plan: [Errno 21] "
                                "Is a directory: '/data/plans/x'"}}}),
        json.dumps({"type": "tool_use", "part": {
            "type": "tool", "tool": "omd_run_plan", "callID": "c2",
            "state": {"status": "completed", "input": {"plan_path": "plans/x/plan.yaml"},
                      "output": '{"run_id": "run-1", "results": {}}'}}}),
        json.dumps({"type": "step_finish", "part": {"cost": 0}}),
    ])
    run = parse_opencode_events(stream, server="omd")
    raised, fine = run.tool_calls
    assert raised.tool == "run_plan" and not raised.ok
    assert raised.error_code == "TOOL_EXCEPTION"
    assert fine.ok and fine.error_code is None


# ---------------------------------------------------------------------------
# A timed-out sandboxed run captures the container before killing it and
# leaves a timeout note (2026-09-23: three qwen seeds stalled with the model
# idle and nothing on disk named the hung tool call).
# ---------------------------------------------------------------------------


def test_timed_out_sandboxed_run_captures_state_before_kill(monkeypatch, tmp_path):
    from hangar.evals.drivers.sandbox import ContainerSandbox

    order: list = []
    stalled_jsonl = SPIKE_JSONL.rstrip("\n") + "\n" + json.dumps(
        {"type": "step_start", "timestamp": 1_700_000_000_000, "part": {}}) + "\n"
    monkeypatch.setattr(opencode_mod, "run_process",
                        lambda *a, **k: ProcOutcome(None, stalled_jsonl,
                                                    "some stderr", timed_out=True))
    monkeypatch.setattr(opencode_mod, "capture_container_state",
                        lambda name, dest: order.append(("capture", name, dest)) or {})
    monkeypatch.setattr(opencode_mod.subprocess, "run",
                        lambda argv, **k: order.append(("run", argv)))
    monkeypatch.setattr(opencode_mod, "unload_model", lambda *a, **k: True)

    spec = MCPServerSpec.omd_http("http://127.0.0.1:1/mcp")
    result = OpenCodeDriver(sandbox=ContainerSandbox(image="img")).run(
        "x", spec, tmp_path, model="qwen3:8b", timeout_s=5.0)

    assert result.timed_out is True
    container = f"hangar_{tmp_path.name}"
    assert order[0] == ("capture", container, tmp_path)          # before ...
    assert order[1] == ("run", ["docker", "kill", container])     # ... the kill
    assert (tmp_path / "opencode_stderr.txt").read_text() == "some stderr"
    note = json.loads((tmp_path / "opencode_timeout.json").read_text())
    assert note["timed_out"] is True
    assert note["last_event_utc"] == "2023-11-14T22:13:20Z"
    assert note["silent_s"] > 0
    assert "/v1/chat/completions" in note["how_to_read"]


def test_completed_run_writes_stderr_but_no_timeout_note(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode_mod, "run_process",
                        lambda *a, **k: ProcOutcome(0, SPIKE_JSONL, "", timed_out=False))
    monkeypatch.setattr(opencode_mod, "unload_model", lambda *a, **k: True)
    OpenCodeDriver().run("x", MCPServerSpec.omd(tmp_path), tmp_path)
    assert (tmp_path / "opencode_stderr.txt").read_text() == ""
    assert not (tmp_path / "opencode_timeout.json").exists()


def test_timeout_note_without_events_has_no_last_event():
    note = opencode_mod.timeout_note("", 1100.0)
    assert note["last_event_utc"] is None and note["silent_s"] is None
    assert note["wall_clock_s"] == 1100.0
