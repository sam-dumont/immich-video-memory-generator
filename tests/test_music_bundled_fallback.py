"""A failing generator must fall back to the bundled track, not to silence.

Configuring a generator currently makes the failure mode worse than not
configuring one: with none configured `resolve_music` returns a bundled
track, but with ACE-Step enabled and unreachable the exception unwinds past
that branch and the whole music phase is abandoned. The run exits 0 with a
silent video, and under `--quiet` nothing surfaces.

Found by the capability matrix: the ACE-Step row reported ok in 64s — faster
than the bundled-music row — with an empty music/ directory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_music import resolve_music
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _library(tmp_path: Path) -> Path:
    """A bundled library shaped like the shipped one: mood folders holding tracks."""
    folder = tmp_path / "library" / "happy"
    folder.mkdir(parents=True)
    (folder / "track.mp3").write_bytes(b"not really an mp3")
    return tmp_path / "library"


@pytest.fixture
def generator_enabled() -> Config:
    config = Config()
    config.ace_step.enabled = True
    return config


def test_a_failing_generator_falls_back_to_the_bundled_track(
    tmp_path: Path, generator_enabled: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WHY: stands in for the ACE-Step backend — no library installed and no API
    # listening, which is the default state of a machine that just enabled it.
    def unreachable(*args: object, **kwargs: object) -> Path:
        raise ConnectionError("All connection attempts failed")

    monkeypatch.setattr("immich_memories.generate_music.auto_generate_music", unreachable)

    result = resolve_music(
        generator_enabled,
        None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result.path is not None, "a failed generator must not produce silence"
    assert result.path.suffix == ".mp3"


def test_the_bundled_substitution_is_reported_not_swallowed(
    tmp_path: Path, generator_enabled: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently succeeding on bundled music hides a dead backend indefinitely."""

    # WHY: stands in for the ACE-Step backend, unreachable as on a fresh enable.
    def unreachable(*args: object, **kwargs: object) -> Path:
        raise ConnectionError("All connection attempts failed")

    monkeypatch.setattr("immich_memories.generate_music.auto_generate_music", unreachable)

    result = resolve_music(
        generator_enabled,
        None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result.warning is not None
    assert "All connection attempts failed" in result.warning
    assert "bundled" in result.warning


def test_a_bundled_substitution_still_warns_the_finished_run(
    tmp_path: Path, generator_enabled: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Applied music with a warning, not silent success: the artifact carries it."""
    from immich_memories.generate_music import MusicSelection
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    track = _library(tmp_path) / "happy" / "track.mp3"
    warning = "Optional music failed: unreachable; used a bundled track instead"

    # WHY: replaces the resolver's generator call and library read, which need a
    # backend and the installed music package.
    monkeypatch.setattr(
        "immich_memories.generate_music.resolve_music",
        lambda **_kwargs: MusicSelection(track, warning),
    )
    # WHY: replaces the FFmpeg mix and republication of the base artifact.
    monkeypatch.setattr("immich_memories.generate_music.apply_music_file", MagicMock())

    result = _run_music_phase(
        GenerationParams(clips=[], output_path=base_video, config=generator_enabled),
        [],
        base_video,
        tmp_path,
        MagicMock(),
        encoding_plan=_h264_plan(),
    )

    assert result.applied is True
    assert result.warning == warning


def test_an_explicit_missing_track_is_still_a_user_error(
    tmp_path: Path, generator_enabled: Config
) -> None:
    """Substituting bundled music for a typo'd --music path would hide the typo."""
    result = resolve_music(
        generator_enabled,
        tmp_path / "absent.mp3",
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result.path is None


def test_no_music_still_means_no_music(tmp_path: Path, generator_enabled: Config) -> None:
    result = resolve_music(
        generator_enabled,
        None,
        no_music=True,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result.path is None
