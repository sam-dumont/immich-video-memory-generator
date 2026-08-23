"""What the trip map intro and its pins are actually given (#498).

The trip path builds its own title from a template and renders its own map.
Both were reading fewer inputs than the pipeline had already computed: the
curated title never reached the most prominent screen in the video, and the
pins were drawn without the names sitting next to the coordinates.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams, _build_title_settings
from immich_memories.processing.assembly_config import AssemblyClip


def _trip_params(**overrides) -> GenerationParams:
    base = {
        "clips": [],
        "output_path": Path("/tmp/trip.mp4"),
        "config": Config(),
        "memory_type": "trip",
        "date_start": date(2025, 7, 1),
        "date_end": date(2025, 7, 14),
        "memory_preset_params": {
            "location_name": "Spain",
            "trip_start": date(2025, 7, 1),
            "trip_end": date(2025, 7, 14),
        },
    }
    base.update(overrides)
    return GenerationParams(**base)


def test_a_curated_title_reaches_the_trip_map_intro() -> None:
    """The map intro is the most prominent screen; it must not show the template.

    Every other memory type honours `title` — the LLM's suggestion, or whatever
    the user typed over it. The trip map read `trip_title_text`, which is built
    from the preset params, so a curated title was computed, stored and then
    ignored on the one screen a viewer is guaranteed to see.
    """
    settings = _build_title_settings(_trip_params(title="Two Weeks in Spain"), Config(), [])

    assert settings is not None
    assert settings.trip_title_text == "Two Weeks in Spain"


def test_without_a_curated_title_the_template_still_wins() -> None:
    settings = _build_title_settings(_trip_params(), Config(), [])

    assert settings is not None
    assert settings.trip_title_text
    assert "SPAIN" in settings.trip_title_text.upper()


def _gps_clip(lat: float, lon: float, name: str | None) -> AssemblyClip:
    return AssemblyClip(
        path=Path("/fake/clip.mp4"), duration=3.0, latitude=lat, longitude=lon, location_name=name
    )


def test_pin_names_line_up_with_the_pins_they_label() -> None:
    """The renderer draws labels by index, so a shifted list mislabels the map.

    Coordinates are de-duplicated on a rounded key, so the names have to be
    de-duplicated by the same rule or the two lists drift apart and every pin
    after the first duplicate is captioned with somebody else's city.
    """
    settings = _build_title_settings(
        _trip_params(),
        Config(),
        [
            _gps_clip(48.8566, 2.3522, "Paris"),
            _gps_clip(48.8566, 2.3522, "Paris"),
            _gps_clip(51.5074, -0.1278, "London"),
        ],
    )

    assert settings is not None
    assert settings.trip_locations is not None
    assert len(settings.trip_location_names) == len(settings.trip_locations)
    assert settings.trip_location_names == ["Paris", "London"]


def test_a_pin_with_no_known_place_still_holds_its_slot() -> None:
    """A missing name must not shorten the list and shift every later label."""
    settings = _build_title_settings(
        _trip_params(),
        Config(),
        [_gps_clip(48.8566, 2.3522, None), _gps_clip(51.5074, -0.1278, "London")],
    )

    assert settings is not None
    assert settings.trip_location_names == ["", "London"]


def test_a_location_card_is_given_the_coordinates_it_is_standing_on() -> None:
    """`render_location_card` draws a satellite map from lat/lon, or a grey box.

    The planner inserts a card precisely because a clip moved more than 30 km,
    so it is holding that clip's coordinates when it asks for the card — and
    was passing only the name, which is how every card ended up as the
    fallback dark gradient.
    """
    from immich_memories.processing.title_divider_planner import TitleDividerPlanner

    generator = MagicMock()
    generator.generate_location_card_screen.return_value = MagicMock(path=Path("/fake/card.mp4"))
    planner = TitleDividerPlanner(generator, MagicMock(month_divider_duration=2.0))

    clips = [
        AssemblyClip(path=Path("/fake/a.mp4"), duration=3.0, latitude=48.85, longitude=2.35),
        AssemblyClip(
            path=Path("/fake/b.mp4"),
            duration=3.0,
            latitude=41.39,
            longitude=2.17,
            location_name="Barcelona",
        ),
    ]
    planner.build_clips_with_location_dividers(clips, None)

    kwargs = generator.generate_location_card_screen.call_args.kwargs
    assert kwargs.get("lat") == 41.39
    assert kwargs.get("lon") == 2.17
