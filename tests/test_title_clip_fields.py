"""Trimming a clip for a title must not quietly discard the rest of it.

AssemblyClip carries sixteen fields. Both title paths rebuilt it by hand with
eight, so turning on content-backed titles dropped a user-set rotation, the
music-ducking flag, and the place caption on the closing clip.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.title_inserter import TitleInserter


def _rich_clip() -> AssemblyClip:
    return AssemblyClip(
        path=Path("/tmp/clip.mp4"),
        duration=10.0,
        date="2024-02-07",
        asset_id="a1",
        rotation_override=90,
        llm_emotion="excited",
        latitude=50.8,
        longitude=4.3,
        location_name="Jette",
        has_speech=True,
        outgoing_transition="fade",
        is_photo=True,
        has_music=True,
    )


def test_trimming_the_first_clip_keeps_everything_it_did_not_change() -> None:
    clips = [_rich_clip()]

    TitleInserter._trim_first_clip(clips, 2.0)

    trimmed = clips[0]
    assert trimmed.duration == 8.0, "the trim itself still applies"
    assert trimmed.input_seek == 2.0
    untouched = {f.name for f in dataclasses.fields(AssemblyClip)} - {"duration", "input_seek"}
    original = _rich_clip()
    for name in untouched:
        assert getattr(trimmed, name) == getattr(original, name), name
