"""Tests for ACE-Step backend — mock only the HTTP calls."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.audio.generators.ace_step_backend import (
    ACEStepBackend,
    ACEStepConfig,
    _detect_season,
    _mood_to_ace_prompt,
    _mood_to_structured_prompt,
)
from immich_memories.audio.generators.base import GenerationRequest

# ---------------------------------------------------------------------------
# _detect_season
# ---------------------------------------------------------------------------


class TestDetectSeason:
    @pytest.mark.parametrize(
        "mood,expected",
        [
            ("holiday fun", "holiday"),
            ("festive cheer", "holiday"),
            ("winter vibes", "winter"),
            ("summer sunshine", "summer"),
            ("sunny days", "summer"),
            ("spring fresh", "spring"),
            ("fresh breezes", "spring"),
            ("autumn leaves", "autumn"),
            ("fall colors", "autumn"),
            ("cozy evening", "autumn"),
            ("happy upbeat", None),
            ("", None),
        ],
    )
    def test_detects_season_from_mood(self, mood, expected):
        assert _detect_season(mood) == expected


# ---------------------------------------------------------------------------
# _mood_to_ace_prompt
# ---------------------------------------------------------------------------


class TestMoodToAcePrompt:
    def test_returns_tags_and_lyrics(self):
        tags, lyrics = _mood_to_ace_prompt("happy")
        assert isinstance(tags, str)
        assert isinstance(lyrics, str)
        assert len(tags) > 5

    def test_instrumental_in_lyrics(self):
        _, lyrics = _mood_to_ace_prompt("energetic")
        assert "[Instrumental]" in lyrics

    def test_with_custom_prompt(self):
        tags, lyrics = _mood_to_ace_prompt("calm", prompt="gentle piano")
        assert isinstance(tags, str)


# ---------------------------------------------------------------------------
# _mood_to_structured_prompt
# ---------------------------------------------------------------------------


class TestMoodToStructuredPrompt:
    def test_returns_caption_result(self):
        result = _mood_to_structured_prompt("happy")
        assert hasattr(result, "caption")
        assert hasattr(result, "lyrics")
        assert hasattr(result, "bpm")
        assert hasattr(result, "key_scale")
        assert hasattr(result, "time_signature")

    def test_bpm_is_positive(self):
        result = _mood_to_structured_prompt("energetic")
        assert result.bpm > 0

    def test_with_scene_moods(self):
        result = _mood_to_structured_prompt("happy", scene_moods=["happy", "calm", "energetic"])
        assert result.caption != ""

    def test_with_memory_type(self):
        result = _mood_to_structured_prompt("happy", memory_type="trip")
        assert result.caption != ""


# ---------------------------------------------------------------------------
# ACEStepConfig
# ---------------------------------------------------------------------------


class TestACEStepConfig:
    def test_defaults(self):
        cfg = ACEStepConfig()
        assert cfg.mode == "lib"
        assert cfg.api_url == "http://localhost:8000"
        assert cfg.model_variant == "turbo"
        assert cfg.timeout_seconds == 3600
        assert cfg.bf16 is True

    def test_custom_config(self):
        cfg = ACEStepConfig(mode="api", api_url="http://remote:9000", bf16=False)
        assert cfg.mode == "api"
        assert cfg.api_url == "http://remote:9000"
        assert cfg.bf16 is False


# ---------------------------------------------------------------------------
# ACEStepBackend
# ---------------------------------------------------------------------------


class TestACEStepBackend:
    def test_name_shows_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))
        backend._effective_mode = "api"
        assert "api" in backend.name.lower()

    def test_name_default(self):
        backend = ACEStepBackend()
        # Before determining mode, shows configured mode
        assert "lib" in backend.name.lower() or "ACE-Step" in backend.name

    def test_get_effective_mode_api_fallback(self):
        """When lib isn't importable, falls back to api."""
        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        # WHY: Mock import check because ace-step isn't installed in test env
        with patch(
            "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
            return_value=False,
        ):
            mode = backend._get_effective_mode()
        assert mode == "api"

    def test_get_effective_mode_caches(self):
        backend = ACEStepBackend()
        backend._effective_mode = "api"
        assert backend._get_effective_mode() == "api"


