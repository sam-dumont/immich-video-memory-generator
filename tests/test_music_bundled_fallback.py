"""A failing generator must fall back to the bundled track, not to silence.

Configuring a generator currently makes the failure mode worse than not
configuring one: with none configured `resolve_music_file` returns a bundled
track, but with ACE-Step enabled and unreachable the exception unwinds past
that branch and the whole music phase is abandoned. The run exits 0 with a
silent video, and under `--quiet` nothing surfaces.

Found by the capability matrix: the ACE-Step row reported ok in 64s — faster
than the bundled-music row — with an empty music/ directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate_music import resolve_music_file


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

    result = resolve_music_file(
        generator_enabled,
        None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result is not None, "a failed generator must not produce silence"
    assert result.suffix == ".mp3"


def test_an_explicit_missing_track_is_still_a_user_error(
    tmp_path: Path, generator_enabled: Config
) -> None:
    """Substituting bundled music for a typo'd --music path would hide the typo."""
    result = resolve_music_file(
        generator_enabled,
        tmp_path / "absent.mp3",
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result is None


def test_no_music_still_means_no_music(tmp_path: Path, generator_enabled: Config) -> None:
    result = resolve_music_file(
        generator_enabled,
        None,
        no_music=True,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=_library(tmp_path),
    )

    assert result is None
