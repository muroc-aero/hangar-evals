"""Turn graded seeds into error rows so the next run retries exactly them.

The honest resume (``results_index.case_status``) retries error rows -- seeds
the harness never measured -- and leaves graded rows alone. That is right
until a seed is graded but should not have been: a run that hit the cap
because a sandbox tool hung with the model idle is a harness loss the runner
recorded as a timed-out FAIL, since nothing at run time could tell it from a
slow model (three qwen seeds, 2026-09-22 arm). Re-running the whole cell
with ``--force`` would throw away the seeds that were measured properly.

``mark_lost`` appends, to the newest records file for the case, one error
row per named seed that supersedes the graded one (last row per seed wins,
for the runner, ``regrade`` and the tables alike). The original row stays in
the file, and the new row says what it replaced and why, so the marking is
as reviewable as a retry. The next plain ``scripts/evals run <arm>`` then
resumes those seeds and nothing else.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from hangar.evals.regrade import load_records

ERROR_TYPE = "HarnessLoss"


def mark_lost(
    case: str,
    seeds: list[int],
    reason: str,
    results_dir: Path,
    *,
    harness: str | None = None,
    model: str | None = None,
) -> dict:
    """Append superseding error rows for ``seeds`` of ``case``.

    Picks the newest ``<case>_<stamp>.jsonl`` that holds every named seed
    (for ``harness``/``model`` when given). Returns ``{"file", "marked":
    [(harness, model, seed), ...]}``. Raises ``ValueError`` when no file has
    them or when ``reason`` is empty -- a loss with no stated cause is not
    reviewable.
    """
    if not reason.strip():
        raise ValueError("a reason is required: say why the seed is a harness loss")
    if not seeds:
        raise ValueError("no seeds given")
    results_dir = Path(results_dir)
    wanted = set(seeds)

    for path in sorted(results_dir.glob(f"{case}_*.jsonl"), reverse=True):
        records = [r for r in load_records(path) if r.get("case") == case]
        if harness:
            records = [r for r in records if r["harness"] == harness]
        if model:
            records = [r for r in records if r["model"] == model]
        if not records:
            continue
        have = {r["seed"] for r in records}
        if not wanted <= have:
            continue
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        marked = []
        with path.open("a") as fh:
            for r in records:
                if r["seed"] not in wanted:
                    continue
                if r.get("error"):
                    continue  # already an error row; the resume retries it
                row = dict(r)
                row.update({
                    "completed": False,
                    "passed": False,
                    "scores": None,
                    "reporting": {"parsed": False, "passed": None,
                                  "matches_effects": None, "scores": None},
                    "oracle": None,
                    "error": {"type": ERROR_TYPE, "message": reason},
                    "marked_lost": {
                        "at": stamp,
                        "reason": reason,
                        "superseded": {
                            "passed": r.get("passed"),
                            "completed": r.get("completed"),
                            "timed_out": (r.get("telemetry") or {}).get("timed_out"),
                            "wall_clock_s": (r.get("telemetry") or {}).get("wall_clock_s"),
                        },
                    },
                })
                fh.write(json.dumps(row) + "\n")
                marked.append((r["harness"], r["model"], r["seed"]))
        return {"file": str(path), "marked": marked}

    raise ValueError(
        f"no records file for case {case!r} holds seeds {sorted(wanted)}"
        + (f" for {harness}/{model}" if harness or model else ""))
