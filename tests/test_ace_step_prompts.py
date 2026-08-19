"""Tests for ACE-Step prompt generation."""

from pathlib import Path

import pytest

from immich_memories.audio.generators.ace_step_backend import (
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


def test_every_matrix_cell_yields_a_complete_caption():
    """Replaces the old per-template shape check: the matrix is the source now."""
    from immich_memories.audio.generators.ace_step_captions import (
        MOOD_PROFILES,
        STYLE_PROFILES,
    )

    for mood in MOOD_PROFILES:
        for style in STYLE_PROFILES:
            result = build_ace_caption_structured(mood, style=style)

            assert result.caption
            assert result.bpm > 0
            assert result.key_scale
            assert result.time_signature


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
    """Mood resolution must not be hijacked by generic booster words.

    _transform_mood prepends words like "upbeat" and "warm" to every mood, so a
    naive first-match would send every memory to the same profile.
    """

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("upbeat romantic", "tender"),
            ("upbeat warm groovy nostalgic", "nostalgic"),
            ("upbeat playful", "happy"),
            ("upbeat warm calm", "calm"),
            ("upbeat", "happy"),
        ],
    )
    def test_specific_mood_words_win_over_boosters(self, phrase, expected):
        from immich_memories.audio.generators.ace_step_captions import resolve_mood

        assert resolve_mood(phrase) == expected

    def test_scene_moods_drive_the_profile(self):
        from immich_memories.audio.generators.ace_step_captions import (
            build_ace_caption_structured,
        )

        result = build_ace_caption_structured("happy", scene_moods=["calm", "calm"])

        assert "serene" in result.caption


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


def test_key_is_stated_so_combinations_do_not_share_a_tonality():
    """Left empty, the model settled on the same tonality for every style."""
    key = build_ace_caption_structured("happy", style="acoustic").key_scale

    assert key.endswith("major")
    assert key.split()[0] in ("C", "D", "E", "F", "G", "A", "Bb")


def test_caption_leads_with_its_genre_anchor():
    from immich_memories.audio.generators.ace_step_captions import STYLE_PROFILES

    result = build_ace_caption_structured("happy", style="acoustic")

    assert result.caption.startswith(STYLE_PROFILES["acoustic"].genre)


def test_memory_type_selects_a_style():
    spotlight = build_ace_caption_structured("happy", memory_type="person_spotlight")
    on_this_day = build_ace_caption_structured("happy", memory_type="on_this_day")

    assert spotlight.caption.startswith("acoustic folk")
    assert on_this_day.caption.startswith("future bass")


def test_a_style_can_name_a_different_genre_per_mood():
    """Electronic sub-genres are tempo-defined: drum and bass at 70 bpm is not a thing."""
    fast = build_ace_caption_structured("energetic", style="electronic")
    slow = build_ace_caption_structured("calm", style="electronic")

    assert fast.caption.startswith("drum and bass")
    assert slow.caption.startswith("downtempo electronic")


def test_memory_type_does_not_override_the_moods_energy():
    """A calm spotlight is still calm: it sits low in its style's tempo band."""
    calm = build_ace_caption_structured("calm", memory_type="person_spotlight")
    energetic = build_ace_caption_structured("energetic", memory_type="person_spotlight")

    assert calm.bpm < energetic.bpm


def test_unknown_memory_type_still_produces_a_caption():
    result = build_ace_caption_structured("calm", memory_type="not_a_memory_type")

    assert "serene" in result.caption
    assert f"{result.bpm} bpm" in result.caption
