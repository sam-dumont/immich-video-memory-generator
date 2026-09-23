"""Who names a memory on the CLI, and with what.

A film about people or an occasion is named by the model as soon as a reader is
configured: a date span with three full names under it describes no film. Trips
keep their own prompt and still wait to be asked, and `--no-llm-title` pins the
template for a matrix run that has to stay comparable across months.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from immich_memories.api.album_service import AlbumService, FilmScope
from immich_memories.cli._llm_title import resolve_cli_title
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_clip

_RANGE = DateRange(start=datetime(2025, 7, 1), end=datetime(2025, 7, 14))


def _config_with_llm() -> Config:
    config = Config()
    config.llm.model = "some-model"
    return config


def _answers(title: str = "Ada and her grandparents", subtitle: str | None = None):
    """Stand in for the reader. WHY: the LLM call is the only boundary here."""
    seen: dict = {}

    def ask(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(title=title, subtitle=subtitle)

    return ask, seen


def test_a_people_memory_is_named_by_the_model_with_no_flag_at_all() -> None:
    """A reader is configured, so the family record beats the name list."""
    ask, seen = _answers()

    title, subtitle = resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example", "Grace Example"],
        ask=ask,
    )

    assert (title, subtitle) == ("Ada and her grandparents", None)
    assert seen["person_names"] == ["Ada Example", "Grace Example"]


def test_no_llm_title_pins_the_template() -> None:
    """The contact-sheet matrix needs runs before and after to stay comparable."""
    called = []

    title, subtitle = resolve_cli_title(
        enabled=False,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example"],
        ask=lambda **kwargs: called.append(kwargs),
    )

    assert (title, subtitle) == (None, None)
    assert called == []


def test_a_trip_still_waits_to_be_asked() -> None:
    """Trips keep the prompt they have; this PR does not change what names them."""
    called = []

    title, _subtitle = resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="trip",
        date_range=_RANGE,
        person_names=[],
        ask=lambda **kwargs: called.append(kwargs),
    )

    assert title is None
    assert called == []


def test_the_flag_forces_the_model_onto_a_trip() -> None:
    ask, seen = _answers(title="Under the sandstone cliffs")

    title, _subtitle = resolve_cli_title(
        enabled=True,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="trip",
        date_range=_RANGE,
        person_names=[],
        ask=ask,
    )

    assert title == "Under the sandstone cliffs"
    assert seen["memory_type"] == "trip"


def test_an_explicit_title_outranks_the_model() -> None:
    """--title is the user typing the answer; nothing should overrule it."""
    called = []

    title, subtitle = resolve_cli_title(
        enabled=True,
        title_override="Our Summer",
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="year_in_review",
        date_range=_RANGE,
        person_names=[],
        ask=lambda **kwargs: called.append(kwargs),
    )

    assert title == "Our Summer"
    assert called == []


def test_the_grouped_condition_travels_with_the_names() -> None:
    """Either grandparent AND the child is a shape, not a list of three names."""
    ask, seen = _answers()

    resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example", "Grace Example"],
        memory_preset_params={
            "person_expression": {
                "all": [
                    {"person": "Ada Example"},
                    {"person": "Grace Example"},
                ]
            }
        },
        ask=ask,
    )

    assert seen["facts"].people_condition == '("Ada Example" AND "Grace Example")'


def test_a_catalogued_day_hands_the_model_what_the_catalogue_saw() -> None:
    ask, seen = _answers(title="A day at the bowling alley")

    resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="special_day",
        date_range=_RANGE,
        person_names=[],
        memory_preset_params={"what": "an afternoon at a themed bowling alley"},
        ask=ask,
    )

    assert seen["facts"].occasion_name == "an afternoon at a themed bowling alley"


def test_the_album_the_cut_sits_in_reaches_the_facts() -> None:
    """WHY the lambda: it stands in for the Immich album read, the only boundary."""
    ask, seen = _answers()

    resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example"],
        album_lookup=lambda: "Sunday at the lake",
        ask=ask,
    )

    assert seen["facts"].album_name == "Sunday at the lake"


def test_a_year_film_in_the_phone_catch_all_gets_its_normal_title_facts() -> None:
    """121 of 127 pictures in a library-scale catch-all: the model hears no album name."""
    cut = [f"p{n:03d}" for n in range(127)]
    everything = {
        "id": "album-everything",
        "albumName": "Everything",
        "assetCount": 38_000,
        "startDate": "2014-02-01T09:00:00.000Z",
        "endDate": "2026-09-20T18:00:00.000Z",
    }

    async def request(method: str, endpoint: str, **kwargs):
        # WHY: stands in for Immich's `GET /albums?assetId=` read, the only boundary.
        return [everything] if kwargs["params"]["assetId"] in cut[:121] else []

    async def version():  # pragma: no cover - never reached
        raise AssertionError

    service = AlbumService(request, version)
    year = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59))
    scope = FilmScope(start=year.start, end=year.end, pool=3500)
    ask, seen = _answers(title="2024")

    title, _subtitle = resolve_cli_title(
        enabled=True,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="year",
        date_range=year,
        person_names=[],
        album_lookup=lambda: asyncio.run(service.album_holding_most(cut, scope=scope)),
        ask=ask,
    )

    assert seen["facts"].album_name is None
    assert title == "2024"


def test_an_album_memory_never_pays_for_the_lookup() -> None:
    """Its own name is already known; one request per asset is not free."""
    asked = []

    resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="album",
        date_range=_RANGE,
        person_names=[],
        memory_preset_params={"album_name": "Old Negatives 75"},
        album_lookup=lambda: asked.append("asked") or "Something Else",
        ask=_answers()[0],
    )

    assert asked == []


def test_an_unanswerable_album_lookup_leaves_the_rest_of_the_facts_alone() -> None:
    ask, seen = _answers()

    def explode() -> str:
        raise RuntimeError("Immich is down")

    resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example"],
        album_lookup=explode,
        ask=ask,
    )

    assert seen["facts"].album_name is None
    assert seen["person_names"] == ["Ada Example"]


def test_the_model_answering_without_a_subtitle_leaves_no_subtitle_line() -> None:
    """Null beats a guess: the name list must not come back as a consolation."""
    ask, _seen = _answers(subtitle=None)

    title, subtitle = resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example", "Grace Example"],
        ask=ask,
    )

    assert (title, subtitle) == ("Ada and her grandparents", None)


def test_the_flag_carries_the_clip_descriptions_into_the_ask() -> None:
    """The analyzer already described every selected clip; a trip prompt gets them."""
    ask, seen = _answers(title="A Fortnight in July", subtitle="2025")

    clip = make_clip("clip-1")
    clip.llm_description = "children running through a sprinkler"

    title, subtitle = resolve_cli_title(
        enabled=True,
        title_override=None,
        clips=[clip],
        config=_config_with_llm(),
        memory_type="trip",
        date_range=_RANGE,
        person_names=["Ada Example"],
        ask=ask,
    )

    assert (title, subtitle) == ("A Fortnight in July", "2025")
    assert seen["clip_descriptions"] == ["children running through a sprinkler"]
    assert seen["person_names"] == ["Ada Example"]
    assert seen["duration_days"] == 13


def test_a_missing_reader_leaves_the_template_alone() -> None:
    """A people memory without a model configured must not fail the run."""
    title, subtitle = resolve_cli_title(
        enabled=None,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=Config(),
        memory_type="multi_person",
        date_range=_RANGE,
        person_names=["Ada Example"],
        ask=lambda **_kwargs: None,
    )

    assert (title, subtitle) == (None, None)


def test_a_plan_that_stops_before_rendering_still_shows_the_title(capsys) -> None:
    """--no-render answers "what will this film be called" without rendering it."""
    from pathlib import Path

    from immich_memories.cli._generation_preview import GenerationPreview, print_generation_preview
    from immich_memories.processing.output_canvas import OutputCanvas
    from immich_memories.processing.timeline_budget import TimelinePlan

    print_generation_preview(
        GenerationPreview(
            memory_type="special_day",
            date_range="Mar 27",
            video_candidates=1,
            live_photo_candidates=0,
            photo_candidates=9,
            selected_videos=1,
            selected_photos=9,
            selected_duration=40.0,
            timeline=TimelinePlan(
                target_duration=60.0,
                content_budget=50.0,
                title_budget=9.5,
                title_duration=3.5,
                ending_duration=4.0,
                divider_duration=2.0,
                max_dividers=1,
                transition_budget=4.0,
            ),
            canvas=OutputCanvas(width=1280, height=720, orientation="landscape"),
            output_path=Path("/day.mp4"),
            upload_intent=False,
            music_policy="disabled",
            title="Lakeside Half 2022",
            subtitle="Ten kilometres of rain",
        )
    )

    printed = capsys.readouterr().out
    assert "Lakeside Half 2022" in printed
    assert "Ten kilometres of rain" in printed
