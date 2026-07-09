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


@pytest.mark.requires_hangar
def test_resolve_hangar_repo_points_at_the_hangar():
    repo = resolve_hangar_repo()
    assert (repo / EXAMPLES_SUBDIR).is_dir()


@pytest.mark.requires_hangar
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
