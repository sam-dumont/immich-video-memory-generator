"""Tests for ACE-Step prompt generation."""

from immich_memories.audio.generators.ace_step_backend import (
    ACE_CAPTION_TEMPLATES,
    build_ace_caption,
)
from immich_memories.audio.generators.ace_step_captions import (
    build_ace_caption_structured,
)


def test_build_ace_caption_returns_tags_and_lyrics():
    tags, lyrics = build_ace_caption("happy")
    assert isinstance(tags, str)
    assert isinstance(lyrics, str)
    assert "[Instrumental]" in lyrics


def test_build_ace_caption_includes_key():
    tags, _ = build_ace_caption("happy")
    assert "key of" in tags


def test_build_ace_caption_all_moods_covered():
    moods = [
        "happy",
        "energetic",
        "calm",
        "nostalgic",
        "romantic",
        "playful",
        "dramatic",
        "peaceful",
        "inspiring",
    ]
    for mood in moods:
        tags, lyrics = build_ace_caption(mood)
        assert len(tags) > 10, f"Tags too short for mood '{mood}': {tags}"


def test_build_ace_caption_seasonal_modifiers():
    tags, _ = build_ace_caption("happy", season="winter")
    assert "cozy" in tags.lower() or "warm" in tags.lower()


def test_build_ace_caption_unknown_mood_uses_default():
    tags, _ = build_ace_caption("xyznonexistent")
    assert len(tags) > 10


def test_caption_templates_have_required_fields():
    for name, template in ACE_CAPTION_TEMPLATES.items():
        assert "tags" in template, f"Template '{name}' missing 'tags'"
        assert "bpm" in template, f"Template '{name}' missing bpm"
        assert "key" in template, f"Template '{name}' missing key"
        assert "time_signature" in template, f"Template '{name}' missing time_signature"
        assert "instruments" in template, f"Template '{name}' missing instruments"


def test_structured_caption_has_explicit_musical_params():
    result = build_ace_caption_structured("happy")
    assert isinstance(result.bpm, int)
    assert result.bpm > 0
    assert result.key_scale != ""
    assert result.time_signature != ""
    assert "loop background" in result.caption
    assert "instrumental" in result.caption


def test_structured_caption_bpm_not_in_caption_text():
    """BPM should be a separate field, not embedded in caption."""
    result = build_ace_caption_structured("happy")
    assert "BPM" not in result.caption
    assert str(result.bpm) not in result.caption


def test_structured_caption_seasonal_modifier():
    result = build_ace_caption_structured("happy", season="winter")
    assert "cozy" in result.caption.lower() or "warm" in result.caption.lower()


def test_memory_type_trip_picks_tropical_or_acoustic():
    """Trip memory type should pick travel-appropriate music."""
    result = build_ace_caption_structured("happy", memory_type="trip")
    # Trip maps to tropical or acoustic
    assert "tropical" in result.caption.lower() or "acoustic" in result.caption.lower()


def test_memory_type_person_spotlight_picks_acoustic():
    """Person Spotlight should pick intimate/acoustic music."""
    result = build_ace_caption_structured("happy", memory_type="person_spotlight")
    assert "acoustic" in result.caption.lower() or "gentle" in result.caption.lower()


def test_memory_type_on_this_day_picks_nostalgic():
    """On This Day should pick nostalgic music."""
    result = build_ace_caption_structured("happy", memory_type="on_this_day")
    assert "lo-fi" in result.caption.lower() or "lofi" in result.caption.lower()


def test_memory_type_overrides_mood():
    """Memory type should take priority over mood for template selection."""
    # Without memory_type, "calm" maps to ambient
    without = build_ace_caption_structured("calm")
    assert "ambient" in without.caption.lower()

    # With trip memory_type, should pick tropical instead
    with_trip = build_ace_caption_structured("calm", memory_type="trip")
    assert "tropical" in with_trip.caption.lower() or "acoustic" in with_trip.caption.lower()


def test_unknown_memory_type_falls_back_to_mood():
    """Unknown memory type should fall back to mood-based selection."""
    result = build_ace_caption_structured("calm", memory_type="unknown_type")
    assert "ambient" in result.caption.lower()
