"""Shared test scaffolding: the ``requires_hangar`` marker.

Tests marked ``requires_hangar`` exercise a real the-hangar checkout through
the ``$HANGAR_REPO`` seam (Lane A references via ``compute_refs`` /
``lane_a_reference``). When no checkout resolves — e.g. bare CI — they SKIP
with a pointer instead of failing on ``FileNotFoundError``, keeping the
hangar-independent suite runnable anywhere.
"""

from __future__ import annotations

import pytest

from hangar.evals.hangar_ref import resolve_hangar_repo


def _hangar_available() -> bool:
    try:
        resolve_hangar_repo()
    except FileNotFoundError:
        return False
    return True


def pytest_collection_modifyitems(config, items):
    if _hangar_available():
        return
    skip = pytest.mark.skip(
        reason="no the-hangar checkout (set HANGAR_REPO to run the seam tests)"
    )
    for item in items:
        if "requires_hangar" in item.keywords:
            item.add_marker(skip)
