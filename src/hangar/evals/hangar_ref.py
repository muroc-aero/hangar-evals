"""The seam: resolve the-hangar and compute Lane A reference values.

hangar-evals benchmarks local LLMs against the Hangar tools, but it owns no
ground truth of its own. Every reference number comes from the-hangar's Lane A
scripts, reached through this one module. ``hangar_ref`` is the ONLY place that
knows where the-hangar lives on disk; everything downstream imports references
and tolerances through it.

Resolution follows the existing Hangar convention (see
``lakesideai-infra/scripts/package-case-study.sh``): the ``HANGAR_REPO``
environment variable, defaulting to a sibling checkout at ``../the-hangar``.

References are computed exactly as
``packages/omd/examples/agent_eval/eval_lane_c.py`` does it: one subprocess per
``(example, module)``, because each example's ``shared.py`` collides on
``sys.path`` when several examples are imported into a single process. The
subprocess runs under the current interpreter (``sys.executable``), which must
therefore have the-hangar installed -- the documented dev setup is
hangar-evals editable-installed into a venv that also has the-hangar.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Layout inside the-hangar (stable; part of the seam contract). All Lane A
# example packages live under this directory.
EXAMPLES_SUBDIR = Path("packages/omd/examples")

# hangar_ref.py is src/hangar/evals/hangar_ref.py, so parents[3] is the repo
# root and its sibling is the default the-hangar checkout.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HANGAR_REPO = _REPO_ROOT.parent / "the-hangar"


def resolve_hangar_repo() -> Path:
    """Return the absolute path to the-hangar checkout.

    Honors ``$HANGAR_REPO``; otherwise defaults to a sibling ``../the-hangar``.
    Raises ``FileNotFoundError`` with a clear message if the resolved path is
    missing or does not look like the-hangar (no ``packages/omd/examples``).
    """
    raw = os.environ.get("HANGAR_REPO")
    repo = (Path(raw).expanduser() if raw else DEFAULT_HANGAR_REPO).resolve()
    source = f"$HANGAR_REPO={raw!r}" if raw else f"default sibling {DEFAULT_HANGAR_REPO}"

    if not repo.is_dir():
        raise FileNotFoundError(
            f"the-hangar not found at {repo} ({source}). "
            f"Set HANGAR_REPO to your the-hangar checkout."
        )
    if not (repo / EXAMPLES_SUBDIR).is_dir():
        raise FileNotFoundError(
            f"{repo} ({source}) does not look like the-hangar: "
            f"missing {EXAMPLES_SUBDIR}. Set HANGAR_REPO to the repo root."
        )
    return repo


def examples_dir(hangar_repo: Path | None = None) -> Path:
    """Return the ``packages/omd/examples`` directory inside the-hangar."""
    repo = hangar_repo or resolve_hangar_repo()
    return repo / EXAMPLES_SUBDIR


def lane_a_reference(
    example: str, module: str, hangar_repo: Path | None = None
) -> dict:
    """Compute a Lane A reference by running ``<example>.lane_a.<module>.run()``.

    Runs one subprocess under the current interpreter, exactly like
    ``eval_lane_c.py``. The interpreter must have the-hangar installed; if the
    reference script raises (e.g. a missing dependency), the subprocess stderr
    is surfaced in the raised ``RuntimeError``.
    """
    repo = hangar_repo or resolve_hangar_repo()
    examples = repo / EXAMPLES_SUBDIR
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(examples)!r})\n"
        f"from {example}.lane_a.{module} import run\n"
        "print(json.dumps(run(), default=float))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Lane A reference {example}.lane_a.{module} failed "
            f"(interpreter {sys.executable}):\n{proc.stderr}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])
