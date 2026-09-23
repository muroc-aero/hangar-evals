"""Turn graded seeds into error rows so the next run retries exactly them --
and turn them back when the marking was wrong.

The honest resume (``results_index.case_status``) retries error rows -- seeds
the harness never measured -- and leaves graded rows alone. That is right
until a seed is graded but should not have been: a run that hit the cap
because the harness itself broke (a sandbox tool hung, a server crashed) is
a harness loss the runner recorded as a timed-out FAIL, since nothing at run
time could tell it from a slow model. Re-running the whole cell with
``--force`` would throw away the seeds that were measured properly.

``mark_lost`` appends, to the newest records file for the case, one error
row per named seed that supersedes the graded one (last row per seed wins,
for the runner, ``regrade`` and the tables alike). The original row stays in
the file, and the new row says what it replaced and why, so the marking is
as reviewable as a retry. The next plain ``scripts/evals run <arm>`` then
resumes those seeds and nothing else.

``restore_marked`` is the undo, for a mark made on a wrong diagnosis (the
three qwen seeds of 2026-09-22 were marked as Ollama hangs; the "hang" was
the model spending 32k output tokens on one step). It appends a copy of the
row the mark superseded, so the original grade is the last row again and
any rows a resume added in between are superseded in turn -- the file
keeps every step of the story.
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
    path, records = _newest_file_with(case, seeds, results_dir, harness, model)
    wanted = set(seeds)
    stamp = _now()
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


def restore_marked(
    case: str,
    seeds: list[int],
    reason: str,
    results_dir: Path,
    *,
    harness: str | None = None,
    model: str | None = None,
) -> dict:
    """Undo ``mark_lost``: make the row each mark superseded the last row again.

    For every named seed whose file holds a ``marked_lost`` row, appends a
    copy of the last row before that mark, tagged ``restored_from_lost``
    with the reason and the rows it supersedes (the mark, and any rows a
    resume appended after it -- a re-run of a seed that was never a harness
    loss is a re-roll and does not count). Seeds with no mark are left
    alone and reported under ``"unmarked"``.
    """
    if not reason.strip():
        raise ValueError("a reason is required: say why the mark was wrong")
    path, _ = _newest_file_with(case, seeds, results_dir, harness, model)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    stamp = _now()
    restored, unmarked = [], []
    with path.open("a") as fh:
        for seed in seeds:
            key = lambda r: (r.get("case") == case and r["seed"] == seed  # noqa: E731
                             and (not harness or r["harness"] == harness)
                             and (not model or r["model"] == model))
            mine = [(i, r) for i, r in enumerate(rows) if key(r)]
            marks = [i for i, r in mine if r.get("marked_lost")]
            if not marks:
                unmarked.append(seed)
                continue
            first_mark = marks[0]
            before = [r for i, r in mine if i < first_mark and not r.get("marked_lost")]
            if not before:
                raise ValueError(f"seed {seed}: nothing precedes its mark in {path.name}")
            original = before[-1]
            superseded = [
                {"row": i, "kind": "marked_lost" if r.get("marked_lost") else "rerun",
                 "passed": r.get("passed"), "error": (r.get("error") or {}).get("type")}
                for i, r in mine if i >= first_mark
            ]
            row = dict(original)
            row["restored_from_lost"] = {
                "at": stamp, "reason": reason, "supersedes": superseded}
            fh.write(json.dumps(row) + "\n")
            restored.append((original["harness"], original["model"], seed))
    return {"file": str(path), "restored": restored, "unmarked": unmarked}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _newest_file_with(case, seeds, results_dir, harness, model):
    """Newest ``<case>_<stamp>.jsonl`` holding every seed (last row per seed)."""
    if not seeds:
        raise ValueError("no seeds given")
    wanted = set(seeds)
    for path in sorted(Path(results_dir).glob(f"{case}_*.jsonl"), reverse=True):
        records = [r for r in load_records(path) if r.get("case") == case]
        if harness:
            records = [r for r in records if r["harness"] == harness]
        if model:
            records = [r for r in records if r["model"] == model]
        if records and wanted <= {r["seed"] for r in records}:
            return path, records
    raise ValueError(
        f"no records file for case {case!r} holds seeds {sorted(wanted)}"
        + (f" for {harness}/{model}" if harness or model else ""))
