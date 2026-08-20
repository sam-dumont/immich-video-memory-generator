"""An Immich error while fetching live photos must not end the run.

`fetch_live_photo_clips` already intends tolerance -- it logs "Failed to fetch
live photos" and returns empty so the memory is built from what did load. But it
caught `(OSError, RuntimeError, ValueError)`, and `ImmichAPIError` inherits from
`Exception` alone, so the one error the call actually raises was the one the
guard could not catch.

In a large library there is always an asset mid-import or just deleted, so a 404
here is a Tuesday rather than an edge case.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.analysis.live_photo_pipeline import fetch_live_photo_clips
from immich_memories.api.immich import ImmichAPIError, ImmichNotFoundError
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange


def _call_with(error: Exception):
    date_range = DateRange(start="2026-01-01", end="2026-01-31")
    # WHY: the Immich server; the point of the test is what it raises.
    with patch(
        "immich_memories.analysis.live_photo_pipeline.search_live_photos", side_effect=error
    ):
        return fetch_live_photo_clips(MagicMock(), date_range, config=Config())


def test_a_missing_asset_costs_the_live_photos_not_the_run():
    assert _call_with(ImmichNotFoundError("asset 404")) == ([], set())


def test_any_immich_api_error_is_tolerated():
    assert _call_with(ImmichAPIError("500 from Immich")) == ([], set())


def test_the_existing_tolerated_errors_still_are():
    assert _call_with(OSError("socket died")) == ([], set())
    assert _call_with(ValueError("bad payload")) == ([], set())
