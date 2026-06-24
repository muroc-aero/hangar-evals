"""Runner — one cell of the model × harness × task matrix, end to end.

A *cell* is (case, harness, model, seed). ``run_cell`` builds the prompt, drives
the agent, then scores it three ways — numeric correctness (vs Lane A), tool-use
(harness trace), and workflow adherence (provenance DB) — and returns one
JSON-serializable record. The CLI runs a small matrix and appends records to a
gitignored ``results/*.jsonl``.

    python -m hangar.evals.run --case paraboloid --harness opencode --seeds 1
    python -m hangar.evals.run --case paraboloid --harness claude,opencode

Scoring is held constant; only the driver/model vary — that's the whole point.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

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
        "validated_before_execute": p.validated_before_execute,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", default="paraboloid", choices=list(CASES))
    parser.add_argument("--harness", default="opencode",
                        help="comma-separated: " + ",".join(HARNESSES))
    parser.add_argument("--model", default=None, help="override the harness default model")
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--results-dir", default="results", type=Path)
    args = parser.parse_args(argv)

    case = CASES[args.case]
    harnesses = [h.strip() for h in args.harness.split(",") if h.strip()]
    unknown = [h for h in harnesses if h not in HARNESSES]
    if unknown:
        parser.error(f"unknown harness(es): {unknown}. Choose from {list(HARNESSES)}")

    results_dir = args.results_dir.resolve()
    (results_dir / "run_data").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = results_dir / f"{args.case}_{stamp}.jsonl"

    print(f"Running {args.case} × {harnesses} × {args.seeds} seed(s) -> {out_path}\n")
    with out_path.open("w") as fh:
        for harness in harnesses:
            factory, default_model = HARNESSES[harness]
            model = args.model or default_model
            driver = factory()
            for seed in range(args.seeds):
                record = run_cell(case, driver, harness, model, seed,
                                  results_dir, args.max_turns)
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                _print_summary(record)
                print()

    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
