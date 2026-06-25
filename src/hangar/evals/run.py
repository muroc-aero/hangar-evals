"""Runner — the model × harness × task matrix, multi-seed, end to end.

A *cell* is (case, harness, model); ``run_cell`` runs ONE seed of it — builds the
prompt, drives the agent, scores it three ways (numeric vs Lane A, tool-use
trace, provenance DB) — and returns one JSON record. Because a single local-model
run is noise (Step 9), each cell is run N seeds and ``aggregate_cell`` reduces
them to a pass-rate ``CellSummary``.

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
records), ``<case>_<stamp>_config.json`` (the manifest — re-run via ``--config``),
and ``<case>_<stamp>_summary.json`` (the per-cell summaries). The random seed is
NOT yet reproducible, but the *matrix* is. Scoring is held constant; only the
driver/model vary — that's the whole point.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hangar.evals.aggregate import CellSummary, aggregate_cell
from hangar.evals.cases import CASES, Case, build_prompt
from hangar.evals.drivers.base import MCPServerSpec
from hangar.evals.drivers.claude_sdk import ClaudeAgentSDKDriver
from hangar.evals.drivers.opencode import OpenCodeDriver
from hangar.evals.scoring import compute_refs, extract_report, score_report
from hangar.evals.trace import parse_tool_trace, read_provenance

# harness name -> (driver factory, default model). Claude's default model is the
# SDK/CLI default (None); OpenCode floors to the pulled smoke model.
HARNESSES = {
    "claude": (ClaudeAgentSDKDriver, None),
    "opencode": (OpenCodeDriver, "qwen3:8b"),
}

_CONFIG_KEYS = ("case", "harnesses", "model", "seeds", "max_turns", "results_dir")


@dataclass(frozen=True)
class RunConfig:
    """A full, serializable description of one eval run — the scriptable unit.

    ``model`` overrides every harness's default when set. Round-trips to/from a
    JSON config file so a run can be reproduced by ``--config <manifest>`` or
    rebuilt in a Python script (modulo the not-yet-reproducible random seed).
    """

    case: str = "paraboloid"
    harnesses: tuple[str, ...] = ("opencode",)
    model: str | None = None
    seeds: int = 3
    max_turns: int = 80
    results_dir: str = "results"

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in _CONFIG_KEYS}
        d["harnesses"] = list(self.harnesses)  # tuple -> JSON array
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RunConfig":
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
    max_turns: int = 80,
) -> dict:
    """Run one cell and return its result record."""
    data_root = Path(tempfile.mkdtemp(
        prefix=f"{case.name}_{harness}_s{seed}_", dir=str(results_dir / "run_data")
    )).resolve()
    mcp = MCPServerSpec.omd(data_root)

    result = driver.run(
        build_prompt(case), mcp, data_root, model=model, max_turns=max_turns
    )

    # Numeric correctness (None when the agent emitted no parseable report).
    refs = compute_refs(case.example, case.metrics)
    try:
        report = extract_report(result.final_text)
    except ValueError:
        report = None
    score = score_report(case.metrics, report, refs) if report is not None else None

    # Tool-use (harness trace) + workflow adherence (provenance DB).
    trace = result.tool_call_trace or []
    tool_metrics = parse_tool_trace(trace)
    db = data_root / "analysis.db"
    prov = read_provenance(db) if db.exists() else None

    return {
        "case": case.name,
        "harness": harness,
        "model": model,
        "seed": seed,
        "completed": report is not None,
        "passed": (score.passed if score else False),
        "scores": _scores_to_dicts(score),
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
        },
        "data_root": str(data_root),
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


def _print_summary(record: dict) -> None:
    t = record["telemetry"]
    cell = f"{record['case']} · {record['harness']}/{record['model']} · seed {record['seed']}"
    verdict = "PASS" if record["passed"] else ("completed" if record["completed"] else "NO REPORT")
    tu = record["tool_use"]
    print(f"  {cell}")
    print(f"    result: {verdict}  | turns={t['num_turns']} "
          f"wall={t['wall_clock_s']:.1f}s cost={t['cost_usd']}")
    print(f"    tools : {tu['total_calls']} calls, valid {tu['valid_call_rate']:.0%}, "
          f"schema-err {tu['schema_errors']}, hallucinated {tu['hallucinated_calls']}, "
          f"recovered {tu['recovered_errors']}")
    for s in record["scores"] or []:
        got = "null" if s["agent"] is None else f"{s['agent']:.6g}"
        print(f"    {s['key']:<14s} ref={s['lane_a']:.6g} got={got} -> {s['verdict']}")


def _print_cell_summary(s: CellSummary) -> None:
    print(f"  ══ cell summary: {s.case} · {s.harness}/{s.model} ({s.n_seeds} seeds) ══")
    print(f"     pass-rate {s.n_passed}/{s.n_seeds} ({s.pass_rate:.0%})  |  "
          f"completed {s.n_completed}/{s.n_seeds} ({s.completion_rate:.0%})")
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


def run_matrix(config: RunConfig, stamp: str) -> list[CellSummary]:
    """Run the full matrix in ``config``; write records + manifest + summaries.

    One cell per (harness) — each run ``config.seeds`` times, then reduced to a
    ``CellSummary``. ``stamp`` is injected by the caller so output naming is
    deterministic and the function stays testable. Returns the cell summaries.
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

    # Manifest FIRST so the run is reproducible (via `--config <this file>`) even
    # if it crashes partway. Records the exact matrix, modulo the random seed.
    manifest_path.write_text(json.dumps(
        {"stamp": stamp, "config": config.to_dict()}, indent=2))

    summaries: list[CellSummary] = []
    with records_path.open("w") as fh:
        for harness in config.harnesses:
            factory, default_model = HARNESSES[harness]
            model = config.model or default_model
            driver = factory()
            cell_records: list[dict] = []
            for seed in range(config.seeds):
                record = run_cell(case, driver, harness, model, seed,
                                  results_dir, config.max_turns)
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
    parser.add_argument("--case", default="paraboloid", choices=list(CASES))
    parser.add_argument("--harness", default="opencode",
                        help="comma-separated: " + ",".join(HARNESSES))
    parser.add_argument("--model", default=None, help="override the harness default model")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args(argv)

    if args.config:
        config = RunConfig.from_json_file(args.config)
    else:
        harnesses = tuple(h.strip() for h in args.harness.split(",") if h.strip())
        config = RunConfig(
            case=args.case, harnesses=harnesses, model=args.model,
            seeds=args.seeds, max_turns=args.max_turns,
            results_dir=str(args.results_dir),
        )

    unknown = [h for h in config.harnesses if h not in HARNESSES]
    if unknown:
        parser.error(f"unknown harness(es): {unknown}. Choose from {list(HARNESSES)}")
    if config.case not in CASES:
        parser.error(f"unknown case: {config.case}. Choose from {list(CASES)}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"Running {config.case} × {list(config.harnesses)} × {config.seeds} seed(s)\n")
    run_matrix(config, stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
