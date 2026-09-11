"""One command that runs an arm end to end and leaves a filled-in table behind.

The bundle scripts this replaces were bash loops around ``python -m
hangar.evals.run``, and they redirected every seed's output into a log file. The
terminal showed ``START`` and ``DONE``; the results existed but nobody could see
them. On 2026-09-10 the very first seed of the very first case raised, and that
was legible only at 02:54 the next morning, after 7h48m.

An arm is described by the publication manifest it already had —
``examples/lane_c_pub_anchor.yaml`` and its siblings — read through the SAME
``overrides`` mapping the have-agent bridge uses (``config_from_overrides``). So
a manifest means one thing whichever front door drives it, and
``notes/eval-ops-design.md``'s "the manifest is the unit, adding an arm is a new
YAML" holds here too. This module is the near-term executor: no control-plane
DB, no worker pool, no approval gate — just the manifest, in order, in process.

Calling ``run_matrix`` IN PROCESS is what makes the rest fall out: per-seed
output already goes to the terminal (``run.py`` has always printed it well), the
``CellSummary`` comes back as a value instead of as text to re-parse, and the
records path is known rather than globbed for.

Four properties the bash loop could not have:

* **Preflight** — auth, container runtime, image, and the-hangar resolve BEFORE
  case one, so a setup failure costs ten seconds instead of a night. What gets
  checked is derived from the manifest: an arm with no ``claude`` cell is never
  blocked on a credential it does not use.
* **A live table** — re-rendered after every case, so a campaign is reviewable
  while it runs and a crash at case 9 still leaves 8 cases tabulated.
* **A manifest of the run** — what ran, under which config, at which SHA, with
  what outcome, as one artifact instead of prose in a log.
* **Honest resume** — ``results_index.case_status`` distinguishes a graded
  failure (a result; leave it) from an error row (an absence; retry it), which
  the old ``n_seeds >= seeds`` check could not.

Post-run it regrades and re-renders the paper's tables, so "run the arm" and
"the paper's numbers are current" stop being separate acts of memory.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hangar.evals import preflight, report
from hangar.evals.environment import capture_environment
from hangar.evals.hangar_ref import resolve_hangar_repo
from hangar.evals.have_bridge import config_from_overrides
from hangar.evals.regrade import load_records, regrade_file
from hangar.evals.results_index import case_status
from hangar.evals.run import RunConfig, load_resume_records, run_matrix

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_DIR = REPO_ROOT / "examples"

#: Steps that run inside the-hangar checkout. A closed registry, not arbitrary
#: argv from a config file: a campaign names a step, it does not invent one.
HANGAR_STEPS: dict[str, tuple[list[str], str]] = {
    "lane_parity": (
        ["uv", "run", "python", "paper/run_lanes.py"],
        "Lane A/B/C parity suites -> paper/results/lane_parity.jsonl",
    ),
    "lane_c_agent": (
        ["uv", "run", "--with", "claude-agent-sdk",
         "packages/omd/examples/agent_eval/eval_lane_c.py", "all",
         "--save-json", "paper/results/lane_c_agent.json"],
        "Lane C live-agent column -> paper/results/lane_c_agent.json",
    ),
}

#: Composites of the above — ``paper`` is every number in the two tables.
COMPOSITES: dict[str, dict] = {
    "paper": {"hangar_steps": ["lane_parity", "lane_c_agent"],
              "arms": ["anchor", "gemma"],
              "description": "every number in the paper's two tables"},
}


class _Defaults:
    """Executor-level defaults ``config_from_overrides`` reads off an object."""

    def __init__(self, results_dir: str):
        self.results_dir = results_dir
        self.seeds = 1
        self.model = None
        self.max_turns = None
        self.timeout_s = None
        self.omd_transport = "stdio"
        self.sandbox = "none"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


class Tee:
    """Mirror stdout into the campaign log, so scrollback survives the terminal.

    Both sides flush per line. Python block-buffers stdout whenever it is not a
    TTY, and the launch line for a real run is
    ``op run --env-file=op.env -- scripts/evals ...`` — ``op`` proxies the child's
    streams to conceal secrets, so stdout is a pipe and a whole arm's output sat
    in a 8 KB buffer. A runner whose reason to exist is being watchable cannot
    go quiet the moment it is piped into `tee`, a log, or a CI job.
    """

    def __init__(self, stream, path: Path):
        self._stream = stream
        self._fh = path.open("a", buffering=1)

    def write(self, data):
        self._stream.write(data)
        self._fh.write(data)
        if "\n" in data:
            self._stream.flush()
        return len(data)

    def flush(self):
        self._stream.flush()
        self._fh.flush()

    def close(self):
        self._fh.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


# --------------------------------------------------------------------------
# manifests


def manifest_path(arm: str) -> Path:
    """``anchor`` -> ``examples/lane_c_pub_anchor.yaml``; a path passes through."""
    given = Path(arm)
    if given.is_file():
        return given
    path = MANIFEST_DIR / f"lane_c_pub_{arm}.yaml"
    if not path.is_file():
        arms = sorted(p.stem.replace("lane_c_pub_", "")
                      for p in MANIFEST_DIR.glob("lane_c_pub_*.yaml"))
        raise SystemExit(
            f"no arm {arm!r} (have: {', '.join(arms)}; "
            f"or {', '.join(COMPOSITES)}; or a path to a manifest)")
    return path


def load_manifest(path: Path, results_dir: Path) -> tuple[str, list[tuple[str, RunConfig]]]:
    """``(title, [(case_id, RunConfig)])`` from a publication manifest.

    PyYAML is imported here rather than at module scope: hangar-evals declares no
    runtime dependencies, and every other entry point still works without it.
    """
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise SystemExit(
            "reading a manifest needs PyYAML. scripts/evals supplies it via "
            "`uv run --project ../the-hangar`; installing standalone, add it."
        ) from exc

    data = yaml.safe_load(path.read_text())
    defaults = _Defaults(str(results_dir))
    cells = []
    for entry in data.get("cases", []):
        overrides = entry.get("overrides", {})
        cells.append((entry.get("case_id", overrides.get("case", "?")),
                      config_from_overrides(overrides, defaults)))
    return data.get("title", data.get("study", path.stem)), cells


def derive_preflight(cells: list[tuple[str, RunConfig]]) -> tuple[list[str], bool]:
    """``(preflight checks, whether cases need the anchor credential)``.

    Derived from the manifest rather than declared beside it, so the checks
    cannot drift from the cases: an arm with no ``claude`` cell is never blocked
    on a credential it does not use, and one with a ``claude`` cell always probes
    before spending the night on it. Cheap independent checks come first — a
    stop-at-first-failure preflight must not let a missing token hide a broken
    HANGAR_REPO.
    """
    checks = ["hangar_refs"]
    if any(c.sandbox == "container" for _, c in cells):
        checks.append("container_runtime")
    anchor = any("claude" in c.harnesses for _, c in cells)
    if anchor:
        checks += ["anchor_image", "anchor_auth"]
    return checks, anchor


# --------------------------------------------------------------------------
# planning


def plan_rows(cells: list[tuple[str, RunConfig]],
              results_dir: Path) -> list[dict]:
    """Per-cell disposition + a wall-clock estimate, without running anything."""
    return [{"case_id": case_id, "config": config,
             "status": case_status(config, results_dir),
             "estimate_s": _estimate_seconds(config, results_dir)}
            for case_id, config in cells]


def _estimate_seconds(config: RunConfig, results_dir: Path) -> float | None:
    """Seeds x the median wall clock the newest prior run of this case saw.

    Any prior run of the case counts, including a different model's — it is an
    order-of-magnitude figure for "can I start this before dinner", and a wrong
    model is a better prior than no prior. ``None`` when the case is new.
    """
    for path in sorted(Path(results_dir).glob(f"{config.case}_*_summary.json"),
                       reverse=True):
        try:
            for rec in json.loads(path.read_text()):
                wall = rec.get("wall_clock_s")
                if isinstance(wall, dict) and wall.get("median"):
                    return wall["median"] * config.seeds
        except (OSError, json.JSONDecodeError):
            continue
    return None


def print_plan(name: str, rows: list[dict]) -> None:
    todo = [r for r in rows if r["status"].should_run]
    print(f"\n== plan for '{name}': {len(rows)} case(s), "
          f"{len(todo)} to run, {len(rows) - len(todo)} already graded")
    for row in rows:
        status = row["status"]
        mark = {"graded": "skip  ", "resumable": "RESUME",
                "not_started": "run   "}[status.state]
        est = f"~{_hms(row['estimate_s'])}" if row["estimate_s"] else "~?"
        print(f"   {mark} {row['config'].case:<22s} {row['config'].seeds} seeds  "
              f"{est:>8s}   {status.reason}")
    total = sum(r["estimate_s"] for r in todo if r["estimate_s"])
    if total:
        unknown = sum(1 for r in todo if not r["estimate_s"])
        note = f"  ({unknown} case(s) have no prior data)" if unknown else ""
        print(f"\n   estimated wall clock for the {len(todo)} case(s) to run: "
              f"~{_hms(total)}{note}")


# --------------------------------------------------------------------------
# running


def _run_hangar_step(step: str, hangar_repo: Path) -> bool:
    argv, what = HANGAR_STEPS[step]
    print(f"\n[hangar] {step}: {what}")
    print(f"         $ {' '.join(argv)}   (cwd {hangar_repo})")
    proc = subprocess.run(argv, cwd=hangar_repo)
    if proc.returncode != 0:
        print(f"[hangar] {step} exited {proc.returncode} — continuing; the table "
              "will render from whatever it produced.")
    return proc.returncode == 0


def _run_one_case(config: RunConfig, results_dir: Path) -> tuple[str, list[dict]]:
    """Run (or resume) one cell. Returns ``(state, regraded summaries)``."""
    status = case_status(config, results_dir)
    resume_records = None
    if status.is_resume:
        manifest = status.records.with_name(status.records.stem + "_config.json")
        if manifest.is_file():
            stamp = json.loads(manifest.read_text())["stamp"]
            resume_records = load_resume_records(status.records, retry_errors=True)
            print(f"        resuming {status.records.name} — {status.reason}")
        else:
            stamp = _stamp()  # orphaned records; start clean rather than guess
            print(f"        {status.records.name} has no manifest — starting fresh")
    else:
        stamp = _stamp()

    run_matrix(config, stamp, resume_records=resume_records)
    records = Path(config.results_dir).resolve() / f"{config.case}_{stamp}.jsonl"
    summaries = regrade_file(records) if records.is_file() else []
    return case_status(config, results_dir).state, summaries


def run_campaign(name: str, *, only: set[str] | None = None, force: bool = False,
                 results_dir: Path | None = None) -> int:
    results_dir = Path(results_dir or REPO_ROOT / "results")
    composite = COMPOSITES.get(name, {})
    arms = composite.get("arms", [name])
    hangar_steps = composite.get("hangar_steps", [])

    cells: list[tuple[str, RunConfig]] = []
    titles = []
    for arm in arms:
        title, arm_cells = load_manifest(manifest_path(arm), results_dir)
        titles.append(title)
        cells.extend(arm_cells)
    if only:
        unknown = only - {c.case for _, c in cells}
        if unknown:
            print(f"!! --only named unknown case(s): {', '.join(sorted(unknown))}")
            return 2
        cells = [(cid, c) for cid, c in cells if c.case in only]

    checks, _ = derive_preflight(cells)
    stamp = _stamp()
    out_dir = results_dir / "campaigns" / f"{name}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path, manifest_json = out_dir / "table.md", out_dir / "manifest.json"

    tee = Tee(sys.stdout, out_dir / "campaign.log")
    sys.stdout = tee
    started = time.time()
    environment = capture_environment()  # shells out to git; capture once
    summaries: list[dict] = []
    entries: list[dict] = []
    halted = ""

    def flush_state(finished: str | None = None) -> None:
        table_path.write_text(report.render_markdown(
            summaries, note=f"campaign '{name}' {stamp} — live; "
                            "Ambig/Rep-dis gate the Passed column"))
        manifest_json.write_text(json.dumps({
            "campaign": name, "arms": arms, "stamp": stamp,
            "started_utc": datetime.fromtimestamp(
                started, timezone.utc).isoformat(timespec="seconds"),
            "finished_utc": finished, "halted": halted or None,
            "environment": environment, "cases": entries, "cells": summaries,
        }, indent=2))

    try:
        print(f"== campaign '{name}' — "
              f"{composite.get('description') or '; '.join(titles)}")
        for arm in arms:
            print(f"   manifest   {manifest_path(arm).relative_to(REPO_ROOT)}")
        print(f"   results    {out_dir.relative_to(REPO_ROOT)}")
        print(f"   live table {table_path.relative_to(REPO_ROOT)}")

        for result in preflight.run_checks(checks):
            print(f"   preflight  {result}")
            if not result.ok:
                halted = f"preflight: {result.name}"
                return 1

        try:
            hangar_repo = resolve_hangar_repo()
        except Exception:  # noqa: BLE001 — absence is reported, not fatal
            hangar_repo = None

        for step in hangar_steps:
            if hangar_repo is None:
                print(f"\n[hangar] {step}: SKIPPED — the-hangar not resolvable "
                      "(set HANGAR_REPO)")
                continue
            _run_hangar_step(step, hangar_repo)

        print_plan(name, plan_rows(cells, results_dir))
        print()

        total = len(cells)
        for i, (case_id, config) in enumerate(cells, start=1):
            status = case_status(config, results_dir)
            head = f"[{i:2d}/{total}] {config.case}"

            if status.state == "graded" and not force:
                print(f"{head:<40s} SKIP — {status.reason}   {_ts()}")
                entries.append({"case": config.case, "case_id": case_id,
                                "status": "skipped", "reason": status.reason})
                summaries.extend(regrade_file(status.records)
                                 if status.records else [])
                flush_state()
                continue

            # Probe only what this cell needs: the anchor credential matters
            # before a claude cell and is meaningless before an on-device one.
            precase, _ = derive_preflight([(case_id, config)])
            failed = [c for c in preflight.run_checks(
                [c for c in precase if c == "anchor_auth"]) if not c.ok]
            if failed:
                print(f"\n=== '{name}' HALTED before {config.case} — {failed[0]}")
                print(f"    {total - i + 1} case(s) not started; nothing was "
                      "burned. Re-run the same command once the cause is "
                      "cleared — graded cases are skipped and partial ones "
                      "resume.")
                halted = f"precase {failed[0].name}: {failed[0].detail}"
                break

            print(f"{head:<40s} {_ts()}")
            t0 = time.time()
            entry = {"case": config.case, "case_id": case_id,
                     "started": datetime.now(timezone.utc).isoformat(
                         timespec="seconds")}
            try:
                state, cell_summaries = _run_one_case(config, results_dir)
            except KeyboardInterrupt:
                entry.update(status="interrupted", elapsed_s=time.time() - t0)
                entries.append(entry)
                halted = f"interrupted during {config.case}"
                print(f"\n=== interrupted during {config.case} — its finished "
                      "seeds are saved and will resume.")
                break
            except Exception as exc:  # noqa: BLE001 — one bad case must not
                entry.update(status="failed",              # kill the campaign
                             error=f"{type(exc).__name__}: {exc}",
                             elapsed_s=time.time() - t0)
                entries.append(entry)
                print(f"        FAILED {type(exc).__name__}: {exc}")
                flush_state()
                continue

            elapsed = time.time() - t0
            entry.update(status=state, elapsed_s=elapsed,
                         finished=datetime.now(timezone.utc).isoformat(
                             timespec="seconds"))
            entries.append(entry)
            summaries.extend(cell_summaries)
            for summary in cell_summaries:
                print(f"        cell {summary['n_passed']}/{summary['n_seeds']}"
                      f" {report.flags(summary)}  ·  {_hms(elapsed)}"
                      f"   [table.md: {len(summaries)} cell(s)]")
            flush_state()

    finally:
        flush_state(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        _finish(name, summaries, out_dir, results_dir, started, halted)
        sys.stdout = tee._stream
        tee.close()
    return 1 if halted else 0


def _finish(name: str, summaries: list[dict], out_dir: Path, results_dir: Path,
            started: float, halted: str) -> None:
    """Regrade, re-render, and print. On success, failure, and Ctrl-C alike.

    Especially on Ctrl-C: the reason to interrupt a run is usually that you want
    to look at it, and re-deriving the table by hand is exactly the friction this
    module exists to remove.
    """
    print(f"\n=== '{name}' cases done in {_hms(time.time() - started)}"
          + (f" — HALTED: {halted}" if halted else ""))

    from hangar.evals.regrade import main as regrade_main

    print("\n[post] regrade — re-deriving every summary from records")
    regrade_main(["--results-dir", str(results_dir), "--quiet"])
    _render_paper_tables(results_dir)

    print()
    print(report.render_terminal(summaries, f"== '{name}' results"))

    # Harness health is kept OUT of the results table on purpose: these are
    # defects in the measurement, and a defect belongs in a fix, not in every
    # future reader's way. The target is a forced re-run that reports none.
    health = {"n_ambiguous": 0, "n_degraded": 0}
    for cell in summaries:
        for key, value in (cell.get("harness_health") or {}).items():
            health[key] = health.get(key, 0) + value
    if any(health.values()):
        print("\n== harness health (fix these, do not annotate them)")
        if health["n_ambiguous"]:
            print(f"   {health['n_ambiguous']} seed(s) scored on whichever "
                  "same-mode run ran LAST — prompts that ask for a comparison "
                  "run against a policy that grades the last one.")
        if health["n_degraded"]:
            print(f"   {health['n_degraded']} seed(s) graded but their harness "
                  "exited abnormally on the way.")
        print("   Neither changed a verdict. Both mean this arm is not yet a "
              "clean measurement — re-run with --force once fixed.")

    print(f"\n   table.md   {out_dir / 'table.md'}")
    print(f"   manifest   {out_dir / 'manifest.json'}")
    print(f"   full log   {out_dir / 'campaign.log'}")


def _render_paper_tables(results_dir: Path) -> None:
    """Re-render the paper's tables from the regraded summaries.

    Skipped with a notice rather than an error when the-hangar is absent:
    hangar-evals has to stay usable on its own, and a missing paper checkout is
    not a failed eval run.
    """
    try:
        hangar_repo = resolve_hangar_repo()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[post] paper tables: SKIPPED — {exc}")
        return
    argv = ["uv", "run", "python", "paper/make_tables.py",
            "--evals-dir", str((results_dir / "regraded").resolve())]
    print(f"\n[post] paper tables — $ {' '.join(argv)}   (cwd {hangar_repo})")
    # Captured rather than inherited: it is a handful of lines and it is the
    # record of which table was written, so it belongs in campaign.log too.
    proc = subprocess.run(argv, cwd=hangar_repo, capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        print(f"       {line}")
    if proc.returncode != 0:
        print(f"[post] make_tables.py exited {proc.returncode}: "
              f"{(proc.stderr or '').strip()[-500:]}")


# --------------------------------------------------------------------------
# CLI


def _stored_cells(results_dir: Path) -> list[dict]:
    """Regraded summaries for everything in ``results/``, newest per cell."""
    latest: dict[tuple, dict] = {}
    for path in sorted(results_dir.glob("*.jsonl")):
        try:
            for summary in regrade_file(path):
                latest[(summary["case"], summary["harness"],
                        summary["model"])] = summary
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            continue
    return [latest[k] for k in sorted(latest)]


def review_rows(results_dir: Path) -> list[dict]:
    """Seeds a person has to adjudicate, newest records first.

    A disagreement between the agent's own verdict and the effect grade cannot
    be resolved by rule: the same signature covers an agent that misreported its
    numbers and a grading policy that scored the wrong one of the agent's runs.
    Both need someone to open the artifacts, so this hands over the paths.
    """
    rows = []
    for path in sorted(results_dir.glob("*.jsonl"), reverse=True):
        try:
            records = load_records(path)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        for r in records:
            rep = r.get("reporting") or {}
            if not rep.get("parsed"):
                continue
            if bool(rep.get("passed")) == bool(r.get("passed")):
                continue
            rows.append({
                "case": r["case"], "model": r.get("model"), "seed": r.get("seed"),
                "graded": "PASS" if r.get("passed") else "FAIL",
                "reported": "PASS" if rep.get("passed") else "FAIL",
                "scores": r.get("scores") or [],
                "data_root": r.get("data_root"), "workspace": r.get("workspace"),
                "ambiguity": (r.get("oracle") or {}).get("ambiguity") or 0,
            })
    return rows


def print_review(rows: list[dict]) -> None:
    if not rows:
        print("Nothing awaiting review — every parsed report agrees with its grade.")
        return
    print(f"== {len(rows)} seed(s) awaiting review\n")
    for row in rows:
        print(f"  {row['case']} · {row['model']} · seed {row['seed']}")
        print(f"     graded {row['graded']}, agent reported {row['reported']}")
        for sc in row["scores"]:
            got = "null" if sc.get("agent") is None else f"{sc['agent']:.6g}"
            if sc.get("verdict") != "PASS":
                print(f"       {sc['key']:<16s} ref={sc['lane_a']:.6g} "
                      f"graded-run={got}  -> {sc['verdict']}")
        if row["ambiguity"]:
            print(f"     NOTE: the oracle skipped {row['ambiguity']} successful "
                  "same-mode run(s) — the grade may be of the wrong run")
        print(f"     provenance {row['data_root']}/analysis.db")
        if row["workspace"]:
            print(f"     transcript {row['workspace']}/claude_events.jsonl")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evals", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run an arm (or 'paper') end to end")
    p_run.add_argument("campaign",
                       help="arm name (anchor/gemma/qwen), 'paper', or a "
                            "path to a publication manifest")
    p_run.add_argument("--only", default=None,
                       help="comma-separated case names — run just these")
    p_run.add_argument("--force", action="store_true",
                       help="re-run cases already graded (default: skip them)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="preflight and plan only — no agent calls, no spend")
    p_run.add_argument("--results-dir", type=Path, default=None)

    for name, help_text in (("status", "print the table from stored results"),
                            ("table", "regrade and re-render the paper tables"),
                            ("review", "list seeds awaiting a human look")):
        p = sub.add_parser(name, help=f"{help_text}; run nothing")
        p.add_argument("--results-dir", type=Path, default=None)

    args = parser.parse_args(argv)
    results_dir = Path(args.results_dir or REPO_ROOT / "results")

    try:  # see Tee: a piped stdout block-buffers, and every real launch is piped
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # already wrapped, or not reconfigurable
        pass

    if args.cmd == "review":
        print_review(review_rows(results_dir))
        return 0

    if args.cmd == "status":
        print(report.render_terminal(_stored_cells(results_dir),
                                     f"== stored results ({results_dir})"))
        return 0

    if args.cmd == "table":
        from hangar.evals.regrade import main as regrade_main

        regrade_main(["--results-dir", str(results_dir), "--quiet"])
        _render_paper_tables(results_dir)
        return 0

    if args.dry_run:
        composite = COMPOSITES.get(args.campaign, {})
        cells = []
        for arm in composite.get("arms", [args.campaign]):
            cells.extend(load_manifest(manifest_path(arm), results_dir)[1])
        checks, _ = derive_preflight(cells)
        for result in preflight.run_checks(checks):
            print(f"   preflight  {result}")
        for step in composite.get("hangar_steps", []):
            print(f"   would run  [hangar] {step}: {HANGAR_STEPS[step][1]}")
        print_plan(args.campaign, plan_rows(cells, results_dir))
        print("\n   dry run — nothing was executed.")
        return 0

    only = {c.strip() for c in args.only.split(",")} if args.only else None
    return run_campaign(args.campaign, only=only, force=args.force,
                        results_dir=results_dir)


if __name__ == "__main__":
    raise SystemExit(main())
