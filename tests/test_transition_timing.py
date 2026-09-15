"""A saved cut has reproducible transitions on every assembly path."""

from dataclasses import replace
from pathlib import Path

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.assembly_engine import AssemblyEngine
from immich_memories.processing.clip_encoder import ClipEncoder
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.title_inserter import TitleInserter


def assembly(mode):
    settings = AssemblySettings(encoding_plan=standalone_assembly_encoding_plan(), transition=mode)
    prober = FFmpegProber(settings)
    return AssemblyEngine(
        settings, prober, ClipEncoder(settings, prober, lambda _: None)
    ), TitleInserter(settings, prober)


def clips():
    return [
        AssemblyClip(path=Path(f"/one/{i}.mp4"), duration=4, asset_id=str(i)) for i in range(16)
    ]


def test_smart_decisions_are_shared_and_independent_of_process_randomness(monkeypatch):
    # WHY: make accidental dependence on process-global randomness fail directly.
    monkeypatch.setattr("random.random", lambda: pytest.fail("unseeded transition choice"))
    engine, titles = assembly(TransitionType.SMART)
    first = clips()
    rerendered = [replace(clip, path=Path(f"/two/{clip.asset_id}.mp4")) for clip in first]
    transitions = engine.get_transition_types(first)
    assert {"fade", "cut"} == set(transitions)
    assert transitions == titles._decide_transitions_for_final_clips(first)
    assert transitions == titles._decide_transitions_for_final_clips(rerendered)


@pytest.mark.parametrize(
    "mode, expected",
    [(TransitionType.CUT, "cut"), (TransitionType.NONE, "cut"), (TransitionType.CROSSFADE, "fade")],
)
def test_composed_titles_respect_the_requested_transition_mode(mode, expected):
    engine, titles = assembly(mode)
    content = clips()[:3]
    content[0].is_title_screen = True
    assert engine.get_transition_types(content) == [expected, expected]
    assert titles._decide_transitions_for_final_clips(content) == [expected, expected]


def test_zero_duration_does_not_turn_into_a_half_second_fade():
    engine, titles = assembly(TransitionType.SMART)
    engine.settings.transition_duration = 0
    assert engine.get_transition_types(clips()) == ["cut"] * 15
    assert titles._decide_transitions_for_final_clips(clips()) == ["cut"] * 15
