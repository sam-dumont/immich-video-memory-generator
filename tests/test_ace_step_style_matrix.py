"""Mood x style matrix for generated music (#308).

One caption per mood made every memory of that mood sound the same. Mood now
sets the tempo and emotional register; style picks the genre and instruments,
so the same mood can arrive as acoustic, orchestral, jazz, indie or funk.
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


def test_the_matrix_is_the_advertised_size():
    assert len(MOODS) == 5
    assert len(STYLES) == 5


@pytest.mark.parametrize("mood", MOODS)
@pytest.mark.parametrize("style", STYLES)
class TestEveryCombinationIsUsable:
    def test_combination_names_its_genre_and_mood(self, mood, style):
        result = build_ace_caption_structured(mood, style=style)

        assert STYLE_PROFILES[style].genre_for(mood) in result.caption
        assert MOOD_PROFILES[mood].word in result.caption

    def test_combination_states_bpm_in_tags_and_field(self, mood, style):
        result = build_ace_caption_structured(mood, style=style)

        assert f"{result.bpm} bpm" in result.caption

    def test_combination_fits_the_api_limit(self, mood, style):
        assert len(build_ace_caption_structured(mood, style=style).caption) < 512


def test_tempo_follows_the_mood_not_the_style():
    """A calm memory stays calm whichever genre renders it."""
    calm = {build_ace_caption_structured("calm", style=s).bpm for s in STYLES}
    energetic = {build_ace_caption_structured("energetic", style=s).bpm for s in STYLES}

    assert max(calm) < min(energetic)


def test_repeated_memories_of_one_mood_do_not_all_sound_alike():
    """Without an explicit style the matrix is sampled, so variety emerges."""
    captions = {build_ace_caption_structured("happy").caption for _ in range(40)}

    assert len(captions) > 1


def test_an_explicit_style_is_reproducible():
    first = build_ace_caption_structured("happy", style="jazz").caption
    again = build_ace_caption_structured("happy", style="jazz").caption

    assert first == again


def test_an_unknown_style_falls_back_instead_of_failing():
    result = build_ace_caption_structured("happy", style="klezmer-polka")

    assert result.caption
    assert f"{result.bpm} bpm" in result.caption
