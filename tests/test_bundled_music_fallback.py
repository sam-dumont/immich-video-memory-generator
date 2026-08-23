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


def test_resolve_music_uses_the_bundle_when_no_backend_is_configured(library):
    from immich_memories.generate_music import resolve_music

    config = Config()
    config.ace_step.enabled = False
    config.musicgen.enabled = False

    chosen = resolve_music(
        config=config,
        music_path=None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
    )

    assert chosen.path is not None


def test_no_music_still_means_no_music(library):
    from immich_memories.generate_music import resolve_music

    chosen = resolve_music(
        config=Config(),
        music_path=None,
        no_music=True,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
    )

    assert chosen.path is None


def test_a_missing_explicit_track_is_not_silently_replaced(library, tmp_path):
    """A typo in --music should fail loudly, not quietly play something else."""
    from immich_memories.generate_music import resolve_music

    chosen = resolve_music(
        config=Config(),
        music_path=tmp_path / "typo.mp3",
        no_music=False,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
    )

    assert chosen.path is None


def test_bundled_music_is_mastered_before_use(library, tmp_path):
    """Bundled and generated tracks get the tilt and loudness target; user files do not."""
    from immich_memories.generate_music import resolve_music

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
        resolve_music(
            config=config,
            music_path=None,
            no_music=False,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
            bundled_library=library,
            transition_overlap=0.0,
        )
    finally:
        mastering.master_music_track = original

    assert seen, "bundled music should be mastered"


def _emotional_clips(emotion, tmp_path):
    from immich_memories.processing.assembly_config import AssemblyClip

    return [
        AssemblyClip(path=tmp_path / f"clip{i}.mp4", duration=3.0, llm_emotion=emotion)
        for i in range(3)
    ]


def _bundled_choice(clips, library, tmp_path, monkeypatch):
    from immich_memories.generate_music import resolve_music

    # WHY: replaces the FFmpeg mastering pass so the pick keeps its folder.
    monkeypatch.setattr(
        "immich_memories.audio.mastering.master_music_track", lambda source, _dest: source
    )
    config = Config()
    config.ace_step.enabled = False
    config.musicgen.enabled = False
    return resolve_music(
        config=config,
        music_path=None,
        no_music=False,
        assembly_clips=clips,
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
    ).path


def test_the_analysers_emotion_reaches_the_bundled_mood_folder(library, tmp_path, monkeypatch):
    """The five mood folders were inert: AssemblyClip carries llm_emotion, not mood.

    Asserted over repeats because a mood-blind pick would land in the right
    folder by chance often enough to pass a single draw.
    """
    clips = _emotional_clips("joyful", tmp_path)

    folders = {
        _bundled_choice(clips, library, tmp_path, monkeypatch).parent.name for _ in range(30)
    }

    assert folders == {"happy"}, "joyful belongs to the happy family"


def test_clips_with_no_emotion_still_draw_from_the_whole_library(library, tmp_path, monkeypatch):
    clips = _emotional_clips(None, tmp_path)

    folders = {
        _bundled_choice(clips, library, tmp_path, monkeypatch).parent.name for _ in range(30)
    }

    assert folders == {"calm", "happy"}


@pytest.mark.parametrize(
    ("emotion", "folder"),
    [
        # The vocabulary the analysis prompt asks the vision LLM for.
        ("happy", "happy"),
        ("calm", "calm"),
        ("excited", "energetic"),
        ("playful", "happy"),
        ("joyful", "happy"),
        ("peaceful", "calm"),
        # Families with no folder of their own borrow the closest that has one.
        ("romantic", "tender"),
        ("wistful", "nostalgic"),
    ],
)
def test_every_emotion_the_prompt_asks_for_reaches_a_shipped_folder(tmp_path, emotion, folder):
    """A vocabulary drift here is what left all five folders unreachable."""
    from immich_memories.processing.scaling_utilities import aggregate_mood_from_clips

    shipped = tmp_path / "shipped"
    for name in ("calm", "energetic", "happy", "nostalgic", "tender"):
        (shipped / name).mkdir(parents=True)
        (shipped / name / f"{name}.opus").write_bytes(b"")
    mood = aggregate_mood_from_clips(_emotional_clips(emotion, tmp_path))

    # Repeated because a mood with no folder falls to the flat pool, which would
    # land on the right one by chance once in five.
    folders = {bundled_track_for_mood(mood, library=shipped).parent.name for _ in range(20)}

    assert folders == {folder}


def test_a_user_supplied_track_is_left_alone(library, tmp_path):
    from immich_memories.generate_music import resolve_music

    theirs = tmp_path / "mine.mp3"
    theirs.write_bytes(b"")

    chosen = resolve_music(
        config=Config(),
        music_path=theirs,
        no_music=False,
        assembly_clips=[],
        run_output_dir=tmp_path,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
    )

    assert chosen.path == theirs


def test_asking_for_bundled_skips_a_configured_generator(library, monkeypatch):
    """ "Bundled" has to mean bundled, not "bundled if generation fails".

    With a generator configured, `resolve_music` tries it first and only reaches
    the bundle when it is unavailable or errors. A UI option offering "Bundled"
    on top of that precedence would hand the user AI music instead — the option
    would lie. An explicit source says which one was asked for.
    """
    from immich_memories.generate_music import MusicSource, resolve_music

    config = Config()
    config.musicgen.enabled = True

    generated = []

    def never(*_args, **_kwargs):
        generated.append(True)
        raise AssertionError("the generator must not run when bundled was asked for")

    # WHY: generation would hit a model server; asking for bundled must not reach it.
    monkeypatch.setattr("immich_memories.generate_music.auto_generate_music", never)

    chosen = resolve_music(
        config=config,
        music_path=None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
        source=MusicSource.BUNDLED,
    )

    assert chosen.path is not None
    assert generated == []


def test_the_default_source_keeps_todays_precedence(library, monkeypatch):
    """Omitting the source must behave exactly as before — the CLI depends on it."""
    from immich_memories.generate_music import resolve_music

    config = Config()
    config.musicgen.enabled = True

    asked = []

    # WHY: stands in for the model server; records that generation was preferred.
    monkeypatch.setattr(
        "immich_memories.generate_music.auto_generate_music",
        lambda *_a, **_k: asked.append(True) or None,
    )

    resolve_music(
        config=config,
        music_path=None,
        no_music=False,
        assembly_clips=[],
        run_output_dir=library,
        memory_type=None,
        bundled_library=library,
        transition_overlap=0.0,
    )

    assert asked == [True]
