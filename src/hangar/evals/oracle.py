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

Grading policy (spec §4c, revised 2026-09-12): per metric, grade the run the
agent **named** in its report's ``run_id``, provided that run really executed
and matches the metric's mode. Failing that, fall back to its last successful
run of the matching mode.

Selecting by name, not by position, is what the earlier policy got wrong. The
2026-09-11 anchor arm produced four seeds where the agent reported the correct
answer and then kept working — a 500 NM sensitivity sweep on a 250 NM task, a
surrogate wing model after the live one, a looser re-solve — and "last run"
graded the exploration instead of the answer. Each of those seeds scored FAIL
on work that was right.

The anti-spray-and-pray property survives intact, because naming a run is not
choosing an answer: the numbers still come from the provenance DB, never from
the report's own figures, so a run the agent names but never made, or made and
failed, grades nothing. What the agent gains is the ability every engineer has
— to say which run is the result. What it cannot do is claim a number its runs
did not produce. Cherry-picking stays visible: ``n_executed_ok`` and
``oracle_ambiguity`` record how many runs it took to get there.

No successful run of the required mode means the metric value is ``None`` → a
required metric FAILs. That makes "pass by doing nothing" (and
pass-by-forged-report) structurally impossible: with no successful execute
there is nothing to grade.

The fenced-JSON self-report is scored separately as *reporting fidelity*
(``report_matches_effects``): did the agent report the numbers its own runs
produced? Honest self-reporting is a deployment-relevant trait, but it is a
SECONDARY signal — correctness of the work comes from here.

Evidence layers (Step 15): raw ``run_cases`` final data holds OpenMDAO
variable names (``paraboloid.f_xy``, promoted ``x``/``y``), but most suite
metrics are SUMMARY-level quantities omd computes at run time
(``fuel_burn_kg`` integrates phases; ``CL`` reads a solver point) that never
appear as recorder variables. Those scalars are snapshotted into the
assessment metadata (the-hangar widened the snapshot to every scalar summary
key for exactly this), so the oracle reads both layers: assessment scalars —
plus per-component summaries flattened to ``<comp_id>.<key>`` — overlaying
the raw final data. Composite metrics resolve by unique dotted-suffix match
because the agent chooses component ids.

Scope note: only ``analysis``/``optimize`` runs are mapped today (all current
cases). Polar/study runs record differently; extend ``MODE_BY_MODULE`` and the
selection when a T2+ case needs them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from hangar.evals.scoring import Metric

# Metric.lane_a_module -> the omd run "mode" recorded in assessment metadata.
# Every T1/T4 module is an analysis run; only the paraboloid optimization
# tasks the optimizer. Explicit (not defaulted) so an unmapped module is a
# loud KeyError at grading time, not a silently wrong grade.
MODE_BY_MODULE = {
    "analysis": "analysis",
    "optimization": "optimize",
    "aero_analysis": "analysis",
    "aerostruct_analysis": "analysis",
    "basic_mission": "analysis",
    "full_mission": "analysis",
    "hybrid_mission": "analysis",
    "wing_mission": "analysis",
    "coupled_mission": "analysis",
    "direct_coupled_mission": "analysis",
    "design_analysis": "analysis",
    "sizing": "analysis",
}

# Assessment metadata keys that are run bookkeeping, not summary metrics.
_ASSESS_BOOKKEEPING = {"status", "mode", "case_count"}


@dataclass(frozen=True)
class EffectRun:
    """One omd run reconstructed from the provenance DB."""

    run_id: str
    mode: str | None            # assessment metadata "mode"; None if no assessment
    executed_ok: bool           # act-execute-<run_id> completed
    assess_status: str | None   # e.g. "completed", "converged"
    started_at: str             # execute activity start (ISO string; sortable)
    final_values: dict          # last run_cases 'final' row for this run
    # Summary scalars snapshotted into the assessment metadata, with composite
    # per-component summaries flattened to "<comp_id>.<key>" (Step 15).
    assess_values: dict = field(default_factory=dict)


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
                    assess_values=_assess_values(meta),
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


