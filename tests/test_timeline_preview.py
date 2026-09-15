"""Saved and live previews place the same cards as assembly."""

from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip, TitleScreenSettings
from immich_memories.processing.timeline_budget import TimelinePlan
from immich_memories.processing.timeline_preview import preview_timeline


def test_year_cards_and_cuts_preserve_full_video_holds():
    clips = [
        AssemblyClip(Path(), 4, date=f"{year}-01-01", asset_id=str(year)) for year in (2023, 2024)
    ]
    plan = TimelinePlan(30, 20, 10, 3, 5, 2, 1)
    starts, seconds = preview_timeline(
        clips, plan, TitleScreenSettings(divider_mode="year"), "cut", 0.5
    )
    assert starts == {"2023": (3, 4), "2024": (9, 4)}
    assert seconds == 18


def test_trip_map_and_location_card_use_real_source_places():
    clips = [
        AssemblyClip(Path(), 4, asset_id="a", latitude=0, longitude=0, location_name="A"),
        AssemblyClip(Path(), 4, asset_id="b", latitude=1, longitude=0, location_name="B"),
    ]
    plan = TimelinePlan(30, 20, 10, 3, 5, 2, 1)
    starts, seconds = preview_timeline(
        clips, plan, TitleScreenSettings(memory_type="trip"), "crossfade", 0.5
    )
    assert starts == {"a": (2.5, 4), "b": (7.5, 4)}
    assert seconds == 16.5
