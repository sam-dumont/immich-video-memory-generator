"""The part of a day the event actually occupies.

Some days are an event; some days contain one. Getting that wrong in either
direction costs a memory: a window that covers the whole day trims nothing,
and a window a minute wide is not an event at all.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.special_day import ask_if_special, event_window

# Two synthetic places, far enough apart to be different clusters.
_CIRCUIT = (50.31, 4.66)
_HOME = (50.45, 4.90)
_ELSEWHERE = (50.60, 5.20)


def _shot(at: datetime, where: tuple[float, float]) -> SimpleNamespace:
    return SimpleNamespace(
        file_created_at=at,
        exif_info=SimpleNamespace(latitude=where[0], longitude=where[1]),
        people=[],
    )


def _run(
    start: datetime, count: int, every: timedelta, where: tuple[float, float]
) -> list[SimpleNamespace]:
    return [_shot(start + every * n, where) for n in range(count)]


def test_a_day_mostly_taken_up_by_its_event_still_gets_a_window() -> None:
    """The predicate was a ratio, so covering the day well was disqualifying.

    A race photographed from arrival to podium put its cluster across two
    thirds of the day, with one stray picture at home that morning. The ratio
    test read the good coverage as "the day simply happened there" and
    returned nothing, so the memory kept the stray breakfast picture.
    """
    day = [
        _shot(datetime(2021, 6, 12, 8, 24, tzinfo=UTC), _HOME),
        *_run(datetime(2021, 6, 12, 12, 0, tzinfo=UTC), 30, timedelta(minutes=16), _CIRCUIT),
    ]

    window = event_window(day)

    assert window == (
        datetime(2021, 6, 12, 12, 0, tzinfo=UTC),
        datetime(2021, 6, 12, 19, 44, tzinfo=UTC),
    )


def test_a_day_that_was_all_one_thing_has_nothing_to_trim() -> None:
    """A wedding put everything in one place across all the hours it ran."""
    day = _run(datetime(2021, 6, 12, 9, 0, tzinfo=UTC), 36, timedelta(minutes=20), _CIRCUIT)

    assert event_window(day) is None


def test_a_burst_a_minute_wide_is_not_an_event_window() -> None:
    """The trim test had no floor, so a dense burst produced a 69-second window.

    Everything the day was about sat outside it, and the memory it would have
    bounded is a minute long.
    """
    day = [
        _shot(datetime(2021, 6, 12, 8, 0, tzinfo=UTC), _HOME),
        *_run(datetime(2021, 6, 12, 14, 0, tzinfo=UTC), 20, timedelta(seconds=3), _CIRCUIT),
        _shot(datetime(2021, 6, 12, 20, 36, tzinfo=UTC), _ELSEWHERE),
    ]

    assert event_window(day) is None


def test_an_afternoon_inside_a_long_day_is_a_window() -> None:
    """The case the window has always been for, and still is.

    A track day put most of its pictures in one place inside a couple of
    hours; the rest of that day is a cat on a balcony.
    """
    day = [
        _shot(datetime(2021, 6, 12, 8, 0, tzinfo=UTC), _HOME),
        _shot(datetime(2021, 6, 12, 9, 30, tzinfo=UTC), _HOME),
        *_run(datetime(2021, 6, 12, 13, 0, tzinfo=UTC), 24, timedelta(minutes=6), _CIRCUIT),
        _shot(datetime(2021, 6, 12, 18, 36, tzinfo=UTC), _ELSEWHERE),
    ]

    window = event_window(day)

    assert window == (
        datetime(2021, 6, 12, 13, 0, tzinfo=UTC),
        datetime(2021, 6, 12, 15, 18, tzinfo=UTC),
    )


class TestTheModelMaySayWhenTheEventRan:
    """Geometry can only see where the pictures were, not what was happening.

    A race day's coordinates are the same from the moment the car is parked to
    the moment it leaves, so the cluster starts at arrival. The per-picture
    lines carry timestamps and say what is in the frame, so the model can tell
    arrival from the start of the race. Its answer wins; geometry is the
    fallback.
    """

    def _day(self) -> list[SimpleNamespace]:
        return [
            _shot(datetime(2021, 6, 12, 8, 24, tzinfo=UTC), _HOME),
            *_run(datetime(2021, 6, 12, 11, 0, tzinfo=UTC), 30, timedelta(minutes=18), _CIRCUIT),
        ]

    def _answer(self, payload: str) -> object:
        # WHY: the model is the boundary; this pins what we accept back from it.
        with patch("immich_memories.analysis.special_day._ask", return_value=payload):
            return ask_if_special(self._day(), llm_config=SimpleNamespace())

    def test_clock_times_it_read_off_the_lines_become_the_window(self) -> None:
        """The end is rounded past the last picture, which is what a reader does."""
        verdict = self._answer(
            '{"special": true, "title": "A day out", "window": ["12:22", "20:00"]}'
        )

        assert verdict.window == (
            datetime(2021, 6, 12, 12, 22, tzinfo=UTC),
            datetime(2021, 6, 12, 20, 0, tzinfo=UTC),
        )

    def test_a_time_the_day_never_reached_is_not_a_window(self) -> None:
        """A window has to be bounded by the day it claims to describe."""
        verdict = self._answer(
            '{"special": true, "title": "A day out", "window": ["12:22", "23:55"]}'
        )

        assert verdict.window is None

    def test_a_window_a_minute_wide_is_no_better_from_the_model(self) -> None:
        verdict = self._answer(
            '{"special": true, "title": "A day out", "window": ["14:00", "14:01"]}'
        )

        assert verdict.window is None

    def test_anything_that_is_not_a_clock_time_is_no_window(self) -> None:
        verdict = self._answer(
            '{"special": true, "title": "A day out", "window": ["morning", "evening"]}'
        )

        assert verdict.window is None

    def test_a_model_that_says_nothing_about_the_window_leaves_it_open(self) -> None:
        verdict = self._answer('{"special": true, "title": "A day out"}')

        assert verdict.window is None


def test_the_catalogue_takes_the_models_window_over_the_geometric_one(monkeypatch) -> None:
    """Geometry is the fallback for a day the model would not bound."""
    from immich_memories.automation.special_day_scan import scan_year

    day = [
        _shot(datetime(2021, 6, 12, 8, 24, tzinfo=UTC), _HOME),
        *_run(datetime(2021, 6, 12, 11, 0, tzinfo=UTC), 30, timedelta(minutes=18), _CIRCUIT),
    ]
    judged = (
        datetime(2021, 6, 12, 12, 22, tzinfo=UTC),
        datetime(2021, 6, 12, 20, 0, tzinfo=UTC),
    )

    # WHY: ask_if_special is the LLM call; its verdict is the input here.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(
            special=True, title="A day out", subtitle="", what="out", window=judged
        ),
    )

    found = scan_year(day, llm_config=None, home=None, ask=1)

    assert [(d.day, d.window) for d in found] == [(date(2021, 6, 12), judged)]


def _days_due(catalogue: Path, on: str) -> str:
    from click.testing import CliRunner

    from immich_memories.cli import main
    from immich_memories.config_loader import Config

    # WHY: the CLI group loads the user's real config directory on startup.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=Config()),
    ):
        result = CliRunner().invoke(
            main,
            ["days-due", "--on", on, "--catalogue", str(catalogue)],
            catch_exceptions=False,
        )
    return result.output


def test_the_window_survives_the_trip_through_the_catalogue(tmp_path) -> None:
    """The scan wrote a window and days-due reloaded every entry with None.

    Nothing downstream could ever have read it, which is the same as never
    having found it.
    """
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(
        json.dumps(
            [
                {
                    "day": "2015-06-12",
                    "title": "A day out",
                    "subtitle": "",
                    "what": "out",
                    "photos": 133,
                    "window": ["2015-06-12T12:22:00", "2015-06-12T20:00:00"],
                }
            ]
        )
    )

    assert "12:22" in _days_due(catalogue, "2025-06-12")


def test_an_entry_from_before_windows_existed_still_reads(tmp_path) -> None:
    """Catalogues predate this field, and a scan of twenty years is not cheap."""
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(json.dumps([{"day": "2015-06-12", "title": "A day out"}]))

    assert "10 years ago" in _days_due(catalogue, "2025-06-12")
