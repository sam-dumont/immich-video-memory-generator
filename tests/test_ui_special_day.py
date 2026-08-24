"""The wizard can reach a day the catalogue found.

Fixture days are invented. A real catalogue names real people and places, and
none of that belongs in a test file.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from immich_memories.automation.special_day_scan import DiscoveredDay
from immich_memories.memory_types.registry import MemoryType
from immich_memories.ui.pages import step1_presets
from immich_memories.ui.pages.step1_presets import _PRESET_CARDS
from immich_memories.ui.state import AppState

BRUSSELS = timezone(timedelta(hours=2))

_A_LONG_EVENING = {
    "day": "2016-06-12",
    "title": "A long evening out",
    "subtitle": "The one that ran past midnight",
    "what": "a street party",
    "photos": 289,
    "window": ["2016-06-12T18:30:00+02:00", "2016-06-13T01:15:00+02:00"],
    "active_hours": 9,
}


def _catalogue(home: Path, *entries: dict) -> None:
    """Write a catalogue where `discover-days` would have left one."""
    folder = home / ".immich-memories"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "special-days.json").write_text(json.dumps(list(entries)))


def _render_card(monkeypatch, home: Path, pick: int | None = None) -> tuple[AppState, MagicMock]:
    """Drive the card the way a browser does: mount it, then choose a row.

    The catalogue is read from the user's home, so pointing HOME at a tmp
    directory is enough to hand the card a library's worth of days -- or none.
    """
    monkeypatch.setenv("HOME", str(home))
    state = AppState(memory_type=MemoryType.SPECIAL_DAY)
    ui_stub = MagicMock()
    # WHY: the wizard reads its state through a per-session accessor.
    with (
        patch.object(step1_presets, "get_app_state", return_value=state),
        # WHY: NiceGUI widgets need a live client slot; the card's catalogue
        # reading and option building do not. These two stand in for the whole
        # widget layer, and recording what was drawn is how the test reads it.
        patch.object(step1_presets, "ui", ui_stub),
        patch.object(step1_presets, "im_card", MagicMock()),
    ):
        step1_presets._render_params(MemoryType.SPECIAL_DAY)
        ui_stub.timer.call_args.args[1]()
        if pick is not None:
            ui_stub.select.call_args.kwargs["on_change"](SimpleNamespace(value=pick))
    return state, ui_stub


def _discovered(day: str, title: str) -> DiscoveredDay:
    return DiscoveredDay(
        day=date.fromisoformat(day),
        title=title,
        subtitle="",
        what="",
        photos=44,
        window=None,
    )


def _drawn_text(ui_stub: MagicMock) -> str:
    return " ".join(str(call) for call in ui_stub.label.call_args_list)


def test_the_catalogue_is_offered_as_a_card() -> None:
    keys = [card[0] for card in _PRESET_CARDS]

    assert MemoryType.SPECIAL_DAY in keys


def test_choosing_a_day_scopes_the_wizard_to_the_hours_it_happened_in(
    monkeypatch, tmp_path
) -> None:
    """The window is the scope, offsets and all.

    Trimming to the hours the day was awake is the whole point of recording a
    window; a memory scoped to midnight-to-midnight would put the cat on the
    balcony that morning beside the fireworks.
    """
    _catalogue(tmp_path, _A_LONG_EVENING)

    state, _ = _render_card(monkeypatch, tmp_path, pick=0)

    assert [(r.start, r.end) for r in state.date_ranges] == [
        (
            datetime(2016, 6, 12, 18, 30, tzinfo=BRUSSELS),
            datetime(2016, 6, 13, 1, 15, tzinfo=BRUSSELS),
        )
    ]
    assert state.scope_is_selected
    assert state.memory_preset_params["day"] == date(2016, 6, 12)
    assert state.memory_preset_params["photos"] == 289
    assert state.memory_preset_params["active_hours"] == 9


def test_choosing_a_day_carries_its_own_name_to_the_title(monkeypatch, tmp_path) -> None:
    """The catalogue already named this day, from the day's own photos."""
    _catalogue(tmp_path, _A_LONG_EVENING)

    state, _ = _render_card(monkeypatch, tmp_path, pick=0)

    assert state.title_suggestion_title == "A long evening out"
    assert state.title_suggestion_subtitle == "The one that ran past midnight"


def test_with_no_catalogue_the_card_asks_for_one_instead_of_picking_a_day(
    monkeypatch, tmp_path
) -> None:
    """Refuse over fake.

    A library nobody has scanned has no special days, and offering a random
    one would be the tool inventing an occasion. Step 1 stays incomplete and
    the card says which file was missing and what builds it.
    """
    state, ui_stub = _render_card(monkeypatch, tmp_path)

    assert not state.scope_is_selected
    assert not state.memory_preset_params
    ui_stub.select.assert_not_called()
    drawn = _drawn_text(ui_stub)
    assert "discover-days" in drawn
    assert str(tmp_path / ".immich-memories" / "special-days.json") in drawn


