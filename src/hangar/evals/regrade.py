"""Re-derive cell summaries from stored records — no agent re-runs.

Why this exists: a summary is a pure function of its records
(``aggregate_cell``), so any summary field added later can be back-filled over
runs that already happened, including arms whose model is retired. This module
does that, and adds the two counts a pass-rate alone hides.

``n_harness_errors`` -- seeds the harness lost: it crashed, or its credential
or network went, and the agent produced no successful run to grade. These are
NOT results. They are the only failures that earn a re-run, and a table with a
nonzero count here is not finished -- fix the harness and run the case again.

``n_needs_review`` -- seeds where the agent's own fenced-JSON verdict differs
from the effect grade. Nothing here can be resolved automatically: the same
signature covers an agent that misreported its numbers and a grading policy
that scored the wrong one of the agent's runs. So it is routed to a person,
with the artifacts, via ``evals review``.

Neither count changes a verdict.

Not counted any more: oracle ambiguity (how many successful same-mode runs the
"grade the last one" policy skipped). It measured a defect in the HARNESS --
prompts that ask for a comparison run, against a policy that grades whichever
run happens to be last -- and a defect belongs in a fix, not in a permanent
column of the results table. ``harness_health`` still reports it so it can be
fixed; see ``oracle.oracle_ambiguity``.

Usage:
    python -m hangar.evals.regrade                       # -> results/regraded/
    python -m hangar.evals.regrade --results-dir results/old --out-dir /tmp/x
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hangar.evals.aggregate import aggregate_cell


def _key(record: dict) -> tuple:
    return (record["case"], record["harness"], record["model"])


def load_records(records_path: Path) -> list[dict]:
    """Records from one run's ``.jsonl``, last row per seed winning.

    Mirrors ``run.load_resume_records``' de-duplication: a resumed file holds a
    superseded row before its retry, and only the retry counts.
    """
    latest: dict[tuple, dict] = {}
    for line in records_path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            latest[(r["case"], r["harness"], r["model"], r["seed"])] = r
    return list(latest.values())


def extra_counts(records: list[dict]) -> dict[str, int]:
    """The counts ``aggregate_cell`` does not carry (see module docstring)."""
    harness_errors = sum(1 for r in records if r.get("error"))
    needs_review = sum(
        1 for r in records
        if (r.get("reporting") or {}).get("parsed")
        and bool((r.get("reporting") or {}).get("passed")) != bool(r.get("passed")))
    return {"n_harness_errors": harness_errors, "n_needs_review": needs_review}


def harness_health(records: list[dict]) -> dict[str, int]:
    """Defects in the MEASUREMENT, for fixing — never for the results table.

    ``n_ambiguous`` counts seeds whose score depended on which of the agent's
    same-mode runs happened to execute last. ``n_degraded`` counts seeds that
    graded but whose harness exited abnormally on the way. Both mean the
    apparatus is imperfect, not that the agent is: a run with either is a
    candidate for a re-run once the cause is fixed, and the goal is a forced
    re-run that reports zero of both.
    """
    return {
        "n_ambiguous": sum(
            1 for r in records
            if ((r.get("oracle") or {}).get("ambiguity") or 0) > 0),
        "n_degraded": sum(
            1 for r in records
            if (r.get("telemetry") or {}).get("exit_code")),
    }


def regrade_file(records_path: Path) -> list[dict]:
    """Summary dicts for one run's records, one per (case, harness, model)."""
    cells: dict[tuple, list[dict]] = {}
    for r in load_records(records_path):
        cells.setdefault(_key(r), []).append(r)
    out = []
    for _, recs in sorted(cells.items()):
        recs.sort(key=lambda r: r["seed"])
        summary = aggregate_cell(recs).to_dict()
        summary.update(extra_counts(recs))
        summary["harness_health"] = harness_health(recs)
        out.append(summary)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="default: <results-dir>/regraded")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the closing count, for callers that "
                             "render their own table")
    args = parser.parse_args(argv)
    out_dir = args.out_dir or (args.results_dir / "regraded")
    out_dir.mkdir(parents=True, exist_ok=True)

    n_files = n_cells = 0
    for records_path in sorted(args.results_dir.glob("*.jsonl")):
        summaries = regrade_file(records_path)
        if not summaries:
            continue
        target = out_dir / (records_path.stem + "_summary.json")
        target.write_text(json.dumps(summaries, indent=2))
        n_files += 1
        n_cells += len(summaries)
        if args.quiet:
            continue
        for s in summaries:
            flags = []
            if s["n_harness_errors"]:
                flags.append(f"{s['n_harness_errors']} harness error")
            if s["n_needs_review"]:
                flags.append(f"{s['n_needs_review']} to review")
            print(f"  {s['case']:24s} {s.get('model') or '-':18s} "
                  f"{s['n_passed']}/{s['n_seeds']} passed"
                  + (f"  [{', '.join(flags)}]" if flags else ""))
    print(f"\n{n_cells} cell summaries from {n_files} record files -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
