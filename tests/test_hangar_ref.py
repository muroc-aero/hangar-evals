"""Tests for the hangar_ref seam.

The reference tests exercise the real the-hangar checkout resolved via
``$HANGAR_REPO``, so they require the-hangar to be importable in the current
interpreter (openmdao etc.). Run from a venv that has both hangar-evals and
the-hangar installed:

    HANGAR_REPO=../the-hangar pytest tests/
"""

from __future__ import annotations

import pytest

from hangar.evals.hangar_ref import (
    EXAMPLES_SUBDIR,
    lane_a_reference,
    resolve_hangar_repo,
)


def test_resolve_hangar_repo_points_at_the_hangar():
    repo = resolve_hangar_repo()
    assert (repo / EXAMPLES_SUBDIR).is_dir()


def test_paraboloid_analysis_reference_is_39():
    ref = lane_a_reference("paraboloid", "analysis")
    assert ref["f_xy"] == 39.0


def test_bad_hangar_repo_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HANGAR_REPO", str(tmp_path / "does-not-exist"))
    with pytest.raises(FileNotFoundError, match="the-hangar not found"):
        resolve_hangar_repo()


def test_hangar_repo_without_examples_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("HANGAR_REPO", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="does not look like the-hangar"):
        resolve_hangar_repo()


def test_shared_constants_reads_example_shared_module():
    from hangar.evals.hangar_ref import shared_constants

    sh = shared_constants("paraboloid", ("ANALYSIS_X", "ANALYSIS_Y"))
    assert sh == {"ANALYSIS_X": 1.0, "ANALYSIS_Y": 2.0}


def test_shared_constants_unknown_name_raises_with_stderr():
    from hangar.evals.hangar_ref import shared_constants

    with pytest.raises(RuntimeError, match="NOPE"):
        shared_constants("paraboloid", ("NOPE",))


# --- reference caching (Step 18) -----------------------------------------------


import types  # noqa: E402

from hangar.evals import hangar_ref  # noqa: E402

_CLEAN = ("a" * 40, False)
_DIRTY = ("a" * 40, True)


def _fake_compute(monkeypatch, payload: dict, calls: list):
    """Stub the Lane-A subprocess: records each call, prints ``payload``."""
    import json as _json

    def fake_run(argv, capture_output, text, cwd):
        calls.append(argv)
        return types.SimpleNamespace(
            returncode=0, stdout=_json.dumps(payload) + "\n", stderr="")

    monkeypatch.setattr(hangar_ref.subprocess, "run", fake_run)


@pytest.fixture()
def fresh_memo(monkeypatch):
    monkeypatch.setattr(hangar_ref, "_MEMO", {})


def test_memo_avoids_recompute_within_process(monkeypatch, fresh_memo):
    calls: list = []
    _fake_compute(monkeypatch, {"f_xy": 39.0}, calls)
    monkeypatch.setattr(hangar_ref, "_repo_state", lambda repo: _DIRTY)

    first = hangar_ref.lane_a_reference("fakeex", "mod")
    second = hangar_ref.lane_a_reference("fakeex", "mod")
    assert first == second == {"f_xy": 39.0}
    assert len(calls) == 1   # N seeds of one run never recompute


def test_disk_cache_round_trips_across_processes(monkeypatch, fresh_memo, tmp_path):
    calls: list = []
    _fake_compute(monkeypatch, {"f_xy": 39.0}, calls)
    monkeypatch.setattr(hangar_ref, "_repo_state", lambda repo: _CLEAN)

    ref = hangar_ref.lane_a_reference("fakeex", "mod", cache_dir=tmp_path)
    assert ref == {"f_xy": 39.0} and len(calls) == 1
    cache_file = tmp_path / f"fakeex.mod.{'a' * 12}.json"
    assert cache_file.is_file()

    # "New process": memo cleared, compute broken — the disk cache must serve.
    monkeypatch.setattr(hangar_ref, "_MEMO", {})
    monkeypatch.setattr(
        hangar_ref.subprocess, "run",
        lambda *a, **k: pytest.fail("cache hit must not spawn a subprocess"))
    again = hangar_ref.lane_a_reference("fakeex", "mod", cache_dir=tmp_path)
    assert again == {"f_xy": 39.0}


def test_dirty_checkout_bypasses_disk_cache(monkeypatch, fresh_memo, tmp_path):
    # A dirty working tree has no trustworthy SHA key: nothing is read or
    # written, every "process" recomputes.
    calls: list = []
    _fake_compute(monkeypatch, {"f_xy": 1.0}, calls)
    monkeypatch.setattr(hangar_ref, "_repo_state", lambda repo: _DIRTY)

    hangar_ref.lane_a_reference("fakeex", "mod", cache_dir=tmp_path)
    monkeypatch.setattr(hangar_ref, "_MEMO", {})
    hangar_ref.lane_a_reference("fakeex", "mod", cache_dir=tmp_path)
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_new_sha_misses_old_cache_entry(monkeypatch, fresh_memo, tmp_path):
    calls: list = []
    _fake_compute(monkeypatch, {"f_xy": 2.0}, calls)
    monkeypatch.setattr(hangar_ref, "_repo_state", lambda repo: _CLEAN)
    hangar_ref.lane_a_reference("fakeex", "mod", cache_dir=tmp_path)

    # the-hangar moves to a new commit -> old entry must not be served.
    monkeypatch.setattr(hangar_ref, "_MEMO", {})
    monkeypatch.setattr(hangar_ref, "_repo_state", lambda repo: ("b" * 40, False))
    hangar_ref.lane_a_reference("fakeex", "mod", cache_dir=tmp_path)
    assert len(calls) == 2
    assert {p.name for p in tmp_path.iterdir()} == {
        f"fakeex.mod.{'a' * 12}.json", f"fakeex.mod.{'b' * 12}.json"}


def test_repo_state_reads_this_checkout():
    # Real git, real repo: a 40-char SHA and a boolean dirty flag.
    state = hangar_ref._repo_state(hangar_ref._REPO_ROOT)
    assert state is not None
    sha, dirty = state
    assert len(sha) == 40 and isinstance(dirty, bool)
