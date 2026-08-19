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

        assert captured_payload.get("instrumental")
        assert "bpm" in captured_payload
        assert "keyscale" in captured_payload
        assert "timesignature" in captured_payload


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


# =============================================================================
# Mood x style matrix contract
#
# Captions are deliberately tag lists now rather than prose. ACE-Step's
# prompting guides put the genre anchor first and treat fidelity/production
# tags as the lever for clarity; adjective-heavy prose gave the model little
# concrete to render and sounded synthetic. Memory type now steers the *style*
# instead of replacing the whole template, so a calm trip is still calm.
# =============================================================================


def test_structured_caption_carries_bpm_in_both_places():
    """The guides say to state BPM in the tags and as the field."""
    result = build_ace_caption_structured("happy")

    assert result.bpm > 0
    assert f"{result.bpm} bpm" in result.caption


def test_key_and_meter_are_left_for_the_model_to_infer():
    assert build_ace_caption_structured("happy").key_scale == ""


def test_caption_leads_with_its_genre_anchor():
    from immich_memories.audio.generators.ace_step_captions import STYLE_PROFILES

    result = build_ace_caption_structured("happy", style="jazz")

    assert result.caption.startswith(STYLE_PROFILES["jazz"].genre)


def test_memory_type_selects_a_style():
    trip = build_ace_caption_structured("happy", memory_type="trip")
    spotlight = build_ace_caption_structured("happy", memory_type="person_spotlight")

    assert trip.caption.startswith("indie rock")
    assert spotlight.caption.startswith("acoustic folk")


def test_a_style_can_name_a_different_genre_per_mood():
    """Electronic sub-genres are tempo-defined: drum and bass at 70 bpm is not a thing."""
    fast = build_ace_caption_structured("energetic", style="electronic")
    slow = build_ace_caption_structured("calm", style="electronic")

    assert fast.caption.startswith("drum and bass")
    assert slow.caption.startswith("downtempo electronic")


def test_memory_type_does_not_override_the_moods_tempo():
    """A calm trip should still be calm — only the instrumentation changes."""
    calm_trip = build_ace_caption_structured("calm", memory_type="trip")

    assert calm_trip.bpm == build_ace_caption_structured("calm").bpm


def test_unknown_memory_type_still_produces_a_caption():
    result = build_ace_caption_structured("calm", memory_type="not_a_memory_type")

    assert "serene" in result.caption
    assert f"{result.bpm} bpm" in result.caption
