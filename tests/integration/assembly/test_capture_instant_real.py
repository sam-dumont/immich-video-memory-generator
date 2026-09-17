"""The rendered film really carries its capture date, music mix included."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from immich_memories.audio.mixer import DuckingConfig, MixConfig, mix_audio_with_ducking
from immich_memories.processing.streaming_assembler import streaming_assemble_full
from tests.integration.conftest import ffprobe_json, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]

CAPTURED_AT = datetime(2024, 6, 20, 18, 45, tzinfo=timezone(timedelta(hours=2)))


@dataclass
class _Clip:
    """Minimal clip matching the streaming assembler's duck-typed interface."""

    path: Path
    duration: float
    is_title_screen: bool = False
    rotation_override: int | None = None
    is_hdr: bool = False
    color_transfer: str | None = None
    input_seek: float = 0.0


def _container_tags(path: Path) -> dict[str, str]:
    return ffprobe_json(path)["format"].get("tags", {})


def test_rendered_film_is_dated_by_its_last_picture(test_clip_720p, test_music_short, tmp_path):
    """Both container tags survive the render and the music mix that follows it."""
    film = tmp_path / "memory.mp4"
    streaming_assemble_full(
        clips=[_Clip(test_clip_720p, 2.0)],
        transitions=[],
        output_path=film,
        width=1280,
        height=720,
        fps=30,
        captured_at=CAPTURED_AT,
    )

    rendered = _container_tags(film)
    assert rendered["creation_time"] == "2024-06-20T16:45:00Z"
    assert rendered["com.apple.quicktime.creationdate"] == "2024-06-20T18:45:00+02:00"

    with_music = tmp_path / "memory_music.mp4"
    # Short fades: the default 2 s + 3 s would not fit inside a two-second film.
    short_fades = MixConfig(ducking=DuckingConfig(), fade_in_seconds=0.2, fade_out_seconds=0.2)
    mix_audio_with_ducking(film, test_music_short, with_music, short_fades)

    mixed = _container_tags(with_music)
    assert mixed["creation_time"] == rendered["creation_time"]
    assert mixed["com.apple.quicktime.creationdate"] == rendered["com.apple.quicktime.creationdate"]


def test_a_film_with_no_known_capture_instant_is_left_alone(test_clip_720p, tmp_path):
    film = tmp_path / "undated.mp4"
    streaming_assemble_full(
        clips=[_Clip(test_clip_720p, 2.0)],
        transitions=[],
        output_path=film,
        width=1280,
        height=720,
        fps=30,
    )

    assert "com.apple.quicktime.creationdate" not in _container_tags(film)