class TestACEStepBackendAPIAvailability:
    def test_check_api_healthy(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(backend._check_api())
        assert result is True

    def test_check_api_unhealthy(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"status": "error"}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(backend._check_api())
        assert result is False

    def test_check_api_connection_error(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(backend._check_api())
        assert result is False


class TestACEStepBackendAPIGeneration:
    def test_generate_api_builds_correct_payload(self):
        """The API payload includes all required musical parameters."""
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "test-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="happy",
                duration_seconds=30,
                output_dir=Path("/tmp/test_ace_backend"),
            )
            asyncio.run(backend._generate_api(request))

        assert "caption" in captured_payload
        assert "lyrics" in captured_payload
        assert captured_payload["instrumental"] is True
        assert "bpm" in captured_payload
        assert "keyscale" in captured_payload
        assert "timesignature" in captured_payload
        assert captured_payload["duration"] == 30

    def test_generate_api_multi_scene(self):
        """Multi-scene request sums durations and uses scene moods."""
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "multi-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="happy",
                scenes=[
                    {"mood": "happy", "duration": 20},
                    {"mood": "calm", "duration": 15},
                ],
                duration_seconds=60,
                output_dir=Path("/tmp/test_ace_multi"),
            )
            asyncio.run(backend._generate_api(request))

        assert captured_payload["duration"] == 35  # 20 + 15

    def test_generate_api_caps_duration_at_300(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "cap-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="epic",
                duration_seconds=600,
                output_dir=Path("/tmp/test_ace_cap"),
            )
            asyncio.run(backend._generate_api(request))

        assert captured_payload["duration"] == 300  # capped

    def test_generate_api_sends_auth_header(self):
        """API key should be sent as Bearer token."""
        backend = ACEStepBackend(
            ACEStepConfig(
                mode="api",
                api_url="http://fake:8000",
                extra_args={"api_key": "secret-key"},
            )
        )
        backend._effective_mode = "api"

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "auth-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        init_kwargs = {}

        def capture_client_init(**kwargs):
            init_kwargs.update(kwargs)
            return mock_client

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", side_effect=capture_client_init),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="happy",
                duration_seconds=30,
                output_dir=Path("/tmp/test_ace_auth"),
            )
            asyncio.run(backend._generate_api(request))

        assert init_kwargs.get("headers", {}).get("Authorization") == "Bearer secret-key"


class TestACEStepProgressReporting:
    def test_early_phase_llm_reasoning(self):
        callback = MagicMock()
        ACEStepBackend._report_estimated_progress(3.0, callback)
        callback.assert_called_once()
        args = callback.call_args[0]
        assert "LLM" in args[0]
        assert args[1] <= 15

    def test_mid_phase_generating(self):
        callback = MagicMock()
        ACEStepBackend._report_estimated_progress(15.0, callback)
        args = callback.call_args[0]
        assert "Generating" in args[0] or "diffusion" in args[0]

    def test_late_phase_decoding(self):
        callback = MagicMock()
        ACEStepBackend._report_estimated_progress(40.0, callback)
        args = callback.call_args[0]
        assert "Decoding" in args[0]

    def test_no_callback_no_error(self):
        ACEStepBackend._report_estimated_progress(10.0, None)


class TestACEStepHealthCheck:
    def test_health_check_api_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        # WHY: Mock _check_api because it makes real HTTP calls
        with patch.object(backend, "_check_api", new_callable=AsyncMock, return_value=True):
            info = asyncio.run(backend.health_check())

        assert info["backend"] == "ACE-Step (api)"
        assert info["effective_mode"] == "api"
        assert info["available"] is True
        assert info["api_url"] == "http://fake:8000"

    def test_health_check_lib_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        backend._effective_mode = "lib"

        # WHY: Mock _is_ace_step_importable because ace-step isn't installed
        with patch(
            "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
            return_value=False,
        ):
            info = asyncio.run(backend.health_check())

        assert info["effective_mode"] == "lib"
        assert info["available"] is False