def _is_scalar(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _assess_values(meta: dict) -> dict:
    """Summary metrics from assessment metadata: top-level scalars plus
    per-component summaries flattened to ``<comp_id>.<key>``.

    Slots are deliberately NOT flattened — no current metric reads them, and
    a slot provider's internal ``CL`` would collide with a component's in the
    suffix search.
    """
    values = {
        k: v for k, v in meta.items()
        if _is_scalar(v) and k not in _ASSESS_BOOKKEEPING
    }
    components = meta.get("components")
    if isinstance(components, dict):
        for comp_id, comp in components.items():
            if not isinstance(comp, dict):
                continue
            for k, v in comp.items():
                if _is_scalar(v):
                    values[f"{comp_id}.{k}"] = v
    return values


# How a metric's run was chosen — recorded per seed so a fallback is visible.
NAMED = "named"                  # the agent's reported run_id, and it qualified
FALLBACK_LAST = "fallback_last"  # no usable run_id; took the last successful run
NOTHING = "nothing"              # no successful run of the mode at all


def select_run(
    runs: list[EffectRun], mode: str, reported_run_id: str | None = None
) -> EffectRun | None:
    """The run the agent named, else its LAST successful run of ``mode``.

    A reported id only counts if it names a run that actually executed OK in
    the required mode: an id for a failed run, a run of the wrong mode, or a
    run that never happened falls through to the positional rule rather than
    grading nothing.
    """
    candidates = [r for r in runs if r.executed_ok and r.mode == mode]
    if not candidates:
        return None
    if reported_run_id:
        for r in candidates:
            if r.run_id == reported_run_id:
                return r
    return candidates[-1]


def selection_basis(
    metrics: list[Metric], runs: list[EffectRun], reported_run_id: str | None = None
) -> str:
    """How the graded runs were chosen: ``NAMED``, ``FALLBACK_LAST``, ``NOTHING``.

    ``FALLBACK_LAST`` is the one worth watching. It means the agent finished
    without naming a run we could grade, so the policy guessed — which is
    exactly the situation that cost the 2026-09-11 arm four seeds.
    """
    modes = {MODE_BY_MODULE[m.lane_a_module] for m in metrics}
    chosen = [select_run(runs, mode, reported_run_id) for mode in sorted(modes)]
    if not any(chosen):
        return NOTHING
    named = reported_run_id and any(
        r is not None and r.run_id == reported_run_id for r in chosen)
    return NAMED if named else FALLBACK_LAST


def effect_values(
    metrics: list[Metric],
    runs: list[EffectRun],
    reported_run_id: str | None = None,
) -> dict[str, float | None]:
    """Per-metric values from the selected runs (``None`` = nothing to grade).

    The value key is ``Metric.effect_key``, falling back to ``lane_a_key``.
    Lookup order per metric (Step 15): exact key in the assessment summary
    scalars (the vocabulary Lane A shares), exact key in the raw recorder
    final data (paraboloid's promoted ``x``/``y``/``f_xy``), then a unique
    dotted-suffix match among the flattened ``<comp_id>.<key>`` component
    values — composites only, where the agent named the components. An
    ambiguous suffix (two components exposing the same key) grades ``None``
    rather than guessing. Non-numeric values map to ``None`` so the
    comparator FAILs them.
    """
    out: dict[str, float | None] = {}
    for m in metrics:
        run = select_run(runs, MODE_BY_MODULE[m.lane_a_module], reported_run_id)
        got = _lookup(run, m.effect_key or m.lane_a_key) if run else None
        out[m.key] = float(got) if _is_scalar(got) else None
    return out


def _lookup(run: EffectRun, key: str):
    if key in run.assess_values:
        return run.assess_values[key]
    if key in run.final_values:
        return run.final_values[key]
    suffix = [
        v for k, v in run.assess_values.items()
        if "." in k and k.rsplit(".", 1)[1] == key
    ]
    return suffix[0] if len(suffix) == 1 else None


def oracle_ambiguity(metrics: list[Metric], runs: list[EffectRun]) -> int:
    """How many successful mode-matching runs the selection did NOT grade.

    This is an iteration count, not a defect: an agent that runs, inspects,
    adjusts and re-runs will show several, and the one it names is the answer.
    It stays in the record (spec §4c risk 1) so cherry-picking is visible —
    read it alongside ``selection_basis``, which says whether the agent named
    its answer or the policy had to guess.
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
