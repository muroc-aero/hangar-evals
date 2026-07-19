"""Runner — the model × harness × task matrix, multi-seed, end to end.

A *cell* is (case, harness, model); ``run_cell`` runs ONE seed of it — builds
the prompt, drives the agent, and grades it. The PRIMARY grade is
**effect-based** (Step 11): the omd run outputs the agent actually produced
(read from the run's provenance DB via ``oracle.py``) versus Lane A. The
fenced-JSON self-report is a SECONDARY *reporting fidelity* signal, and the
tool trace / provenance DB add tool-use and workflow metrics. Because a single
local-model run is noise (Step 9), each cell is run N seeds and
``aggregate_cell`` reduces them to a pass-rate ``CellSummary``.

A whole run is described by a ``RunConfig``, which makes runs **scriptable and
reproducible** two ways:

    # 1. a JSON config file (every run also writes one as a manifest):
    python -m hangar.evals.run --config configs/paraboloid_q36.json

    # 2. your own Python script:
    from hangar.evals.run import RunConfig, run_matrix
    run_matrix(RunConfig(case="paraboloid", harnesses=("opencode",),
                         model="qwen3.6:35b-mlx", seeds=3), stamp="...")

Or the plain CLI flags:

    python -m hangar.evals.run --case paraboloid --harness opencode --seeds 3

Each run writes three siblings in ``results/``: ``<case>_<stamp>.jsonl`` (per-seed
records), ``<case>_<stamp>_config.json`` (the manifest — re-run via ``--config``;
it also pins the OBSERVED environment: git SHAs, tool/SDK versions, platform),
and ``<case>_<stamp>_summary.json`` (the per-cell summaries). The random seed is
NOT yet reproducible, but the *matrix* is. Scoring is held constant; only the
driver/model vary — that's the whole point.

Runs are checkpointed (Step 18): each seed flushes to the ``.jsonl`` as it
finishes, a harness crash becomes a retryable error row instead of killing the
matrix, and every agent run is bounded by its case's wall-clock budget (the
driver kills the process tree on expiry; the seed still effect-grades from the
provenance DB). ``--resume <records.jsonl>`` reruns only the missing seeds —
error rows are retried by default (``--keep-errors`` to keep them).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hangar.evals.aggregate import CellSummary, aggregate_cell
from hangar.evals.cases import CASES, Case, build_prompt
from hangar.evals.drivers.base import MCPServerSpec
from hangar.evals.drivers.claude_cli import ClaudeCliDriver
from hangar.evals.drivers.claude_sdk import ClaudeAgentSDKDriver
from hangar.evals.drivers.opencode import OpenCodeDriver
from hangar.evals.drivers.sandbox import (
    OPENCODE_IMAGE,
    ContainerSandbox,
    make_workspace,
)
from hangar.evals.environment import capture_environment
from hangar.evals.omd_service import OmdHttpService
from hangar.evals.oracle import (
    effect_values,
    oracle_ambiguity,
    read_effect_runs,
    report_matches_effects,
)
from hangar.evals.scoring import (
    compute_refs,
    extract_report,
    for_reporting,
    score_report,
    score_values,
)
from hangar.evals.trace import parse_tool_trace, read_provenance

# harness name -> (driver factory, default model). The Claude anchor is pinned
# to a LITERAL model id (Step 12): a None default meant "whatever the SDK
# defaults to today", which drifts silently across SDK updates — a model is now
# always an explicit string in records and manifests. OpenCode floors to the
# pulled smoke model.
HARNESSES = {
    "claude": (ClaudeAgentSDKDriver, "claude-opus-4-8"),
    "opencode": (OpenCodeDriver, "qwen3:8b"),
}

_CONFIG_KEYS = ("case", "harnesses", "model", "seeds", "max_turns", "timeout_s",
                "results_dir", "omd_transport", "sandbox")


@dataclass(frozen=True)
class RunConfig:
    """A full, serializable description of one eval run — the scriptable unit.

    ``model`` overrides every harness's default when set. Round-trips to/from a
    JSON config file so a run can be reproduced by ``--config <manifest>`` or
    rebuilt in a Python script (modulo the not-yet-reproducible random seed).

    ``max_turns`` / ``timeout_s`` (Step 18) are GLOBAL overrides; ``None``
    (the default) defers to each case's own budgets (``Case.max_turns`` /
    ``Case.timeout_s``). Old manifests that carry ``max_turns: 80`` reproduce
    unchanged — 80 is every case's turn budget anyway.

    ``omd_transport`` picks how the agent reaches omd: ``"stdio"`` (default —
    the harness spawns it as a child) or ``"http"`` (Step 13 — a host-side
    ``OmdHttpService`` per seed, the sandbox-ready channel).

    ``sandbox`` (Step 14a): ``"container"`` runs the agent in a colima/docker
    container with ONLY a scratch workspace mounted. Requires
    ``omd_transport="http"`` — a stdio omd child inside the container would
    share the agent's privilege domain, making the provenance DB (the PRIMARY
    grading evidence) forgeable.
    """

    case: str = "paraboloid"
    harnesses: tuple[str, ...] = ("opencode",)
    model: str | None = None
    seeds: int = 3
    max_turns: int | None = None
    timeout_s: float | None = None
    results_dir: str = "results"
    omd_transport: str = "stdio"
    sandbox: str = "none"

    def __post_init__(self):
        if self.omd_transport not in ("stdio", "http"):
            raise ValueError(
                f"omd_transport must be 'stdio' or 'http', got {self.omd_transport!r}")
        if self.sandbox not in ("none", "container"):
            raise ValueError(
                f"sandbox must be 'none' or 'container', got {self.sandbox!r}")
        if self.sandbox == "container" and self.omd_transport != "http":
            raise ValueError(
                "sandbox='container' requires omd_transport='http': omd must run "
                "host-side or the agent could forge the grading evidence")

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in _CONFIG_KEYS}
        d["harnesses"] = list(self.harnesses)  # tuple -> JSON array
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RunConfig":
        # Accept a run manifest ({"stamp", "environment", "config"}) as well as
        # a bare config, so `--config <manifest>` really does reproduce a run
        # (Step 12 fix — the wrapper keys used to be rejected as unknown).
        # stamp/environment are observed outputs, not config — ignored.
        if "config" in d:
            d = d["config"]
        unknown = set(d) - set(_CONFIG_KEYS)
        if unknown:
            raise ValueError(f"RunConfig: unknown keys {sorted(unknown)}")
        d = dict(d)
        if "harnesses" in d:
            d["harnesses"] = tuple(d["harnesses"])
        return cls(**d)

    @classmethod
    def from_json_file(cls, path) -> "RunConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))


def run_cell(
    case: Case,
    driver,
    harness: str,
    model: str | None,
    seed: int,
    results_dir: Path,
    max_turns: int | None = None,
    omd_transport: str = "stdio",
    sandbox: str = "none",
    timeout_s: float | None = None,
) -> dict:
    """Run one cell and return its result record.

    ``max_turns`` / ``timeout_s`` override the case's own budgets when set
    (Step 18); the timeout is a HARD wall-clock cap enforced by the driver —
    on expiry the agent is killed and the seed is still graded from whatever
    omd runs its provenance DB holds.
    """
    turn_budget = max_turns if max_turns is not None else case.max_turns
    wall_budget = timeout_s if timeout_s is not None else case.timeout_s
    data_root = Path(tempfile.mkdtemp(
        prefix=f"{case.name}_{harness}_s{seed}_", dir=str(results_dir / "run_data")
    )).resolve()

    # Sandboxed (Step 14a): the agent's world is a scratch workspace OUTSIDE
    # both repos — the only path mounted into the container. data_root (omd
    # state, the grading evidence) stays host-only and is never mounted.
    sandboxed = sandbox == "container"
    workspace = (make_workspace(f"{case.name}_{harness}_s{seed}")
                 if sandboxed else None)

    # Either way omd's state lands under data_root, where the oracle reads it.
    # http (Step 13): omd runs host-side for the seed's duration; the driver
    # gets a url-only spec (no filesystem path crosses to the agent's config).
    # Sandboxed, the spec advertises host.docker.internal (colima forwards it
    # to the host loopback) while the bind stays loopback.
    server = (OmdHttpService(
                  data_root,
                  advertise_host="host.docker.internal" if sandboxed else None)
              if omd_transport == "http"
              else nullcontext(MCPServerSpec.omd(data_root)))
    with server as mcp:
        result = driver.run(
            build_prompt(case), mcp, workspace if sandboxed else data_root,
            model=model, max_turns=turn_budget, timeout_s=wall_budget,
        )

    # Disk-cached against the-hangar's SHA (Step 18) + memoized in-process, so
    # N seeds and repeat runs don't recompute (ocp_three_tool refs: ~70 min).
    refs = compute_refs(case.example, case.metrics,
                        cache_dir=results_dir / "ref_cache")
    db = data_root / "analysis.db"

    # PRIMARY — effect-based (Step 11): grade the omd runs the agent actually
    # produced. No successful run of a metric's mode -> that metric FAILs, so
    # a no-op (or forged-report) run cannot pass.
    runs = read_effect_runs(db) if db.exists() else []
    effects = effect_values(case.metrics, runs)
    effect_score = score_values(case.metrics, effects, refs)
    completed = any(r.executed_ok for r in runs)

    # SECONDARY — reporting fidelity: did it also SAY what it did?
    try:
        report = extract_report(result.final_text)
    except ValueError:
        report = None
    report_score = (
        score_report(for_reporting(case.metrics), report, refs)
        if report is not None else None
    )
    matches = (
        report_matches_effects(case.metrics, report, effects)
        if report is not None else None
    )

    # Tool-use (harness trace) + workflow adherence (provenance DB).
    trace = result.tool_call_trace or []
    tool_metrics = parse_tool_trace(trace)
    prov = read_provenance(db) if db.exists() else None

    return {
        "case": case.name,
        "harness": harness,
        "model": model,
        "seed": seed,
        # >=1 successful execute (Step 11; was: "emitted parseable JSON").
        "completed": completed,
        "passed": effect_score.passed,
        "scores": _scores_to_dicts(effect_score),   # PRIMARY: effect-graded
        "reporting": {
            "parsed": report is not None,
            "passed": (report_score.passed if report_score else None),
            "matches_effects": matches,
            "scores": _scores_to_dicts(report_score),
        },
        "oracle": {
            "n_runs": len(runs),
            "n_executed_ok": sum(r.executed_ok for r in runs),
            "ambiguity": oracle_ambiguity(case.metrics, runs),
            "runs": [
                {"run_id": r.run_id, "mode": r.mode,
                 "executed_ok": r.executed_ok, "assess_status": r.assess_status}
                for r in runs
            ],
        },
        "tool_use": _tool_metrics_to_dict(tool_metrics),
        # Per-call trace, so the record shows WHICH tools ran, not just counts.
        "tool_trace": [
            {"tool": c.tool, "ok": c.ok, "error_code": c.error_code} for c in trace
        ],
        "provenance": _prov_to_dict(prov),
        "telemetry": {
            "wall_clock_s": result.wall_clock_s,
            "cost_usd": result.cost_usd,
            "num_turns": result.num_turns,
            # The budgets this seed ran under, and whether the wall-clock one
            # fired (a timed-out seed is killed but still effect-graded).
            "max_turns": turn_budget,
            "timeout_s": wall_budget,
            "timed_out": result.timed_out,
            # Normalized token counts; None when the harness reported none
            # (third-party drivers that never set it still work).
            "tokens": result.tokens,
            # How the agent reached omd — parity runs are self-describing.
            "omd_transport": omd_transport,
            "sandbox": sandbox,
            # Image drift stays visible: the exact container image, when any.
            "sandbox_image": getattr(getattr(driver, "sandbox", None), "image", None),
        },
        "data_root": str(data_root),
        "workspace": str(workspace) if workspace else None,
    }


def _scores_to_dicts(score) -> list[dict] | None:
    if score is None:
        return None
    return [
        {"key": s.key, "lane_a": s.lane_a, "agent": s.agent,
         "rel_err": s.rel_err, "verdict": s.verdict}
        for s in score.scores
    ]


def _tool_metrics_to_dict(m) -> dict:
    return {
        "total_calls": m.total_calls,
        "valid_calls": m.valid_calls,
        "failed_calls": m.failed_calls,
        "schema_errors": m.schema_errors,
        "hallucinated_calls": m.hallucinated_calls,
        "recovered_errors": m.recovered_errors,
        "valid_call_rate": m.valid_call_rate,
        "schema_error_rate": m.schema_error_rate,
        "hallucinated_rate": m.hallucinated_rate,
        "recovery_rate": m.recovery_rate,
        "validated_before_execute": m.validated_before_execute,
    }


def _prov_to_dict(p) -> dict | None:
    if p is None:
        return None
    return {
        "activity_order": p.activity_order,
        "entity_types": p.entity_types,
        "n_activities": p.n_activities,
        "n_failed": p.n_failed,
        "activity_success_rate": p.activity_success_rate,
        "recovered_activities": p.recovered_activities,
        "has_decision": p.has_decision,
    }


def _error_record(
    case: Case,
    harness: str,
    model: str | None,
    seed: int,
    exc: Exception,
    omd_transport: str,
    sandbox: str,
) -> dict:
    """The record for a seed whose harness CRASHED (Step 18).

    Same top-level shape as a ``run_cell`` record (aggregation and resume read
    both uniformly); the ``error`` key marks it retryable — ``--resume`` reruns
    these rows by default. Not used for timeouts: a timed-out seed still
    produces a full, effect-graded record.
    """
    return {
        "case": case.name,
        "harness": harness,
        "model": model,
        "seed": seed,
        "completed": False,
        "passed": False,
        "scores": None,
        "reporting": {"parsed": False, "passed": None,
                      "matches_effects": None, "scores": None},
        "oracle": None,
        "tool_use": {},
        "tool_trace": [],
        "provenance": None,
        "telemetry": {
            "wall_clock_s": None, "cost_usd": None, "num_turns": None,
            "tokens": None, "omd_transport": omd_transport, "sandbox": sandbox,
            "sandbox_image": None,
        },
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }


def _record_key(record: dict) -> tuple:
    """The identity a record has for resume: one seed of one cell."""
    return (record["case"], record["harness"], record["model"], record["seed"])


def load_resume_records(records_path: Path, retry_errors: bool = True) -> list[dict]:
    """Read a prior run's ``.jsonl`` into the records ``run_matrix`` may reuse.

    Keeps the LAST record per (case, harness, model, seed) — a resumed file
    legitimately contains a superseded row before its retry. With
    ``retry_errors`` (the default), error rows are dropped so those seeds run
    again; without it they are kept as final results.
    """
    latest: dict[tuple, dict] = {}
    for line in Path(records_path).read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            latest[_record_key(record)] = record
    if retry_errors:
        latest = {k: r for k, r in latest.items() if not r.get("error")}
    return list(latest.values())


def _print_summary(record: dict) -> None:
    t = record["telemetry"]
    cell = f"{record['case']} · {record['harness']}/{record['model']} · seed {record['seed']}"
    if record.get("error"):
        err = record["error"]
        print(f"  {cell}")
        print(f"    result: ERROR ({err['type']}): {err['message'][:300]}")
        return
    verdict = "PASS" if record["passed"] else ("FAIL" if record["completed"] else "NO RUN")
    tu = record["tool_use"]
    rep = record.get("reporting") or {}
    tok = t.get("tokens") or {}
    tok_s = f" tokens={tok.get('input')}/{tok.get('output')}" if tok else ""
    omd_s = (f" omd={t['omd_transport']}"
             if t.get("omd_transport", "stdio") != "stdio" else "")
    if t.get("sandbox", "none") != "none":
        omd_s += f" sandbox={t['sandbox']}"
    if t.get("timed_out"):
        omd_s += " TIMED OUT"
    print(f"  {cell}")
    print(f"    result: {verdict} (effect-graded) | turns={t['num_turns']} "
          f"wall={t['wall_clock_s']:.1f}s cost={t['cost_usd']}{tok_s}{omd_s}")
    print(f"    report: parsed={rep.get('parsed')} passed={rep.get('passed')} "
          f"matches_effects={rep.get('matches_effects')}")
    print(f"    tools : {tu['total_calls']} calls, valid {tu['valid_call_rate']:.0%}, "
          f"schema-err {tu['schema_errors']}, hallucinated {tu['hallucinated_calls']}, "
          f"recovered {tu['recovered_errors']}")
    for s in record["scores"] or []:
        got = "null" if s["agent"] is None else f"{s['agent']:.6g}"
        print(f"    {s['key']:<14s} ref={s['lane_a']:.6g} got={got} -> {s['verdict']}")


def _print_cell_summary(s: CellSummary) -> None:
    print(f"  ══ cell summary: {s.case} · {s.harness}/{s.model} ({s.n_seeds} seeds) ══")
    print(f"     pass-rate {s.n_passed}/{s.n_seeds} ({s.pass_rate:.0%})  |  "
          f"ran-ok {s.n_completed}/{s.n_seeds} ({s.completion_rate:.0%})  |  "
          f"report-parsed {s.n_report_parsed}/{s.n_seeds}")
    if s.per_metric_pass:
        parts = ", ".join(f"{k} {v}/{s.n_seeds}" for k, v in s.per_metric_pass.items())
        print(f"     per-metric PASS: {parts}")
    if s.turns:
        print(f"     turns  min/med/max: {s.turns.min:g} / {s.turns.median:g} / {s.turns.max:g}")
    if s.wall_clock_s:
        w = s.wall_clock_s
        print(f"     wall_s min/med/max: {w.min:.1f} / {w.median:.1f} / {w.max:.1f}")
    if s.valid_call_rate:
        v = s.valid_call_rate
        print(f"     valid% min/med/max: {v.min:.0%} / {v.median:.0%} / {v.max:.0%}")
    if s.output_tokens:
        o = s.output_tokens
        print(f"     out-tok min/med/max: {o.min:g} / {o.median:g} / {o.max:g}")


def run_matrix(
    config: RunConfig,
    stamp: str,
    resume_records: list[dict] | None = None,
) -> list[CellSummary]:
    """Run the full matrix in ``config``; write records + manifest + summaries.

    One cell per (harness) — each run ``config.seeds`` times, then reduced to a
    ``CellSummary``. ``stamp`` is injected by the caller so output naming is
    deterministic and the function stays testable. Returns the cell summaries.

    Checkpointing (Step 18): every seed is flushed to the ``.jsonl`` as it
    finishes, and a harness crash becomes an ``_error_record`` row instead of
    aborting the matrix. ``resume_records`` (from ``load_resume_records``)
    turns the call into a resume: seeds already in it are reused, everything
    else — including retried error rows — runs and is APPENDED to the same
    records file, so the aggregation always reads the latest row per seed.
    """
    unknown = [h for h in config.harnesses if h not in HARNESSES]
    if unknown:
        raise ValueError(f"unknown harness(es): {unknown}. choose from {list(HARNESSES)}")
    if config.case not in CASES:
        raise ValueError(f"unknown case: {config.case}. choose from {list(CASES)}")

    case = CASES[config.case]
    results_dir = Path(config.results_dir).resolve()
    (results_dir / "run_data").mkdir(parents=True, exist_ok=True)

    base = f"{config.case}_{stamp}"
    records_path = results_dir / f"{base}.jsonl"
    manifest_path = results_dir / f"{base}_config.json"
    summary_path = results_dir / f"{base}_summary.json"

    resuming = resume_records is not None
    done = {_record_key(r): r for r in (resume_records or [])}

    # Manifest FIRST so the run is reproducible (via `--config <this file>`) even
    # if it crashes partway. Records the exact matrix, modulo the random seed —
    # plus the OBSERVED environment (git SHAs, tool versions; Step 12), which
    # reproduction compares rather than replays. On resume the original
    # manifest (and its as-first-run environment) is preserved.
    if not (resuming and manifest_path.exists()):
        manifest_path.write_text(json.dumps(
            {"stamp": stamp, "environment": capture_environment(),
             "config": config.to_dict()}, indent=2))

    summaries: list[CellSummary] = []
    with records_path.open("a" if resuming else "w") as fh:
        for harness in config.harnesses:
            factory, default_model = HARNESSES[harness]
            model = config.model or default_model
            # Sandboxed, each arm swaps to its containerized mechanism —
            # anchor: the in-container CLI driver (14a); opencode: the same
            # driver docker-wrapped with the local-arm image and NO env
            # passthrough (14b). Same AgentResult shape either way.
            if config.sandbox == "container":
                driver = (ClaudeCliDriver() if harness == "claude"
                          else OpenCodeDriver(sandbox=ContainerSandbox(
                              image=OPENCODE_IMAGE, env_passthrough=())))
            else:
                driver = factory()
            cell_records: list[dict] = []
            for seed in range(config.seeds):
                reused = done.get((case.name, harness, model, seed))
                if reused is not None:
                    print(f"  {case.name} · {harness}/{model} · seed {seed}: "
                          f"reused from prior run")
                    cell_records.append(reused)
                    continue
                try:
                    record = run_cell(case, driver, harness, model, seed,
                                      results_dir, config.max_turns,
                                      omd_transport=config.omd_transport,
                                      sandbox=config.sandbox,
                                      timeout_s=config.timeout_s)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    record = _error_record(case, harness, model, seed, exc,
                                           config.omd_transport, config.sandbox)
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                cell_records.append(record)
                _print_summary(record)
                print()
            summary = aggregate_cell(cell_records)
            summaries.append(summary)
            _print_cell_summary(summary)
            print()

    summary_path.write_text(json.dumps([s.to_dict() for s in summaries], indent=2))
    print(f"Wrote {records_path}\n      {manifest_path}\n      {summary_path}")
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path,
                        help="JSON run config (a manifest); overrides the flags below")
    parser.add_argument("--resume", type=Path, metavar="RECORDS_JSONL",
                        help="a prior run's records .jsonl: rerun only its missing "
                             "seeds (error rows are retried by default) and append "
                             "to the same files; config/stamp come from the "
                             "sibling _config.json manifest unless --config is given")
    parser.add_argument("--keep-errors", action="store_true",
                        help="with --resume: keep prior error rows as final "
                             "instead of retrying them")
    parser.add_argument("--case", default="paraboloid", choices=list(CASES))
    parser.add_argument("--harness", default="opencode",
                        help="comma-separated: " + ",".join(HARNESSES))
    parser.add_argument("--model", default=None, help="override the harness default model")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=None,
                        help="override every case's turn budget (default: per-case)")
    parser.add_argument("--timeout-s", type=float, default=None,
                        help="override every case's wall-clock budget in seconds "
                             "(default: per-case, 900 for most)")
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args(argv)

    resume_records = None
    if args.resume:
        records_path = args.resume.resolve()
        manifest_path = records_path.with_name(records_path.stem + "_config.json")
        config = RunConfig.from_json_file(args.config or manifest_path)
        stamp = json.loads(manifest_path.read_text())["stamp"]
        expected = (Path(config.results_dir).resolve()
                    / f"{config.case}_{stamp}.jsonl")
        if expected != records_path:
            parser.error(
                f"--resume {records_path} does not match its manifest's config "
                f"(which writes to {expected}); resume with the records file "
                "that belongs to the manifest")
        resume_records = load_resume_records(records_path,
                                             retry_errors=not args.keep_errors)
    elif args.config:
        config = RunConfig.from_json_file(args.config)
    else:
        harnesses = tuple(h.strip() for h in args.harness.split(",") if h.strip())
        config = RunConfig(
            case=args.case, harnesses=harnesses, model=args.model,
            seeds=args.seeds, max_turns=args.max_turns,
            timeout_s=args.timeout_s, results_dir=str(args.results_dir),
        )

    unknown = [h for h in config.harnesses if h not in HARNESSES]
    if unknown:
        parser.error(f"unknown harness(es): {unknown}. Choose from {list(HARNESSES)}")
    if config.case not in CASES:
        parser.error(f"unknown case: {config.case}. Choose from {list(CASES)}")

    if resume_records is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        print(f"Running {config.case} × {list(config.harnesses)} × {config.seeds} seed(s)\n")
    else:
        print(f"Resuming {config.case} × {list(config.harnesses)} × {config.seeds} "
              f"seed(s) — {len(resume_records)} seed(s) reused\n")
    run_matrix(config, stamp, resume_records=resume_records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
