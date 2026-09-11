"""What a prior run already produced, for the cells of one run config.

Before spending an hour on a case, a bundle runner needs one answer: has this
cell already been run, and if so, is its record set *clean* or does it carry
seeds worth retrying?

``scripts/_done.py`` used to answer only the first half. It asked whether the
cell's summary had ``n_seeds >= seeds`` — and an error row counts as a seed, so
a cell whose first seed died on a transient looked finished forever. Retrying
meant finding the ``.jsonl`` by hand and passing it to ``--resume``. Three seeds
of the 2026-09-10 anchor arm were lost exactly that way: seed 0 of
``ocp_caravan_basic`` raised at 19:07, and the case was skipped on every
subsequent run of the bundle.

So the question is answered from the RECORDS, not the summary — the records are
where an error row is still distinguishable from a graded failure. A graded FAIL
is a result and stays put; an error row is an absence and comes back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hangar.evals.regrade import load_records

#: A cell is ``graded`` (leave it alone), ``resumable`` (records exist but some
#: seeds are missing or errored — resume that file), or ``not_started``.
STATES = ("graded", "resumable", "not_started")


@dataclass(frozen=True)
class CaseStatus:
    """The disposition of one run config against what is already on disk."""

    state: str
    records: Path | None      # the .jsonl to --resume; None when not_started
    n_seeds_found: int
    n_seeds_wanted: int
    n_error_seeds: int
    n_passed: int
    reason: str

    @property
    def should_run(self) -> bool:
        return self.state != "graded"

    @property
    def is_resume(self) -> bool:
        return self.state == "resumable"


def resolve_model(config, harness: str) -> str:
    """The model string ``run_matrix`` will actually record for ``harness``.

    ``RunConfig.model`` overrides every harness default when set; otherwise the
    harness's own pinned default applies. Resolving it the same way here is what
    keeps a config's records findable — records store the resolved string.
    """
    from hangar.evals.run import HARNESSES

    return config.model or HARNESSES[harness][1]


def case_status(config, results_dir: Path | str | None = None) -> CaseStatus:
    """Disposition of ``config``'s cells against the newest matching records.

    Files are scanned newest first (the stamp sorts lexically as it sorts
    chronologically) and the first one covering every expected cell wins — an
    older run of the same case under a different model is not an answer about
    this one, so it is skipped rather than mistaken for one.
    """
    results = Path(results_dir if results_dir is not None else config.results_dir)
    expected = {(h, resolve_model(config, h)) for h in config.harnesses}
    wanted = config.seeds * len(expected)

    if not results.is_dir():
        return CaseStatus("not_started", None, 0, wanted, 0, 0,
                          f"no results dir at {results}")

    for path in sorted(results.glob(f"{config.case}_*.jsonl"), reverse=True):
        try:
            records = [r for r in load_records(path) if r.get("case") == config.case]
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        if not expected <= {(r["harness"], r["model"]) for r in records}:
            continue  # a different arm's file for the same case
        mine = [r for r in records if (r["harness"], r["model"]) in expected]
        found = len({(r["harness"], r["model"], r["seed"]) for r in mine})
        errors = sum(1 for r in mine if r.get("error"))
        passed = sum(1 for r in mine if r.get("passed"))
        if errors:
            return CaseStatus("resumable", path, found, wanted, errors, passed,
                              f"{errors} error row(s) to retry")
        if found < wanted:
            return CaseStatus("resumable", path, found, wanted, 0, passed,
                              f"{wanted - found} seed(s) never ran")
        return CaseStatus("graded", path, found, wanted, 0, passed,
                          f"{passed}/{found} passed")

    return CaseStatus("not_started", None, 0, wanted, 0, 0, "no records found")
