"""Bundled music is the fallback when no generator is configured (#308).

Without it, the recommended Docker/NAS path produces clips, a title card and
silence, because ACE-Step and MusicGen both need a GPU or a separate server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.audio.bundled_music import bundled_track_for_mood
from immich_memories.config_loader import Config


@pytest.fixture
def library(tmp_path):
    """A stand-in for the installed music package, laid out one folder per mood."""
    for mood, names in (("calm", ("a", "b")), ("happy", ("c",))):
        folder = tmp_path / mood
        folder.mkdir()
        for name in names:
            (folder / f"{mood}_{name}.opus").write_bytes(b"")
    return tmp_path


def test_picks_a_track_matching_the_mood(library):
    track = bundled_track_for_mood("calm", library=library)

    assert track is not None
    assert track.parent.name == "calm"


def test_falls_back_to_any_mood_rather_than_returning_silence(library):
    """A mood with no bundled folder should still get music, not nothing."""
    track = bundled_track_for_mood("dramatic", library=library)

    assert track is not None


def test_repeated_calls_do_not_always_return_the_same_track(library):
    chosen = {bundled_track_for_mood("calm", library=library) for _ in range(30)}

    assert len(chosen) > 1


def test_returns_nothing_when_the_package_is_not_installed(tmp_path):
    assert bundled_track_for_mood("calm", library=tmp_path / "absent") is None


def test_resolve_music_file_uses_the_bundle_when_no_backend_is_configured(library):
    from immich_memories.generate_music import resolve_music_file

    config = Config()
    config.ace_step.enabled = False
    config.musicgen.enabled = False

    chosen = resolve_music_file(
        config=config,
        music_path=None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
    )

    assert chosen is not None


def test_no_music_still_means_no_music(library):
    from immich_memories.generate_music import resolve_music_file

    chosen = resolve_music_file(
        config=Config(),
        music_path=None,
        no_music=True,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
    )

    assert chosen is None


def test_a_missing_explicit_track_is_not_silently_replaced(library, tmp_path):
    """A typo in --music should fail loudly, not quietly play something else."""
    from immich_memories.generate_music import resolve_music_file

    chosen = resolve_music_file(
        config=Config(),
        music_path=tmp_path / "typo.mp3",
        no_music=False,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
    )

    assert chosen is None


def test_bundled_music_is_mastered_before_use(library, tmp_path):
    """Bundled and generated tracks get the tilt and loudness target; user files do not."""
    from immich_memories.generate_music import resolve_music_file

    seen: list[Path] = []

    # WHY: replaces the FFmpeg mastering pass; the routing is what is under test.
    def _fake_master(source: Path, destination: Path) -> Path:
        seen.append(source)
        return destination

    import immich_memories.audio.mastering as mastering

    original = mastering.master_music_track
    mastering.master_music_track = _fake_master
    try:
        config = Config()
        config.ace_step.enabled = False
        config.musicgen.enabled = False
        resolve_music_file(
            config=config,
            music_path=None,
            no_music=False,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
            bundled_library=library,
        )
    finally:
        mastering.master_music_track = original

    assert seen, "bundled music should be mastered"


def test_a_user_supplied_track_is_left_alone(library, tmp_path):
    from immich_memories.generate_music import resolve_music_file

    theirs = tmp_path / "mine.mp3"
    theirs.write_bytes(b"")

    chosen = resolve_music_file(
        config=Config(),
        music_path=theirs,
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=library,
    )

    assert chosen == theirs