def test_the_title_is_the_day_the_catalogue_named_not_its_span() -> None:
    """One occasion, one name.

    The generic fallback describes a span, and a special day's span is a few
    hours: it comes out as "June - June 2016", which names nothing that
    happened.
    """
    from immich_memories.ui.pages.pipeline_title import generate_template_title

    title, subtitle = generate_template_title(
        memory_type="special_day",
        start_date="2016-06-12",
        end_date="2016-06-13",
        preset_params={
            "title": "A long evening out",
            "subtitle": "The one that ran past midnight",
        },
    )

    assert title == "A long evening out"
    assert subtitle == "The one that ran past midnight"


async def test_the_title_llm_is_never_asked_to_rename_the_day() -> None:
    """The catalogue's name outranks a second opinion.

    It was written by a model that looked at this day's own photos, months
    before anybody asked for a video of it. The title LLM sees a handful of
    clip descriptions and would rename the occasion from them.
    """
    from immich_memories.timeperiod import DateRange
    from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

    state = AppState(
        memory_type=MemoryType.SPECIAL_DAY,
        memory_preset_params={"day": date(2016, 6, 12), "title": "A long evening out"},
    )
    state.date_ranges = [DateRange(start=datetime(2016, 6, 12, 18, 30), end=datetime(2016, 6, 13))]
    # WHY: Config is a wide tree of pydantic sections and this path reads three
    # leaves of it; a configured title model is what makes the LLM call live.
    state.config = MagicMock(title_llm=None, **{"llm.model": "omlx"})

    # WHY: the title LLM is a network call to a model server.
    with patch(
        "immich_memories.ui.pages.pipeline_title.generate_title_with_llm", new_callable=AsyncMock
    ) as llm:
        await generate_title_after_pipeline(state)

    llm.assert_not_called()
    assert state.title_suggestion_title == "A long evening out"


def test_the_file_is_named_after_the_day_and_nobody_in_it() -> None:
    """The date is the only part of a special day that is safe in a filename.

    The generic slug rounds a single day up to its month, so a special day in
    June 2016 and that month's Monthly Highlights would write the same file.
    The catalogue's own title stays out: it names real people and places, and
    a filename travels further than a title card.
    """
    from immich_memories.filename_builder import build_output_filename

    name = build_output_filename(
        memory_type="special_day",
        preset_params={"day": date(2016, 6, 12), "title": "A long evening out"},
        person_name=None,
        date_start=date(2016, 6, 12),
        date_end=date(2016, 6, 13),
    )

    assert name == "everyone_2016-06-12_memories.mp4"
    assert "evening" not in name


def test_one_occasion_gets_one_title_card_and_no_dividers() -> None:
    """A day is not a span to divide.

    Nothing special-cases this -- a one-day range is already too short for
    dividers -- so the pin is here to catch the day somebody widens the rule.
    """
    from immich_memories.filename_builder import get_divider_mode

    assert get_divider_mode("special_day", date(2016, 6, 12), date(2016, 6, 13)) == "none"


def test_the_picker_puts_anniversaries_first_and_still_offers_the_rest() -> None:
    """The wizard triggers differently from automation, on purpose.

    A scheduled run only proposes a day on its anniversary -- it arrives
    unannounced, and the round number is what earns the interruption. Somebody
    who clicked Surprise me wants a memory now, so the whole catalogue is
    offered, due days first.
    """
    a_decade_ago = _discovered("2016-06-12", "A long evening out")
    nowhere_near_today = _discovered("2019-02-02", "Somebody's leap day")

    rows = step1_presets._special_day_options([nowhere_near_today, a_decade_ago], date(2026, 6, 12))

    assert [label for _, label in rows] == [
        "10 years ago — A long evening out",
        "2019-02-02 — Somebody's leap day",
    ]


def test_a_day_the_model_could_not_name_is_not_offered() -> None:
    """Refuse over fake, one row at a time.

    A day with no title and no description has nothing truthful to put on a
    title card, so the preset refuses it. Listing it would be offering a row
    that clears itself when clicked.
    """
    rows = step1_presets._special_day_options(
        [_discovered("2016-06-12", ""), _discovered("2019-02-02", "Somebody's leap day")],
        date(2026, 6, 12),
    )

    assert [entry.day for entry, _ in rows] == [date(2019, 2, 2)]


def test_a_blank_title_falls_back_to_what_the_day_was() -> None:
    """The picker and the preset have to agree on which days are nameable.

    `create_preset` falls back from an empty title to `what`; a picker that
    did not would hide a day the factory would happily have built.
    """
    blank_titled = DiscoveredDay(
        day=date(2019, 2, 2),
        title="   ",
        subtitle="",
        what="A very long walk",
        photos=44,
        window=None,
    )

    rows = step1_presets._special_day_options([blank_titled], date(2026, 6, 12))

    assert [label for _, label in rows] == ["2019-02-02 — A very long walk"]
