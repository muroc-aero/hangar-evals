"""Sandbox guarantees (Step 14a): workspace policy, mount discipline, isolation.

The unit tests pin the two structural properties everything else leans on:
the workspace root is mountable (under $HOME — colima bind-mounts from
anywhere else appear silently EMPTY) and outside both repos, and the docker
wrapper mounts NOTHING but the workspace. The live tests (slow; need a
running colima + the built image) prove the isolation itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hangar.evals.drivers import sandbox as sandbox_mod
from hangar.evals.drivers.sandbox import (
    ANCHOR_IMAGE,
    CONTAINER_WORKSPACE,
    OPENCODE_IMAGE,
    ContainerSandbox,
    make_workspace,
)
from hangar.evals.hangar_ref import resolve_hangar_repo

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _docker_image_present(image: str = ANCHOR_IMAGE) -> bool:
    try:
        return subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=30,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def test_workspace_root_is_mountable_and_outside_both_repos():
    root = sandbox_mod.WORKSPACE_ROOT
    # Under $HOME: colima's VM mounts only $HOME and /tmp/colima — a
    # bind-mount from anywhere else (e.g. /var/folders) is silently EMPTY.
    assert root.is_relative_to(Path.home())
    # Outside this repo (threat (e)) and outside the-hangar (threat (a)).
    assert not root.is_relative_to(_REPO_ROOT)
    assert not root.is_relative_to(resolve_hangar_repo())


def test_make_workspace_creates_fresh_dirs_under_root(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_mod, "WORKSPACE_ROOT", tmp_path / "workspaces")
    a = make_workspace("paraboloid_claude_s0")
    b = make_workspace("paraboloid_claude_s0")
    assert a != b and a.is_dir() and b.is_dir()
    assert a.parent == (tmp_path / "workspaces").resolve()
    assert a.name.startswith("paraboloid_claude_s0_")


def test_wrap_argv_mounts_only_the_workspace(monkeypatch, tmp_path):
    # The token must cross as a bare `-e VAR` (value read from the docker
    # client's env) — never as a value in argv.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-SECRET")
    argv = ContainerSandbox().wrap_argv(["claude", "--version"], tmp_path)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "sk-ant-oat-SECRET" not in " ".join(argv)
    assert argv[argv.index("-e") + 1] == "CLAUDE_CODE_OAUTH_TOKEN"
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == [f"{tmp_path.resolve()}:{CONTAINER_WORKSPACE}"]
    assert argv[argv.index("-w") + 1] == CONTAINER_WORKSPACE
    # Image, then the inner argv verbatim at the end.
    assert argv[argv.index(ANCHOR_IMAGE) + 1:] == ["claude", "--version"]


# --- live isolation proof (slow: needs colima running + the image built) -------


needs_image = pytest.mark.skipif(
    not _docker_image_present(),
    reason=f"docker unavailable or {ANCHOR_IMAGE} not built",
)


@pytest.mark.slow
@needs_image
def test_container_cannot_see_either_repo_but_sees_workspace():
    # A REAL workspace (under $HOME) — pytest's tmp_path lives in /var/folders,
    # which colima can't mount (it comes up empty AND read-only in-container;
    # this test originally failed exactly that way).
    import shutil

    ws = make_workspace("isolation_test")
    (ws / "probe.txt").write_text("probe")
    hangar_file = resolve_hangar_repo() / "packages/omd/examples/agent_eval/eval_lane_c.py"
    assert hangar_file.exists()  # the target is real ON THE HOST
    script = (
        f"cat {hangar_file} 2>/dev/null && echo HANGAR_LEAKED; "
        f"ls {_REPO_ROOT}/results 2>/dev/null && echo RESULTS_LEAKED; "
        f"cat /workspace/probe.txt; "
        f"echo written-from-container > /workspace/out.txt"
    )
    argv = ContainerSandbox().wrap_argv(["sh", "-c", script], ws)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        assert "HANGAR_LEAKED" not in proc.stdout
        assert "RESULTS_LEAKED" not in proc.stdout
        # The mount is real (not the silent-empty failure mode) and writable.
        assert "probe" in proc.stdout
        assert (ws / "out.txt").read_text().strip() == "written-from-container"
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.slow
@needs_image
def test_container_reaches_omd_over_advertised_url(tmp_path):
    from hangar.evals.omd_service import OmdHttpService

    service = OmdHttpService(tmp_path / "omd", advertise_host="host.docker.internal")
    with service as spec:
        # node has fetch; ANY http status proves transport + forwarding work.
        js = (f"fetch('{spec.url}')"
              ".then(r => console.log('STATUS', r.status))"
              ".catch(e => { console.log('FETCH_FAILED', e.message); process.exit(1); })")
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = ContainerSandbox().wrap_argv(["node", "-e", js], ws)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert "STATUS" in proc.stdout, proc.stdout + proc.stderr
    # Reachability is NOT acceptance (the 421 lesson): the first live smoke
    # reached the server but every request 421'd on the foreign Host header
    # and the MCP connect hung at "pending". The advertised host must be
    # ADMITTED, not merely routable.
    status = int(proc.stdout.split("STATUS", 1)[1].split()[0])
    assert status != 421, proc.stdout


@pytest.mark.slow
@needs_image
def test_container_claude_cli_version_matches_pin(tmp_path):
    # The workspace mount is unused here — any dir does.
    argv = ContainerSandbox().wrap_argv(["claude", "--version"], tmp_path)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    version = ANCHOR_IMAGE.rsplit("-", 1)[-1]
    assert version in proc.stdout


needs_opencode_image = pytest.mark.skipif(
    not _docker_image_present(OPENCODE_IMAGE),
    reason=f"docker unavailable or {OPENCODE_IMAGE} not built",
)


@pytest.mark.slow
@needs_opencode_image
def test_container_opencode_version_matches_pin(tmp_path):
    # The local-arm image (Step 14b): pinned CLI present, no env passthrough.
    sandbox = ContainerSandbox(image=OPENCODE_IMAGE, env_passthrough=())
    argv = sandbox.wrap_argv(["opencode", "--version"], tmp_path)
    assert "-e" not in argv
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    version = OPENCODE_IMAGE.rsplit("-", 1)[-1]
    assert version in proc.stdout


@pytest.mark.slow
@needs_opencode_image
def test_opencode_container_cannot_see_either_repo_but_sees_workspace():
    # The 14a isolation probes, re-run against the local-arm image.
    import shutil

    ws = make_workspace("isolation_test_oc")
    (ws / "probe.txt").write_text("probe")
    hangar_file = resolve_hangar_repo() / "packages/omd/examples/agent_eval/eval_lane_c.py"
    assert hangar_file.exists()
    script = (
        f"cat {hangar_file} 2>/dev/null && echo HANGAR_LEAKED; "
        f"ls {_REPO_ROOT}/results 2>/dev/null && echo RESULTS_LEAKED; "
        f"cat /workspace/probe.txt; "
        f"echo written-from-container > /workspace/out.txt"
    )
    sandbox = ContainerSandbox(image=OPENCODE_IMAGE, env_passthrough=())
    argv = sandbox.wrap_argv(["sh", "-c", script], ws)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        assert "HANGAR_LEAKED" not in proc.stdout
        assert "RESULTS_LEAKED" not in proc.stdout
        assert "probe" in proc.stdout
        assert (ws / "out.txt").read_text().strip() == "written-from-container"
    finally:
        shutil.rmtree(ws, ignore_errors=True)
