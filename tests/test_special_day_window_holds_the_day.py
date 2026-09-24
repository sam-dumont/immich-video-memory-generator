"""A window has to hold the day it is a window on.

Measured on one real catalogue: a long occasion over 21 active hours and 379 pictures
carried a five-hour window holding 24 of them, so the film was cut from 6 % of
the day and refused for insufficient material. A twelve-hour day of 114
pictures carried a forty-minute window.

The discriminant is not how much clock time a window spans. The track day
#670 was built for spends 2.3 hours of a 10.6-hour day in one place, and that
window still holds about 92 % of the day's pictures. What separates the two is
the share of the day inside the window, and only the geometric route ever
tested it: the clock times the model writes went in unchecked.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.special_day import SpecialDay
from immich_memories.automation.catalogue import record_for, scope_window
from immich_memories.automation.special_day_scan import DiscoveredDay, scan_year


def _shot(at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        id=at.isoformat(),
        file_created_at=at,
        exif_info=SimpleNamespace(
            city="Someplace", country="Belgium", latitude=None, longitude=None
        ),
        people=[],
        llm_description="people in a room",
    )


def _a_long_day() -> list[SimpleNamespace]:
    """Twenty-one hours of pictures, every twenty minutes."""
    start = datetime(2024, 2, 7, 1, 0, tzinfo=UTC)
    return [_shot(start + timedelta(minutes=20) * n) for n in range(63)]


def _scanned(monkeypatch, day: list, window: tuple[datetime, datetime] | None) -> DiscoveredDay:
    # WHY: ask_if_special is the live model call; the window it returns is the input here.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SpecialDay(
            special=True, title="A long night", subtitle="", what="a long occasion", window=window
        ),
    )
    found = scan_year(day, llm_config=None, home=None)
    assert len(found) == 1
    return found[0]


def test_a_window_holding_a_sliver_of_the_day_is_not_the_day(monkeypatch) -> None:
    """The exact shape reported: five hours of a twenty-one-hour day."""
    day = _a_long_day()
    sliver = (datetime(2024, 2, 7, 1, 53, tzinfo=UTC), datetime(2024, 2, 7, 6, 59, tzinfo=UTC))

    entry = _scanned(monkeypatch, day, sliver)

    assert entry.window is None, "a window this narrow hides the day it is about"
    assert entry.window_photos == entry.photos, "with no window the whole day is the scope"


def test_a_window_that_holds_the_day_is_kept(monkeypatch) -> None:
    """The track day: narrow in clock time, nearly the whole day in pictures."""
    day = _a_long_day()
    most = (day[3].file_created_at, day[-2].file_created_at)

    entry = _scanned(monkeypatch, day, most)

    assert entry.window == most
    assert entry.window_photos == 59


def test_the_row_says_how_much_of_the_day_its_window_holds(monkeypatch) -> None:
    """A reader has to be able to see why a window was kept without re-fetching the day."""
    day = _a_long_day()
    most = (day[3].file_created_at, day[-2].file_created_at)

    row = record_for(_scanned(monkeypatch, day, most))

    assert (row["window_photos"], row["photos"]) == (59, 63)


@pytest.mark.parametrize(
    ("window_photos", "kept"),
    [(24, False), (200, True)],
)
def test_a_row_written_before_the_rule_keeps_its_window_until_it_is_rebuilt(
    window_photos: int, kept: bool
) -> None:
    """The count is what the rule reads, and rows without one are not second-guessed."""
    window = (datetime(2024, 2, 7, 1, 53, tzinfo=UTC), datetime(2024, 2, 7, 6, 59, tzinfo=UTC))
    stamped = DiscoveredDay(
        day=date(2024, 2, 7),
        title="A long night",
        subtitle="",
        what="a long occasion",
        photos=379,
        window=window,
        window_photos=window_photos,
    )
    unstamped = DiscoveredDay(
        day=date(2024, 2, 7),
        title="A long night",
        subtitle="",
        what="a long occasion",
        photos=379,
        window=window,
    )

    assert (scope_window(stamped) == window) is kept
    assert scope_window(unstamped) == window


# Which runs the sequence reader names is not these tests' subject (#1093).
pytestmark = pytest.mark.usefixtures("every_run_an_occasion")
