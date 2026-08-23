"""Scanning a library's years for the days worth resurfacing.

The scan decides what to skip before it decides what to keep, so a wrong
answer about home is not a smaller catalogue — it is an empty one, reported
as a success.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from immich_memories.automation.special_day_scan import scan_year
from immich_memories.cli.special_days_cmd import _homebase
from immich_memories.config_models import TripsConfig


def _asset(hour: int, minute: int = 0, day: int = 13) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"a-{day:02d}-{hour:02d}-{minute:02d}",
        file_created_at=datetime(2021, 4, day, hour, minute, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def _a_full_day() -> list[SimpleNamespace]:
    return [_asset(h, m) for h in range(9, 17) for m in (0, 20, 40)]


def test_the_configured_homebase_is_the_one_the_scan_uses() -> None:
    """The scan read two fields that do not exist, so home was a constant.

    Anyone living away from that constant had their at-home days marked as
    trips, and the year was swallowed before a day was ever considered.
    """
    config = SimpleNamespace(trips=TripsConfig(homebase_latitude=12.5, homebase_longitude=-3.25))

    assert _homebase(config) == (12.5, -3.25)


def test_an_unset_homebase_is_not_replaced_by_a_guess() -> None:
    """Null Island is this codebase's way of saying nobody set one."""
    assert _homebase(SimpleNamespace(trips=TripsConfig())) is None


def test_without_a_homebase_the_scan_excludes_no_days(monkeypatch) -> None:
    """Nothing to be away from means nothing to skip.

    Guessing a home is worse than having none: every day at the real home
    reads as away, and the catalogue comes back empty.
    """
    # WHY: ask_if_special is the LLM call; the verdict is not what this tests.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(special=True, title="A day", subtitle="", what="out"),
    )
    # WHY: detect_trips reverse-geocodes over the network, and without a home
    # there is nothing for it to measure against — it must not run at all.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.detect_trips",
        lambda *_a, **_k: pytest.fail("trip detection ran without a homebase"),
    )

    found = scan_year(_a_full_day(), llm_config=None, home=None, ask=1)

    assert [d.day for d in found] == [date(2021, 4, 13)]
