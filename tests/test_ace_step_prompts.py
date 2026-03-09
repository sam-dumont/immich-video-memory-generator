"""Tests for ACE-Step prompt generation."""

from immich_memories.audio.generators.ace_step_backend import (
    ACE_CAPTION_TEMPLATES,
    build_ace_caption,
)


def test_build_ace_caption_returns_tags_and_lyrics():
    tags, lyrics = build_ace_caption("happy")
    assert isinstance(tags, str)
    assert isinstance(lyrics, str)
    assert "[Instrumental]" in lyrics


def test_build_ace_caption_includes_key_and_bpm():
    tags, lyrics = build_ace_caption("happy")
    assert "BPM" in tags
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
        assert "BPM" in template["tags"], f"Template '{name}' missing BPM"
        assert "instruments" in template, f"Template '{name}' missing instruments"
        assert "key" in template, f"Template '{name}' missing key"
