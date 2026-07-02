"""Aggregate N per-seed cell records into one CellSummary.

A single local-model run is noise — two identical paraboloid cells gave 1 turn /
0 calls vs 13 turns / 46 calls. So each cell is run N times (Step 9) and reported
as a *distribution*: pass-rate (k/N), completion-rate, per-metric PASS counts,
and the min/median/max spread of turns / wall-clock / valid-call-rate.

``aggregate_cell`` is pure over the JSON records ``run_cell`` already emits — no
the-hangar dependency, no driver — so it is trivially testable and reusable by
the (later) report/leaderboard layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median


@dataclass(frozen=True)
class Stat:
    """min / median / max of one numeric metric across a cell's seeds."""

    min: float
    median: float
    max: float

    @classmethod
    def of(cls, values) -> "Stat | None":
        """Build from an iterable, skipping ``None``; ``None`` if nothing left."""
        nums = [v for v in values if v is not None]
        if not nums:
            return None
        return cls(min=min(nums), median=float(median(nums)), max=max(nums))


@dataclass(frozen=True)
class CellSummary:
    """One (case, harness, model) cell summarized across its seeds."""

    case: str
    harness: str
    model: str | None
    n_seeds: int
    seeds: list[int]
    n_completed: int                 # >=1 successful execute (Step 11 semantics)
    n_passed: int                    # all required metrics PASS (effect-graded)
    n_report_parsed: int             # emitted a parseable fenced-JSON report
    completion_rate: float
    pass_rate: float
    per_metric_pass: dict[str, int]  # metric key -> effect-PASS count across seeds
    turns: Stat | None
    wall_clock_s: Stat | None
    valid_call_rate: Stat | None

    def to_dict(self) -> dict:
        return asdict(self)  # nested Stat -> dict, None stays None


def aggregate_cell(records: list[dict]) -> CellSummary:
    """Summarize the per-seed records of ONE cell.

    All records must share (case, harness, model) — mixing cells is a bug, so it
    raises rather than silently averaging across them. Numeric spreads come from
    every seed (turns/wall exist even on a NO-REPORT run); per-metric PASS counts
    come from seeds that produced scores.
    """
    if not records:
        raise ValueError("aggregate_cell: no records")
    cases = {r["case"] for r in records}
    harnesses = {r["harness"] for r in records}
    models = {r["model"] for r in records}
    if not (len(cases) == len(harnesses) == len(models) == 1):
        raise ValueError(
            "aggregate_cell: records span multiple cells "
            f"(cases={cases}, harnesses={harnesses}, models={models})"
        )

    n = len(records)
    n_completed = sum(bool(r.get("completed")) for r in records)
    n_passed = sum(bool(r.get("passed")) for r in records)
    n_report_parsed = sum(
        bool((r.get("reporting") or {}).get("parsed")) for r in records
    )

    per_metric_pass: dict[str, int] = {}
    for r in records:
        for s in r.get("scores") or []:
            if s.get("verdict") == "PASS":
                per_metric_pass[s["key"]] = per_metric_pass.get(s["key"], 0) + 1

    turns = Stat.of(r["telemetry"].get("num_turns") for r in records)
    wall = Stat.of(r["telemetry"].get("wall_clock_s") for r in records)
    valid = Stat.of(r["tool_use"].get("valid_call_rate") for r in records)

    return CellSummary(
        case=records[0]["case"],
        harness=records[0]["harness"],
        model=records[0]["model"],
        n_seeds=n,
        seeds=[r.get("seed") for r in records],
        n_completed=n_completed,
        n_passed=n_passed,
        n_report_parsed=n_report_parsed,
        completion_rate=n_completed / n,
        pass_rate=n_passed / n,
        per_metric_pass=per_metric_pass,
        turns=turns,
        wall_clock_s=wall,
        valid_call_rate=valid,
    )
