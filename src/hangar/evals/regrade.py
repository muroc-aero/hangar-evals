"""Re-derive cell summaries from stored records — no agent re-runs.

Why this exists: a summary is a pure function of its records
(``aggregate_cell``), so any summary field added later can be back-filled over
runs that already happened, including arms whose model is retired. This module
does that, and adds the two counts a pass-rate alone hides.

``n_ambiguous`` -- seeds where the oracle skipped a successful mode-matching
run (``oracle.ambiguity > 0``). The grading policy is "the LAST successful run
of the matching mode" (oracle.select_run) -- deliberate, so spray-and-pray
cannot pay. But several Lane C prompts ASK for a comparison run ("judge
whether the fuel burn sits slightly above what the same profile would burn
without the takeoff roll"), and an agent that obeys can leave a control run
last. The score then depends on run ORDER rather than on the work. That is not
a fail in the sense a reader assumes, so it is counted, not buried.

``n_report_disagrees`` -- seeds where the agent's own fenced-JSON verdict
differs from the effect-graded one. Both directions matter: a graded FAIL with
a self-reported PASS is either this ordering artifact or a dishonest report,
and the two are distinguished by hand, not by a heuristic here.

Neither count changes any verdict. The selection policy is untouched; this
only makes an order-dependent score visible to whoever reads the table.

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
    ambiguous = sum(
        1 for r in records if ((r.get("oracle") or {}).get("ambiguity") or 0) > 0)
    disagrees = sum(
        1 for r in records
        if (r.get("reporting") or {}).get("parsed")
        and bool((r.get("reporting") or {}).get("passed")) != bool(r.get("passed")))
    return {"n_ambiguous": ambiguous, "n_report_disagrees": disagrees}


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
        out.append(summary)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="default: <results-dir>/regraded")
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
        for s in summaries:
            flags = []
            if s["n_ambiguous"]:
                flags.append(f"{s['n_ambiguous']} ambiguous")
            if s["n_report_disagrees"]:
                flags.append(f"{s['n_report_disagrees']} report-disagree")
            print(f"  {s['case']:24s} {s.get('model') or '-':18s} "
                  f"{s['n_passed']}/{s['n_seeds']} passed"
                  + (f"  [{', '.join(flags)}]" if flags else ""))
    print(f"\n{n_cells} cell summaries from {n_files} record files -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
