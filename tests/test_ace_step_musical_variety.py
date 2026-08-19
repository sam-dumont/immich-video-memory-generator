"""Tempo and key must vary by combination, not just by mood (#308).

Every style inheriting the mood's tempo made happy-acoustic, happy-rock and
happy-jazz land on the same beat, and an empty key let the model settle into
the same tonality every time. Style now owns a plausible tempo band that the
mood positions within, and each combination gets its own key.
"""

from __future__ import annotations

import pytest

from immich_memories.audio.generators.ace_step_captions import (
    MOOD_PROFILES,
    STYLE_PROFILES,
    build_ace_caption_structured,
)

MOODS = sorted(MOOD_PROFILES)
STYLES = sorted(STYLE_PROFILES)


def test_one_mood_gets_a_different_tempo_from_each_style():
    """happy-acoustic and happy-rock should not share a beat."""
    tempos = {build_ace_caption_structured("happy", style=s).bpm for s in STYLES}

    assert len(tempos) == len(STYLES), tempos


def test_a_style_still_speeds_up_with_the_mood():
    """Within one style, energetic outruns calm."""
    for style in STYLES:
        calm = build_ace_caption_structured("calm", style=style).bpm
        energetic = build_ace_caption_structured("energetic", style=style).bpm

        assert energetic >= calm, style


def test_every_style_stays_inside_a_tempo_its_genre_can_carry():
    for style in STYLES:
        low, high = STYLE_PROFILES[style].tempo_range
        for mood in MOODS:
            assert low <= build_ace_caption_structured(mood, style=style).bpm <= high


def test_combinations_do_not_all_share_one_key():
    keys = {build_ace_caption_structured(m, style=s).key_scale for m in MOODS for s in STYLES}

    assert len(keys) >= 5, keys


def test_key_is_one_ace_step_accepts():
    """An out-of-vocabulary key is force-injected into the LM hints unvalidated."""
    from immich_memories.audio.generators.ace_step_captions import VALID_KEY_ROOTS

    for mood in MOODS:
        for style in STYLES:
            key = build_ace_caption_structured(mood, style=style).key_scale
            root, _, mode = key.partition(" ")

            assert root in VALID_KEY_ROOTS, key
            assert mode in ("major", "minor"), key


@pytest.mark.parametrize("mood", MOODS)
def test_sad_moods_lean_minor_and_bright_moods_major(mood):
    expected = MOOD_PROFILES[mood].mode

    assert build_ace_caption_structured(mood, style="acoustic").key_scale.endswith(expected)
