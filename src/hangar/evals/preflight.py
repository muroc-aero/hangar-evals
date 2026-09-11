"""Checks that turn an hour-three failure into a ten-second one.

Every check here exists because it already cost a run. A rotated token, an
exhausted plan window, a stopped colima, a ``HANGAR_REPO`` pointing at a moved
checkout — each one converts a whole bundle into error rows without ever failing
loudly, because the harness treats a dead agent as a retryable seed rather than
a broken setup.

The auth probe is also called BETWEEN cases by the campaign runner: a usage
window that closes at hour four should halt the bundle with its remaining cases
untouched, not burn them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from hangar.evals.drivers.claude_cli import redact_secrets
from hangar.evals.drivers.sandbox import ANCHOR_IMAGE

#: The model every anchor probe exercises. Keep in step with
#: ``configs/lane_c_anchor/*.json`` — a probe against a model the cases will not
#: use proves nothing about the ones they will.
ANCHOR_MODEL = os.environ.get("ANCHOR_MODEL", "claude-opus-5")

# The Claude CLI exits 0 on an API error and reports it in-band, so a clean exit
# status is not evidence of a working credential. These are the in-band forms.
_API_ERROR_RE = re.compile(
    r"api error|invalid|expired|usage limit|rate limit", re.IGNORECASE)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def __str__(self) -> str:
        return f"{'OK  ' if self.ok else 'FAIL'} {self.name}: {self.detail}"


def container_runtime() -> CheckResult:
    """The sandboxed arms need a reachable docker/colima daemon."""
    if shutil.which("docker") is None:
        return CheckResult("container runtime", False, "no `docker` on PATH")
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if proc.returncode != 0:
        return CheckResult(
            "container runtime", False,
            "daemon unreachable — start it first "
            "(macOS: `colima start`; Linux: `sudo systemctl start docker`)")
    return CheckResult("container runtime", True, "reachable")


def anchor_image() -> CheckResult:
    """The pinned harness image has to exist locally before a bundle starts."""
    proc = subprocess.run(["docker", "image", "inspect", ANCHOR_IMAGE],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return CheckResult("anchor image", False,
                           f"{ANCHOR_IMAGE} not built — "
                           "`docker build -f containers/anchor.Dockerfile`")
    return CheckResult("anchor image", True, ANCHOR_IMAGE)


def anchor_auth(model: str | None = None, timeout_s: float = 120) -> CheckResult:
    """ONE live turn in the anchor image — seconds, and negligible usage.

    Catches the two failures that otherwise fill a bundle with error rows: a
    stale or rotated token, and an exhausted plan window. The token reaches the
    container through a bare ``-e`` passthrough, so it is never in argv; any
    output that might echo it is redacted before it is shown.
    """
    model = model or ANCHOR_MODEL
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return CheckResult(
            "anchor auth", False,
            "CLAUDE_CODE_OAUTH_TOKEN unset — run `claude setup-token` once, store "
            "it in 1Password, and launch under `op run --env-file=op.env --`")
    argv = ["docker", "run", "--rm", "-e", "CLAUDE_CODE_OAUTH_TOKEN", ANCHOR_IMAGE,
            "claude", "-p", "reply with OK", "--model", model,
            "--max-turns", "1", "--setting-sources", ""]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return CheckResult("anchor auth", False,
                           f"probe did not answer within {timeout_s:.0f}s")
    out = redact_secrets((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 or _API_ERROR_RE.search(out):
        tail = " | ".join(out.strip().splitlines()[-3:]) or "(no output)"
        return CheckResult("anchor auth", False,
                           f"probe failed (exit {proc.returncode}): {tail}")
    return CheckResult("anchor auth", True, f"{model} answered")


def hangar_refs() -> CheckResult:
    """the-hangar must resolve — it is where every Lane A reference comes from.

    Deliberately does NOT compute a reference: ``lane_a_reference`` runs the real
    Lane A script (``ocp_three_tool`` is ~70 min). Resolving the repo and its
    examples directory catches the failure that actually happens — a moved
    checkout or an unset ``HANGAR_REPO`` — at no cost.
    """
    try:
        from hangar.evals.hangar_ref import examples_dir, resolve_hangar_repo

        repo = resolve_hangar_repo()
        examples = examples_dir(repo)
    except Exception as exc:  # noqa: BLE001 — any resolution failure is the answer
        return CheckResult("hangar refs", False,
                           f"{type(exc).__name__}: {exc} "
                           "(set HANGAR_REPO to the-hangar checkout)")
    if not examples.is_dir():
        return CheckResult("hangar refs", False, f"no examples dir at {examples}")
    return CheckResult("hangar refs", True, str(repo))


#: Name -> check, for campaign specs to reference by string.
CHECKS = {
    "container_runtime": container_runtime,
    "anchor_image": anchor_image,
    "anchor_auth": anchor_auth,
    "hangar_refs": hangar_refs,
}


def run_checks(names) -> list[CheckResult]:
    """Run named checks in order, stopping at the first failure.

    Stopping early is the point: a failed container check makes the auth probe's
    failure uninformative, and two red lines are harder to act on than one.
    """
    results = []
    for name in names:
        try:
            result = CHECKS[name]()
        except KeyError:
            result = CheckResult(name, False,
                                 f"unknown check (have: {', '.join(CHECKS)})")
        results.append(result)
        if not result.ok:
            break
    return results
