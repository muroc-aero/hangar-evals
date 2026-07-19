"""Tests for the runner — run_cell with a fake driver (offline).

compute_refs runs the real paraboloid Lane A through the seam (needs the-hangar
in the interpreter), but no agent is launched: a fake driver returns a canned
report + tool trace — and optionally drops the fixture provenance DB into the
run's ``data_root``, standing in for "the agent actually ran omd" (Step 11:
the PRIMARY grade reads those side effects, not the report).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hangar.evals import run as run_mod
from hangar.evals.cases import CASES, build_prompt
from hangar.evals.drivers.base import AgentResult
from hangar.evals.run import RunConfig, run_cell, run_matrix
from hangar.evals.scoring import compute_refs
from hangar.evals.trace import ToolCall

FIXTURE_DB = Path(__file__).parent / "fixtures" / "paraboloid_anchor_passed.db"


class _FakeDriver:
    """Returns a fixed AgentResult; records the prompt it was handed.

    ``db_fixture`` (optional) is copied into ``data_root/analysis.db`` during
    ``run`` — simulating the omd side effects of a real agent run.
    """

    def __init__(self, final_text, trace=None, db_fixture=None, timed_out=False):
        self.final_text = final_text
        self.trace = trace or []
        self.db_fixture = db_fixture
        self.timed_out = timed_out
        self.seen_prompt = None
        self.seen_mcp = None

    def run(self, prompt, mcp, data_root, model=None, max_turns=80,
            timeout_s=None):
        self.seen_prompt = prompt
        self.seen_mcp = mcp
        self.seen_dir = Path(data_root)
        self.seen_max_turns = max_turns
        self.seen_timeout_s = timeout_s
        if self.db_fixture is not None:
            shutil.copy(self.db_fixture, Path(data_root) / "analysis.db")
        return AgentResult(
            final_text=self.final_text,
            cost_usd=0.0,
            wall_clock_s=1.0,
            num_turns=3,
            tool_call_trace=self.trace,
            tokens={"input": 1000, "output": 250},
            timed_out=self.timed_out,
        )


def _correct_report():
    case = CASES["paraboloid"]
    refs = compute_refs(case.example, case.metrics)
    return (
        'done\n```json\n{"metrics": {'
        f'"analysis_f_xy": {refs["analysis"]["f_xy"]}, '
        f'"opt_f_xy": {refs["optimization"]["f_xy"]}, '
        f'"opt_x": {refs["optimization"]["x"]}, '
        f'"opt_y": {refs["optimization"]["y"]}'
        '}}\n```'
    )


def test_run_cell_effects_pass_even_without_report(tmp_path):
    # The seed-0 injustice, fixed: the agent RAN the right analyses (fixture DB)
    # but emitted prose instead of the fenced JSON -> effect-PASS, report-unparsed.
    (tmp_path / "run_data").mkdir()
    trace = [ToolCall("start_session", ok=True), ToolCall("run_plan", ok=True)]
    driver = _FakeDriver("here are my findings, in prose...", trace,
                         db_fixture=FIXTURE_DB)

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["completed"] is True            # >=1 successful execute
    assert rec["passed"] is True               # effect-graded
    assert {s["verdict"] for s in rec["scores"]} == {"PASS"}
    assert rec["reporting"]["parsed"] is False
    assert rec["oracle"]["n_executed_ok"] == 2
    assert rec["oracle"]["ambiguity"] == 0
    # Provenance metrics still read from the same DB.
    assert rec["provenance"]["n_activities"] > 0
    assert rec["tool_use"]["total_calls"] == 2
    # Driver-reported tokens land in telemetry (Step 12).
    assert rec["telemetry"]["tokens"] == {"input": 1000, "output": 250}
    # The agent got the real task prompt with the report format.
    assert "REPORT FORMAT" in driver.seen_prompt
    assert "Paraboloid" in driver.seen_prompt


def test_run_cell_report_alone_cannot_pass(tmp_path):
    # The tau-bench forged-report guard: a CORRECT fenced-JSON report with no
    # omd runs behind it scores zero — there is nothing to grade.
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver(_correct_report())

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["completed"] is False           # nothing executed
    assert rec["passed"] is False
    assert {s["verdict"] for s in rec["scores"]} == {"FAIL"}
    # The report itself was fine — recorded as such, but only as fidelity.
    assert rec["reporting"]["parsed"] is True
    assert rec["reporting"]["passed"] is True
    assert rec["reporting"]["matches_effects"] is None  # no effects to match
    assert rec["oracle"]["n_runs"] == 0


def test_run_cell_consistent_report_matches_effects(tmp_path):
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver(_correct_report(), db_fixture=FIXTURE_DB)

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["passed"] is True
    assert rec["reporting"]["parsed"] is True
    assert rec["reporting"]["passed"] is True
    assert rec["reporting"]["matches_effects"] is True


def test_run_cell_no_report_no_runs_is_incomplete(tmp_path):
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("the model rambled but emitted no fenced json")

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["completed"] is False
    assert rec["passed"] is False
    # Effect scores always exist now — all FAIL when nothing ran.
    assert {s["verdict"] for s in rec["scores"]} == {"FAIL"}
    assert rec["reporting"] == {"parsed": False, "passed": None,
                                "matches_effects": None, "scores": None}


def test_run_cell_applies_case_budgets_and_overrides(tmp_path):
    # Step 18: with no overrides the driver sees the CASE's budgets; explicit
    # runner overrides win. Both land in telemetry either way.
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("prose", db_fixture=FIXTURE_DB)
    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)
    assert driver.seen_max_turns == 80
    assert driver.seen_timeout_s == 900.0
    assert rec["telemetry"]["max_turns"] == 80
    assert rec["telemetry"]["timeout_s"] == 900.0
    assert rec["telemetry"]["timed_out"] is False

    driver = _FakeDriver("prose", db_fixture=FIXTURE_DB)
    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path,
                   max_turns=12, timeout_s=60.0)
    assert driver.seen_max_turns == 12
    assert driver.seen_timeout_s == 60.0
    assert rec["telemetry"]["timeout_s"] == 60.0


def test_run_cell_timed_out_run_is_still_effect_graded(tmp_path):
    # Both observed SDK failures happened AFTER the physics finished — a
    # killed run whose provenance DB holds the right runs must still PASS,
    # with the timeout visible in telemetry.
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("no report, run was killed", db_fixture=FIXTURE_DB,
                         timed_out=True)
    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)
    assert rec["telemetry"]["timed_out"] is True
    assert rec["passed"] is True
    assert rec["reporting"]["parsed"] is False


def test_run_cell_populates_ref_cache(tmp_path):
    # The Lane A disk cache lands under results_dir/ref_cache (keyed on the
    # the-hangar SHA) whenever that checkout is clean; a dirty checkout
    # legitimately leaves it empty, so only the clean case is asserted.
    from hangar.evals.hangar_ref import _repo_state, resolve_hangar_repo

    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("prose", db_fixture=FIXTURE_DB)
    run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)
    state = _repo_state(resolve_hangar_repo())
    if state is not None and not state[1]:
        cached = list((tmp_path / "ref_cache").glob("paraboloid.*.json"))
        assert {p.name.split(".")[1] for p in cached} == {"analysis", "optimization"}


def test_build_prompt_is_harness_neutral():
    prompt = build_prompt(CASES["paraboloid"])
    assert "mcp__omd__" not in prompt   # no Claude-specific tool namespace
    assert "omd_start_session" not in prompt
    assert "ONLY the omd tools" in prompt


# --- RunConfig: the scriptable/reproducible unit -------------------------------


def test_run_config_round_trips_through_json():
    cfg = RunConfig(case="paraboloid", harnesses=("opencode", "claude"),
                    model="qwen3.6:35b-mlx", seeds=3, max_turns=40)
    again = RunConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert again == cfg
    assert isinstance(again.harnesses, tuple)   # JSON array -> tuple


def test_run_config_from_json_file(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"case": "paraboloid", "harnesses": ["opencode"],
                             "model": "qwen3.6:35b-mlx", "seeds": 5}))
    cfg = RunConfig.from_json_file(p)
    assert cfg.model == "qwen3.6:35b-mlx" and cfg.seeds == 5
    assert cfg.harnesses == ("opencode",)


def test_run_config_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unknown keys"):
        RunConfig.from_dict({"case": "paraboloid", "temperature": 0.7})


def test_run_config_accepts_manifest_shape():
    # `--config <manifest>` (Step 12 fix): the wrapper keys stamp/environment
    # are ignored and the config is read from the "config" key.
    cfg = RunConfig(case="paraboloid", harnesses=("claude",), seeds=2)
    manifest = {"stamp": "20260717T000000Z",
                "environment": {"python": "3.12.0"},
                "config": cfg.to_dict()}
    assert RunConfig.from_dict(manifest) == cfg


def test_run_config_omd_transport_round_trips_and_validates():
    # Step 13: the transport is config, so parity runs are scriptable and the
    # manifest self-describes how the agent reached omd.
    cfg = RunConfig(case="paraboloid", harnesses=("claude",), seeds=1,
                    omd_transport="http")
    assert RunConfig.from_dict(json.loads(json.dumps(cfg.to_dict()))) == cfg
    with pytest.raises(ValueError, match="omd_transport"):
        RunConfig(omd_transport="carrier-pigeon")


def test_run_cell_http_transport_uses_service_and_records_it(tmp_path, monkeypatch):
    # The http branch hands the driver a url-only spec from OmdHttpService
    # (faked here — the real lifecycle is test_omd_service.py's job) and the
    # record's telemetry names the transport.
    class _FakeService:
        def __init__(self, data_root, host="127.0.0.1", advertise_host=None,
                     startup_timeout_s=120.0):
            self.data_root = data_root

        def __enter__(self):
            return run_mod.MCPServerSpec.omd_http("http://127.0.0.1:8123/mcp")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(run_mod, "OmdHttpService", _FakeService)
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("prose only", db_fixture=FIXTURE_DB)

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path,
                   omd_transport="http")

    assert driver.seen_mcp.transport == "http"
    assert driver.seen_mcp.url == "http://127.0.0.1:8123/mcp"
    assert rec["telemetry"]["omd_transport"] == "http"
    assert rec["passed"] is True    # grading unchanged by the channel


def test_run_cell_default_transport_is_stdio(tmp_path):
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("prose", db_fixture=FIXTURE_DB)
    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)
    assert driver.seen_mcp.transport == "stdio"
    assert rec["telemetry"]["omd_transport"] == "stdio"


def test_run_config_sandbox_round_trips_and_validates():
    # Step 14a: the sandbox is config, and it structurally requires the http
    # transport — stdio omd inside the container would make the grading
    # evidence agent-writable.
    cfg = RunConfig(case="paraboloid", harnesses=("claude",), seeds=1,
                    omd_transport="http", sandbox="container")
    assert RunConfig.from_dict(json.loads(json.dumps(cfg.to_dict()))) == cfg
    with pytest.raises(ValueError, match="sandbox"):
        RunConfig(sandbox="chroot")
    with pytest.raises(ValueError, match="omd_transport='http'"):
        RunConfig(sandbox="container")   # default transport is stdio


def test_run_cell_sandboxed_routes_driver_to_workspace(tmp_path, monkeypatch):
    # The privilege split itself: the driver gets the external workspace, the
    # oracle keeps data_root, and the omd spec advertises the container-facing
    # host name.
    seen = {}

    class _FakeService:
        def __init__(self, data_root, host="127.0.0.1", advertise_host=None,
                     startup_timeout_s=120.0):
            seen["advertise_host"] = advertise_host

        def __enter__(self):
            return run_mod.MCPServerSpec.omd_http(
                "http://host.docker.internal:8123/mcp")

        def __exit__(self, *exc):
            return False

    ws = tmp_path / "external_ws"
    monkeypatch.setattr(run_mod, "OmdHttpService", _FakeService)
    monkeypatch.setattr(run_mod, "make_workspace", lambda prefix: ws)
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("prose only")

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path,
                   omd_transport="http", sandbox="container")

    assert seen["advertise_host"] == "host.docker.internal"
    assert driver.seen_dir == ws                      # agent's world
    assert Path(rec["data_root"]) != ws               # oracle's world
    assert rec["workspace"] == str(ws)
    assert rec["telemetry"]["sandbox"] == "container"


def test_run_matrix_sandboxed_claude_uses_cli_driver(tmp_path, monkeypatch):
    from hangar.evals.drivers.claude_cli import ClaudeCliDriver

    seen = {}

    def fake_run_cell(case, driver, harness, model, seed, results_dir, max_turns,
                      omd_transport="stdio", sandbox="none", timeout_s=None):
        seen["driver"] = driver
        return _fake_record(seed, passed=True)

    monkeypatch.setattr(run_mod, "run_cell", fake_run_cell)
    cfg = RunConfig(case="paraboloid", harnesses=("claude",), seeds=1,
                    results_dir=str(tmp_path),
                    omd_transport="http", sandbox="container")
    run_matrix(cfg, stamp="20260718T000000Z")
    assert isinstance(seen["driver"], ClaudeCliDriver)


def test_run_matrix_sandboxed_opencode_gets_the_local_arm_sandbox(tmp_path, monkeypatch):
    # Step 14b: opencode × container is no longer an error — the driver is the
    # ordinary OpenCodeDriver wrapped in the local-arm sandbox: the pinned
    # opencode image, and NO env passthrough (the local arm carries no token).
    from hangar.evals.drivers.opencode import OpenCodeDriver
    from hangar.evals.drivers.sandbox import OPENCODE_IMAGE

    seen = {}

    def fake_run_cell(case, driver, harness, model, seed, results_dir, max_turns,
                      omd_transport="stdio", sandbox="none", timeout_s=None):
        seen["driver"] = driver
        return _fake_record(seed, passed=True)

    monkeypatch.setattr(run_mod, "run_cell", fake_run_cell)
    cfg = RunConfig(case="paraboloid", harnesses=("opencode",), seeds=1,
                    results_dir=str(tmp_path),
                    omd_transport="http", sandbox="container")
    run_matrix(cfg, stamp="20260719T000000Z")
    driver = seen["driver"]
    assert isinstance(driver, OpenCodeDriver)
    assert driver.sandbox is not None
    assert driver.sandbox.image == OPENCODE_IMAGE
    assert driver.sandbox.env_passthrough == ()


def test_claude_anchor_model_is_pinned():
    # Decision 2 (spec §4d): "SDK default" must not be a reachable state — the
    # anchor model is a literal string, so records/manifests always name it.
    assert run_mod.HARNESSES["claude"][1] == "claude-opus-4-8"


# --- run_matrix: multi-seed wiring + manifest (run_cell faked) ------------------


def _fake_record(seed, *, passed):
    """A complete per-seed record (every field _print_summary/aggregate read)."""
    return {
        "case": "paraboloid", "harness": "fake", "model": "m0", "seed": seed,
        "completed": True, "passed": passed,
        "scores": [{"key": "analysis_f_xy", "lane_a": 39.0,
                    "agent": 39.0 if passed else 22.0, "rel_err": 0.0,
                    "verdict": "PASS" if passed else "FAIL"}],
        "tool_use": {"total_calls": 5, "valid_call_rate": 0.9, "schema_errors": 0,
                     "hallucinated_calls": 0, "recovered_errors": 0},
        "telemetry": {"num_turns": 10 + seed, "wall_clock_s": 100.0 + seed,
                      "cost_usd": 0.0},
    }


def test_run_matrix_writes_records_manifest_and_summary(monkeypatch, tmp_path):
    # seed 1 fails, seeds 0 and 2 pass -> 2/3 pass-rate, no driver/the-hangar.
    def fake_run_cell(case, driver, harness, model, seed, results_dir, max_turns,
                      omd_transport="stdio", sandbox="none", timeout_s=None):
        return _fake_record(seed, passed=(seed != 1))

    monkeypatch.setattr(run_mod, "run_cell", fake_run_cell)
    monkeypatch.setitem(run_mod.HARNESSES, "fake", (lambda: object(), "m0"))

    cfg = RunConfig(case="paraboloid", harnesses=("fake",), seeds=3,
                    results_dir=str(tmp_path))
    summaries = run_matrix(cfg, stamp="20260625T000000Z")

    assert len(summaries) == 1
    s = summaries[0]
    assert s.n_passed == 2 and s.n_seeds == 3 and s.completion_rate == 1.0

    base = tmp_path / "paraboloid_20260625T000000Z"
    # Per-seed records: one JSON line per seed.
    lines = base.with_suffix(".jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    # Manifest reproduces the run via --config — the WHOLE manifest file round-
    # trips (Step 12 fix), and it pins the observed environment.
    manifest_path = tmp_path / "paraboloid_20260625T000000Z_config.json"
    manifest = json.loads(manifest_path.read_text())
    assert set(manifest) == {"stamp", "environment", "config"}
    assert manifest["config"] == cfg.to_dict()
    assert RunConfig.from_json_file(manifest_path) == cfg
    env = manifest["environment"]
    assert isinstance(env["hangar_evals"], dict)   # SHA captured in this checkout
    assert len(env["hangar_evals"]["sha"]) == 40
    # Summary persisted as JSON-shaped CellSummary list.
    summ = json.loads((tmp_path / "paraboloid_20260625T000000Z_summary.json").read_text())
    assert summ[0]["pass_rate"] == pytest.approx(2 / 3)
    assert summ[0]["per_metric_pass"] == {"analysis_f_xy": 2}


def test_run_matrix_rejects_unknown_harness(tmp_path):
    cfg = RunConfig(case="paraboloid", harnesses=("nope",), seeds=1,
                    results_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown harness"):
        run_matrix(cfg, stamp="20260625T000000Z")


# --- error rows + resume (Step 18) ---------------------------------------------


def test_run_config_budget_overrides_round_trip():
    # Step 18: both budget overrides are config (so manifests reproduce them);
    # None means "per-case" and old manifests with max_turns: 80 still load.
    cfg = RunConfig(case="paraboloid", harnesses=("claude",), seeds=1,
                    max_turns=40, timeout_s=1200.0)
    assert RunConfig.from_dict(json.loads(json.dumps(cfg.to_dict()))) == cfg
    assert RunConfig().max_turns is None and RunConfig().timeout_s is None
    old = RunConfig.from_dict({"case": "paraboloid", "harnesses": ["opencode"],
                               "seeds": 3, "max_turns": 80})
    assert old.max_turns == 80


def test_run_matrix_driver_crash_becomes_error_row_not_abort(monkeypatch, tmp_path):
    # A harness crash on one seed must not take down the matrix: the seed
    # becomes a retryable error row, later seeds still run, aggregation counts
    # it as neither completed nor passed.
    def fake_run_cell(case, driver, harness, model, seed, results_dir, max_turns,
                      omd_transport="stdio", sandbox="none", timeout_s=None):
        if seed == 1:
            raise RuntimeError("SDK exploded mid-run")
        return _fake_record(seed, passed=True)

    monkeypatch.setattr(run_mod, "run_cell", fake_run_cell)
    monkeypatch.setitem(run_mod.HARNESSES, "fake", (lambda: object(), "m0"))

    cfg = RunConfig(case="paraboloid", harnesses=("fake",), seeds=3,
                    results_dir=str(tmp_path))
    summaries = run_matrix(cfg, stamp="20260719T000000Z")

    s = summaries[0]
    assert s.n_seeds == 3 and s.n_passed == 2 and s.n_completed == 2
    rows = [json.loads(line) for line in
            (tmp_path / "paraboloid_20260719T000000Z.jsonl").read_text().splitlines()]
    assert len(rows) == 3
    err = rows[1]
    assert err["seed"] == 1 and err["passed"] is False
    assert err["error"] == {"type": "RuntimeError",
                            "message": "SDK exploded mid-run"}


def test_load_resume_records_retries_errors_and_dedups(tmp_path):
    from hangar.evals.run import load_resume_records

    rows = [
        _fake_record(0, passed=True),
        {**_fake_record(1, passed=False), "error": {"type": "X", "message": "y"}},
        _fake_record(2, passed=False),          # superseded below
        _fake_record(2, passed=True),           # the retry — last row wins
    ]
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    kept = load_resume_records(p)               # default: retry errors
    assert {r["seed"] for r in kept} == {0, 2}
    assert next(r for r in kept if r["seed"] == 2)["passed"] is True

    kept = load_resume_records(p, retry_errors=False)
    assert {r["seed"] for r in kept} == {0, 1, 2}


def test_run_matrix_resume_reruns_only_missing_seeds(monkeypatch, tmp_path):
    # Prior run: seed 0 passed, seed 1 errored, seed 2 never ran (crash).
    # Resume must rerun exactly seeds 1 and 2, append to the same records
    # file, and aggregate the union.
    from hangar.evals.run import load_resume_records

    stamp = "20260719T010101Z"
    records_path = tmp_path / f"paraboloid_{stamp}.jsonl"
    prior = [
        _fake_record(0, passed=True),
        {**_fake_record(1, passed=False), "error": {"type": "X", "message": "y"}},
    ]
    records_path.write_text("\n".join(json.dumps(r) for r in prior) + "\n")

    ran: list[int] = []

    def fake_run_cell(case, driver, harness, model, seed, results_dir, max_turns,
                      omd_transport="stdio", sandbox="none", timeout_s=None):
        ran.append(seed)
        return _fake_record(seed, passed=True)

    monkeypatch.setattr(run_mod, "run_cell", fake_run_cell)
    monkeypatch.setitem(run_mod.HARNESSES, "fake", (lambda: object(), "m0"))

    cfg = RunConfig(case="paraboloid", harnesses=("fake",), seeds=3,
                    results_dir=str(tmp_path))
    summaries = run_matrix(cfg, stamp,
                           resume_records=load_resume_records(records_path))

    assert ran == [1, 2]                        # seed 0 reused, error retried
    s = summaries[0]
    assert s.n_seeds == 3 and s.n_passed == 3
    # Appended, not rewritten: 2 prior rows + 2 new ones; latest-per-seed is
    # what load_resume_records would reconstruct on a further resume.
    rows = [json.loads(line) for line in records_path.read_text().splitlines()]
    assert len(rows) == 4
    latest = load_resume_records(records_path)
    assert {r["seed"]: r["passed"] for r in latest} == {0: True, 1: True, 2: True}
