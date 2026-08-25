"""An Immich error while fetching photographs must not end the run.

Live Photos used to be fetched by their own tolerant wrapper, which logged and
returned empty so the memory was built from whatever did load. Their stills
come back with the photographs now, so the tolerance has to live where the
fetch does -- otherwise moving them into one pool quietly costs them the guard.

In a large library there is always an asset mid-import or just deleted, so a
404 here is a Tuesday rather than an edge case.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from immich_memories.api.immich import ImmichAPIError, ImmichNotFoundError
from immich_memories.cli._asset_fetch import fetch_photos
from immich_memories.timeperiod import DateRange


def _call_with(error: Exception):
    date_range = DateRange(start="2026-01-01", end="2026-01-31")
    # WHY: the Immich server; the point of the test is what it raises.
    client = MagicMock()
    client.get_photos_for_date_range.side_effect = error
    return fetch_photos(client=client, date_ranges=[date_range], person_ids=[])


def test_a_missing_asset_costs_its_window_not_the_run():
    assert _call_with(ImmichNotFoundError("asset 404")) == []


def test_any_immich_api_error_is_tolerated():
    assert _call_with(ImmichAPIError("500 from Immich")) == []


def test_the_existing_tolerated_errors_still_are():
    assert _call_with(OSError("socket died")) == []
    assert _call_with(ValueError("bad payload")) == []
