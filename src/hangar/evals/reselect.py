"""Re-score stored records against their provenance DBs — no agent re-runs.

``regrade`` re-derives summaries from records and never re-opens a DB, which
is right for back-filling a summary field. It cannot help when the *grading
policy* changes, because the verdict itself is then stale.

This module can. A seed's effect grade is a pure function of three things it
already persisted: the omd provenance DB under ``data_root``, the run the
agent named in its report, and the Lane A references (cached against
the-hangar's SHA). None of them involve the model, so a policy change can be
applied to arms that already ran — including arms whose model is retired —
without spending a token.

It exists for the 2026-09-12 selection change (grade the run the agent NAMED,
not whichever ran last; see ``oracle``). Re-scoring the 2026-09-11 anchor arm
under the new policy is not a re-run and must not be described as one: the
agent's work is untouched, only our reading of it changes. Every record it
rewrites carries ``reselect`` provenance saying so.

Records written before 2026-09-12 have no ``oracle.reported_run_id``, so the
id is recovered from the seed's stored event stream
(``<workspace>/claude_events.jsonl``) — the same text the grader parsed live.
A seed whose workspace or DB is gone cannot be re-scored and is left exactly
as it was, counted as ``skipped``.

Usage:
    python -m hangar.evals.reselect --campaign results/campaigns/<stamp>/manifest.json
    python -m hangar.evals.reselect --records results/foo.jsonl --dry-run

Scope it to one arm. ``--campaign`` is the way to do that: it takes the files
a given campaign wrote, so a re-score covers exactly the arm you mean and
leaves superseded arms in the same directory alone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

from hangar.evals.cases import CASES
from hangar.evals.drivers.claude_cli import parse_stream_json
from hangar.evals.oracle import (
    FALLBACK_LAST,
    effect_values,
    oracle_ambiguity,
    read_effect_runs,
    report_matches_effects,
    selection_basis,
)
from hangar.evals.scoring import (
    compute_refs,
    extract_report,
    for_reporting,
    score_report,
    score_values,
)

# Stamped into every rewritten record so a re-scored arm is never mistaken for
# a fresh one, and a second pass over the same file is idempotent.
POLICY = "named-run-2026-09-12"


@dataclass
class Outcome:
    """What happened to one seed."""

    record: dict
    status: str          # "changed" | "unchanged" | "skipped"
    reason: str = ""

    @property
    def verdict_flipped(self) -> bool:
        return self.status == "changed" and self.reason.startswith("verdict")


def recover_report(record: dict) -> dict | None:
    """The agent's fenced-JSON report for this seed, from its event stream.

    Returns ``None`` when the workspace is gone or holds no parseable report —
    both of which mean the seed keeps its stored grade.
    """
    workspace = record.get("workspace")
    if not workspace:
        return None
    events = Path(workspace) / "claude_events.jsonl"
    if not events.exists():
        return None
    final_text = parse_stream_json(events.read_text(), "omd").final_text
    try:
        return extract_report(final_text)
    except ValueError:
        return None


def reported_run_id(record: dict, report: dict | None) -> str | None:
    """The run the agent named: from the record if present, else the report."""
    oracle = record.get("oracle") or {}
    if "reported_run_id" in oracle:
        rid = oracle["reported_run_id"]
    else:
        rid = (report or {}).get("run_id")
    return rid if isinstance(rid, str) and rid else None


def reselect_record(record: dict, *, cache_dir: Path | None = None) -> Outcome:
    """Re-score one seed under the current selection policy."""
    case = CASES.get(record.get("case"))
    if case is None:
        return Outcome(record, "skipped", f"unknown case {record.get('case')!r}")
    if record.get("error"):
        return Outcome(record, "skipped", "harness error: nothing was graded")
    db = Path(record.get("data_root") or "") / "analysis.db"
    if not db.exists():
        return Outcome(record, "skipped", f"no provenance DB at {db}")

    report = recover_report(record)
    rid = reported_run_id(record, report)
    runs = read_effect_runs(db)
    refs = compute_refs(case.example, case.metrics, cache_dir=cache_dir)

    effects = effect_values(case.metrics, runs, rid)
    score = score_values(case.metrics, effects, refs)

    was_passed = bool(record.get("passed"))
    was_scores = record.get("scores")

    new = dict(record)
    new["passed"] = score.passed
    new["scores"] = _scores_to_dicts(score)
    new["oracle"] = {
        **(record.get("oracle") or {}),
        "ambiguity": oracle_ambiguity(case.metrics, runs),
        "reported_run_id": rid,
        "selection": selection_basis(case.metrics, runs, rid),
    }
    if report is not None:
        new["reporting"] = {
            **(record.get("reporting") or {}),
            "matches_effects": report_matches_effects(case.metrics, report, effects),
            "passed": score_report(for_reporting(case.metrics), report, refs).passed,
        }
    new["reselect"] = {"policy": POLICY,
                       "at": dt.datetime.now(dt.timezone.utc).isoformat()}

    if score.passed != was_passed:
        return Outcome(new, "changed",
                       f"verdict {was_passed} -> {score.passed} "
                       f"(graded {rid or 'last run'})")
    if new["scores"] != was_scores:
        return Outcome(new, "changed", "metric values moved, verdict unchanged")
    return Outcome(new, "unchanged")


def reselect_file(path: Path, *, cache_dir: Path | None = None,
                  dry_run: bool = False) -> list[Outcome]:
    """Re-score every seed in one records file, rewriting it in place.

    The file keeps its shape: one JSON object per line, in the original order,
    so a resumed file's superseded rows stay where they are and ``regrade``
    still de-duplicates them the same way.
    """
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    outcomes = [reselect_record(json.loads(l), cache_dir=cache_dir) for l in lines]
    if not dry_run and any(o.status == "changed" for o in outcomes):
        path.write_text(
            "".join(json.dumps(o.record) + "\n" for o in outcomes))
    return outcomes


def _scores_to_dicts(score) -> list[dict] | None:
    if score is None:
        return None
    return [
        {"key": s.key, "lane_a": s.lane_a, "agent": s.agent,
         "rel_err": s.rel_err, "verdict": s.verdict}
        for s in score.scores
    ]


def campaign_records(manifest_path: Path, results_dir: Path) -> list[Path]:
    """The record file each of a campaign's cases wrote to.

    Deliberately not a start/finish time window. Two things break a window:
    a forced re-run appends to the case's existing file instead of starting a
    new one (so the file's name predates the campaign), and re-scoring rewrites
    the file (so its mtime postdates it) — which would make this function
    return fewer files the second time it ran.

    What is stable is that the campaign was the last thing to write each of its
    cases, so the newest file per case is its file. That is the same rule
    ``results_index`` uses to answer "is this case done?", and it keeps a
    re-score off the superseded arms sitting in the same directory.
    """
    manifest = json.loads(Path(manifest_path).read_text())
    paths = set()
    for case in {c["case"] for c in manifest.get("cases", [])}:
        matches = list(results_dir.glob(f"{case}_*.jsonl"))
        if matches:
            paths.add(max(matches, key=lambda p: (os.path.getmtime(p), p.name)))
    return sorted(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", type=Path, action="append", default=None,
                        help="a records .jsonl (repeatable); default: all in "
                             "--results-dir")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--campaign", type=Path, default=None,
                        help="a campaign manifest.json; re-score exactly the "
                             "files that campaign wrote")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, write nothing")
    args = parser.parse_args(argv)

    if args.campaign:
        paths = campaign_records(args.campaign, args.results_dir)
    else:
        paths = args.records or sorted(args.results_dir.glob("*.jsonl"))
    if not paths:
        print("no records files matched")
        return 1

    cache_dir = args.results_dir / "ref_cache"
    totals = {"changed": 0, "unchanged": 0, "skipped": 0}
    flips = fallbacks = 0
    for path in paths:
        outcomes = reselect_file(path, cache_dir=cache_dir, dry_run=args.dry_run)
        for o in outcomes:
            totals[o.status] += 1
            flips += o.verdict_flipped
            # Only a fallback with something to choose BETWEEN was a guess;
            # falling back on a seed with one run picks the only candidate.
            oracle = o.record.get("oracle") or {}
            fallbacks += (oracle.get("selection") == FALLBACK_LAST
                          and (oracle.get("ambiguity") or 0) > 0)
            if o.status != "unchanged":
                r = o.record
                print(f"  {r.get('case','?'):24s} seed {r.get('seed','?')}  "
                      f"{o.status}: {o.reason}")

    print(f"\n{flips} verdict(s) changed; {totals['changed']} record(s) rewritten, "
          f"{totals['unchanged']} unchanged, {totals['skipped']} skipped")
    print(f"{fallbacks} seed(s) had their graded run chosen for them — the "
          f"agent named no usable run and had made more than one")
    if args.dry_run:
        print("(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
