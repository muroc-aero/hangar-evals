"""Effect-based oracle — grade what the agent actually RAN, not what it said.

The primary grader (Step 11). A run's ground truth lives in the omd provenance
DB (``analysis.db``) that every seed already captures under its ``data_root``:

  * ``run_cases`` rows with ``case_type='final'`` hold each run's output values
    (e.g. ``{"f_xy": 39.0, "x": 1.0, "y": 2.0}``) — the numbers the solver
    actually produced, independent of how (or whether) the agent reported them.
  * ``activities`` rows ``act-execute-<run_id>`` give per-run execute status
    and a ``started_at`` ordering.
  * the ``assessment-<run_id>`` entity's metadata JSON carries the run
    ``mode`` (``"analysis"`` / ``"optimize"``) — the discriminator pinned by
    the Step 11 Task-1 recon. It is written by omd itself
    (``run.py:_record_assessment``), so it is robust to agents naming plans
    arbitrarily.

Grading policy (spec §4c): per metric, select the agent's **last successful**
run of the matching mode — the final answer-by-action. Deliberately NOT
best-of-all-runs (spray-and-pray must not pay), and no successful run of the
required mode means the metric value is ``None`` → a required metric FAILs.
That makes "pass by doing nothing" (and pass-by-forged-report) structurally
impossible: with no successful execute there is nothing to grade.

The fenced-JSON self-report is scored separately as *reporting fidelity*
(``report_matches_effects``): did the agent report the numbers its own runs
produced? Honest self-reporting is a deployment-relevant trait, but it is a
SECONDARY signal — correctness of the work comes from here.

Scope note: only ``analysis``/``optimize`` runs are mapped today (all current
cases). Polar/study runs record differently; extend ``MODE_BY_MODULE`` and the
selection when a T1+ case needs them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hangar.evals.scoring import Metric

# Metric.lane_a_module -> the omd run "mode" recorded in assessment metadata.
MODE_BY_MODULE = {"analysis": "analysis", "optimization": "optimize"}


@dataclass(frozen=True)
class EffectRun:
    """One omd run reconstructed from the provenance DB."""

    run_id: str
    mode: str | None            # assessment metadata "mode"; None if no assessment
    executed_ok: bool           # act-execute-<run_id> completed
    assess_status: str | None   # e.g. "completed", "converged"
    started_at: str             # execute activity start (ISO string; sortable)
    final_values: dict          # last run_cases 'final' row for this run


def read_effect_runs(db_path: Path) -> list[EffectRun]:
    """Reconstruct every run in ``db_path``, ordered by execute start time.

    Read-only. A missing file raises; a present-but-empty/foreign DB (the
    agent never ran anything, or omd never created the tables) yields ``[]``
    rather than an error — that is the legitimate "did nothing" outcome the
    grading policy must be able to score.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"provenance DB not found: {db_path}")

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        try:
            acts = conn.execute(
                "SELECT activity_id, status, started_at FROM activities "
                "WHERE activity_id LIKE 'act-execute-%' "
                "ORDER BY started_at, activity_id"
            ).fetchall()
            assessments = {
                row["entity_id"]: row["metadata"]
                for row in conn.execute(
                    "SELECT entity_id, metadata FROM entities "
                    "WHERE entity_type='assessment'"
                )
            }
            runs: list[EffectRun] = []
            for a in acts:
                run_id = a["activity_id"][len("act-execute-"):]
                meta = _parse_json(assessments.get(f"assessment-{run_id}"))
                final = conn.execute(
                    "SELECT data FROM run_cases "
                    "WHERE run_id=? AND case_type='final' "
                    "ORDER BY case_id DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
                runs.append(EffectRun(
                    run_id=run_id,
                    mode=meta.get("mode"),
                    executed_ok=(a["status"] == "completed"),
                    assess_status=meta.get("status"),
                    started_at=a["started_at"],
                    final_values=_parse_json(final["data"]) if final else {},
                ))
            return runs
        except sqlite3.OperationalError:
            return []   # no such table: fresh/foreign DB == nothing was run
    finally:
        conn.close()


def _parse_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def select_run(runs: list[EffectRun], mode: str) -> EffectRun | None:
    """The LAST successful run of ``mode`` — the agent's final answer-by-action."""
    candidates = [r for r in runs if r.executed_ok and r.mode == mode]
    return candidates[-1] if candidates else None


def effect_values(
    metrics: list[Metric], runs: list[EffectRun]
) -> dict[str, float | None]:
    """Per-metric values from the selected runs (``None`` = nothing to grade).

    The value key in ``run_cases`` final data is ``Metric.effect_key``,
    falling back to ``lane_a_key`` (they coincide for every current case).
    Non-numeric values map to ``None`` so the comparator FAILs them.
    """
    out: dict[str, float | None] = {}
    for m in metrics:
        run = select_run(runs, MODE_BY_MODULE[m.lane_a_module])
        got = run.final_values.get(m.effect_key or m.lane_a_key) if run else None
        numeric = isinstance(got, (int, float)) and not isinstance(got, bool)
        out[m.key] = float(got) if numeric else None
    return out


def oracle_ambiguity(metrics: list[Metric], runs: list[EffectRun]) -> int:
    """How many successful mode-matching runs the selection SKIPPED.

    Nonzero means the agent produced several graded-mode runs and we took the
    last per policy — logged into the record (spec §4c risk 1), never silently
    resolved.
    """
    modes = {MODE_BY_MODULE[m.lane_a_module] for m in metrics}
    skipped = 0
    for mode in modes:
        n = sum(1 for r in runs if r.executed_ok and r.mode == mode)
        skipped += max(0, n - 1)
    return skipped


def report_matches_effects(
    metrics: list[Metric],
    report: dict,
    effects: dict[str, float | None],
) -> bool | None:
    """Did the agent report the numbers its OWN runs produced?

    True iff every metric with a gradable effect value has a reported number
    within that metric's rtol of it. ``None`` when there are no gradable
    effect values (nothing to be faithful to).
    """
    reported = report.get("metrics", {}) or {}
    gradable = [m for m in metrics if effects.get(m.key) is not None]
    if not gradable:
        return None
    for m in gradable:
        got = reported.get(m.key)
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            return False
        ref = effects[m.key]
        if abs(got - ref) / max(abs(ref), 1e-30) > m.rtol:
            return False
    return True
