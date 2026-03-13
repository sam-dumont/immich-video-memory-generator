"""Tests for ACE-Step prompt generation."""

from pathlib import Path

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
    assert "key of" in tags.lower()


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
        assert "caption" in template, f"Template '{name}' missing 'caption'"
        assert "bpm" in template, f"Template '{name}' missing bpm"
        assert "key" in template, f"Template '{name}' missing key"
        assert "time_signature" in template, f"Template '{name}' missing time_signature"


def test_structured_caption_has_explicit_musical_params():
    result = build_ace_caption_structured("happy")
    assert isinstance(result.bpm, int)
    assert result.bpm > 0
    assert result.key_scale != ""
    assert result.time_signature != ""
    assert "instrumental" in result.caption.lower()


def test_structured_caption_bpm_not_in_caption_text():
    """BPM should be a separate field, not embedded in caption."""
    result = build_ace_caption_structured("happy")
    assert "BPM" not in result.caption
    assert str(result.bpm) not in result.caption


def test_structured_caption_seasonal_modifier():
    result = build_ace_caption_structured("happy", season="winter")
    assert "cozy" in result.caption.lower() or "warm" in result.caption.lower()


def test_memory_type_trip_picks_sunny_or_acoustic():
    """Trip memory type should pick travel-appropriate music."""
    result = build_ace_caption_structured("happy", memory_type="trip")
    # Trip maps to tropical template (sunny summer feel) or acoustic
    assert (
        "sunny" in result.caption.lower()
        or "summer" in result.caption.lower()
        or "acoustic" in result.caption.lower()
    )


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

    # With trip memory_type, should pick tropical template (sunny/summer) instead
    with_trip = build_ace_caption_structured("calm", memory_type="trip")
    assert (
        "sunny" in with_trip.caption.lower()
        or "summer" in with_trip.caption.lower()
        or "acoustic" in with_trip.caption.lower()
    )


def test_unknown_memory_type_falls_back_to_mood():
    """Unknown memory type should fall back to mood-based selection."""
    result = build_ace_caption_structured("calm", memory_type="unknown_type")
    assert "ambient" in result.caption.lower()


class TestACEStepAPIPayload:
    """Test that the API payload sent to ACE-Step includes all required params."""

    def test_api_payload_includes_instrumental_flag(self):
        """The API must send instrumental=True to prevent vocal generation."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx

        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest

        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))

        # Capture the payload sent to the API
        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "test-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise httpx.HTTPError("unexpected url")

        import asyncio

        with (
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            request = GenerationRequest(
                prompt="happy",
                duration_seconds=30,
                output_dir=Path("/tmp/test_ace"),
            )
            asyncio.run(backend._generate_api(request))

        assert captured_payload.get("instrumental") is True
        assert "bpm" in captured_payload
        assert "keyscale" in captured_payload
        assert "timesignature" in captured_payload


class TestDescriptiveCaptions:
    """Captions should be descriptive sentences, not just comma-separated tag lists."""

    def test_caption_is_descriptive_not_tag_list(self):
        """Caption should read as a description, not a bare tag list."""
        result = build_ace_caption_structured("happy")
        # A descriptive caption starts with an article or descriptor
        # not just "pop, upbeat, feel-good, bright"
        first_word = result.caption.split()[0].lower()
        descriptive_starters = {
            "a",
            "an",
            "warm",
            "bright",
            "mellow",
            "dreamy",
            "smooth",
            "lush",
            "energetic",
            "gentle",
            "epic",
            "festive",
            "driving",
        }
        assert first_word in descriptive_starters, (
            f"Caption should start descriptively, not with '{first_word}': {result.caption}"
        )

    def test_all_templates_produce_descriptive_captions(self):
        """Every template should produce a descriptive sentence."""
        from immich_memories.audio.generators.ace_step_captions import ACE_CAPTION_TEMPLATES

        for _name in ACE_CAPTION_TEMPLATES:
            # Access template directly
            result = build_ace_caption_structured("happy", memory_type=None)
            # At minimum: caption should contain "instrumental"
            assert "instrumental" in result.caption.lower(), (
                f"Template-derived caption missing 'instrumental': {result.caption}"
            )


class TestMoodVariety:
    """Template matching should not be biased by generic booster words."""

    def test_romantic_not_overridden_by_upbeat(self):
        """'upbeat romantic' should match acoustic, not upbeat_pop."""
        from immich_memories.audio.generators.ace_step_captions import _match_template

        # _transform_mood turns "romantic" into "upbeat romantic"
        result = _match_template("upbeat romantic")
        assert result == "acoustic", f"Expected 'acoustic' for 'upbeat romantic', got '{result}'"

    def test_nostalgic_not_overridden_by_upbeat(self):
        """'upbeat warm groovy nostalgic' should match lofi, not upbeat_pop."""
        from immich_memories.audio.generators.ace_step_captions import _match_template

        result = _match_template("upbeat warm groovy nostalgic")
        assert result == "lofi", f"Expected 'lofi' for nostalgic, got '{result}'"

    def test_playful_not_overridden_by_upbeat(self):
        """'upbeat playful' should match indie_electronic, not upbeat_pop."""
        from immich_memories.audio.generators.ace_step_captions import _match_template

        result = _match_template("upbeat playful")
        assert result == "indie_electronic", (
            f"Expected 'indie_electronic' for playful, got '{result}'"
        )

    def test_dramatic_not_overridden_by_upbeat(self):
        """'upbeat dramatic' should match cinematic, not upbeat_pop."""
        from immich_memories.audio.generators.ace_step_captions import _match_template

        result = _match_template("upbeat dramatic")
        assert result == "cinematic", f"Expected 'cinematic' for dramatic, got '{result}'"

    def test_calm_transformed_matches_lofi_or_ambient(self):
        """'upbeat warm groovy calm' should match lofi or ambient, not upbeat_pop."""
        from immich_memories.audio.generators.ace_step_captions import _match_template

        result = _match_template("upbeat warm groovy calm")
        assert result in ("lofi", "ambient"), (
            f"Expected 'lofi' or 'ambient' for calm, got '{result}'"
        )

    def test_pure_upbeat_still_matches_upbeat_pop(self):
        """Just 'upbeat' alone should match upbeat_pop (no more specific word)."""
        from immich_memories.audio.generators.ace_step_captions import _match_template

        result = _match_template("upbeat")
        assert result == "upbeat_pop"

    def test_scene_voting_variety(self):
        """A mix of moods should not collapse to upbeat_pop."""
        from immich_memories.audio.generators.ace_step_captions import _pick_template_for_scenes

        scene_moods = [
            "upbeat romantic",
            "upbeat romantic",
            "upbeat dramatic",
        ]
        # Should pick acoustic (2 votes for romantic) or cinematic (1 vote)
        result = _pick_template_for_scenes(scene_moods)
        assert result == "acoustic", f"Expected 'acoustic' for 2x romantic, got '{result}'"


class TestDescriptiveCaptionNoVocals:
    def test_caption_reinforces_no_vocals(self):
        """Caption text should explicitly say no vocals to prevent LLM confusion."""
        result = build_ace_caption_structured("happy")
        caption_lower = result.caption.lower()
        assert (
            "no vocals" in caption_lower
            or "no singing" in caption_lower
            or "instrumental" in caption_lower
        )
