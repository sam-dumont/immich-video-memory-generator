"""Tests for the music generation backend abstraction."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.audio.generators.ace_step_backend import (
    ACEStepBackend,
    ACEStepConfig,
    _is_ace_step_importable,
    _mood_to_ace_prompt,
)
from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
)
from immich_memories.audio.generators.factory import create_generator
from immich_memories.audio.generators.musicgen_backend import MusicGenBackend
from immich_memories.audio.music_generator import (
    MUSIC_PROMPTS,
    _get_base_prompt,
)
from immich_memories.audio.music_generator_client import (
    MusicGenClient,
    MusicGenClientConfig,
)
from immich_memories.audio.music_generator_models import (
    SEASONAL_MOODS,
    ClipMood,
    GeneratedMusic,
    MusicGenerationResult,
    MusicStems,
    VideoTimeline,
    get_seasonal_prompt,
)

# =============================================================================
# GenerationRequest tests
# =============================================================================


class TestGenerationRequest:
    """Tests for GenerationRequest dataclass."""

    def test_is_multi_scene_false(self):
        req = GenerationRequest(scenes=[{"mood": "happy", "duration": 30}])
        assert not req.is_multi_scene

    def test_is_multi_scene_true(self):
        req = GenerationRequest(
            scenes=[
                {"mood": "happy", "duration": 30},
                {"mood": "calm", "duration": 20},
            ]
        )
        assert req.is_multi_scene


# =============================================================================
# GenerationResult tests
# =============================================================================


class TestGenerationResult:
    """Tests for GenerationResult dataclass."""

    def test_basic(self):
        result = GenerationResult(
            audio_path=Path("/tmp/test.wav"),
            duration_seconds=60.0,
            prompt="upbeat",
            backend_name="TestBackend",
        )
        assert result.audio_path == Path("/tmp/test.wav")
        assert result.backend_name == "TestBackend"
        assert not result.metadata


# =============================================================================
# MusicGenerator ABC tests
# =============================================================================


class _DummyGenerator(MusicGenerator):
    """Reusable concrete MusicGenerator for tests."""

    @property
    def name(self):
        return "Dummy"

    async def is_available(self):
        return True

    async def generate(self, request, progress_callback=None):
        return GenerationResult(
            audio_path=Path("/tmp/dummy.wav"),
            backend_name="Dummy",
        )


class TestMusicGeneratorABC:
    """Tests for the abstract base class."""

    def test_concrete_implementation(self):
        """A concrete implementation should be instantiable."""
        gen = _DummyGenerator()
        assert gen.name == "Dummy"

    @pytest.mark.asyncio
    async def test_default_generate_with_stems_returns_none_stems(self):
        """Default generate_with_stems should return None for stems."""
        gen = _DummyGenerator()
        result, stems = await gen.generate_with_stems(GenerationRequest())
        assert result.audio_path == Path("/tmp/dummy.wav")
        assert stems is None

    @pytest.mark.asyncio
    async def test_default_health_check(self):
        """Default health_check should return backend name and availability."""
        gen = _DummyGenerator()
        health = await gen.health_check()
        assert health["backend"] == "Dummy"
        assert health["available"]

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Default __aenter__/__aexit__ should work."""
        async with _DummyGenerator() as gen:
            assert gen.name == "Dummy"


# =============================================================================
# Seasonal prompt tests
# =============================================================================


class TestSeasonalPrompts:
    """Tests for seasonal mood prompt generation."""

    def test_all_months_have_moods(self):
        for month in range(1, 13):
            assert month in SEASONAL_MOODS
            assert SEASONAL_MOODS[month]

    def test_get_seasonal_prompt_valid(self):
        result = get_seasonal_prompt(12)
        assert "holiday" in result or "festive" in result or "winter" in result

    def test_get_seasonal_prompt_invalid_month(self):
        assert get_seasonal_prompt(0) == ""
        assert get_seasonal_prompt(13) == ""

    def test_southern_hemisphere_inverts(self):
        # July in south = January in north (winter)
        south_july = get_seasonal_prompt(7, hemisphere="south")
        north_january = get_seasonal_prompt(1, hemisphere="north")
        assert south_july == north_january

    def test_northern_hemisphere_default(self):
        result = get_seasonal_prompt(6)
        assert (
            "summer" in result.lower() or "carefree" in result.lower() or "sunny" in result.lower()
        )


# =============================================================================
# VideoTimeline tests
# =============================================================================


class TestVideoTimeline:
    """Tests for VideoTimeline data model."""

    def test_empty_timeline(self):
        timeline = VideoTimeline()
        assert timeline.content_duration == 0.0
        assert (
            timeline.total_duration
            == timeline.title_duration + timeline.ending_duration + timeline.fade_buffer
        )
        assert timeline.content_start == timeline.title_duration

    def test_content_duration_no_transitions(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=10.0, mood="happy"),
                ClipMood(duration=20.0, mood="calm"),
            ]
        )
        assert timeline.content_duration == 30.0

    def test_content_duration_with_transitions(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(
                    duration=10.0, mood="happy", has_transition_after=True, transition_duration=2.0
                ),
                ClipMood(duration=20.0, mood="calm"),
            ]
        )
        assert timeline.content_duration == 32.0

    def test_total_duration(self):
        timeline = VideoTimeline(
            title_duration=3.5,
            ending_duration=7.0,
            fade_buffer=5.0,
            clips=[ClipMood(duration=60.0, mood="upbeat")],
        )
        assert timeline.total_duration == 3.5 + 60.0 + 7.0 + 5.0

    def test_build_scenes_empty(self):
        timeline = VideoTimeline()
        scenes = timeline.build_scenes()
        assert len(scenes) == 1
        assert scenes[0]["mood"] == "upbeat"

    def test_build_scenes_single_clip(self):
        timeline = VideoTimeline(
            title_duration=3.0,
            ending_duration=5.0,
            fade_buffer=4.0,
            clips=[ClipMood(duration=30.0, mood="happy", month=7)],
        )
        scenes = timeline.build_scenes()
        assert len(scenes) == 1
        # Duration should include title + clip + ending + fade
        assert scenes[0]["duration"] == int(3.0 + 30.0 + 5.0 + 4.0)

    def test_build_scenes_multi_clip(self):
        timeline = VideoTimeline(
            title_duration=3.0,
            ending_duration=5.0,
            fade_buffer=4.0,
            clips=[
                ClipMood(duration=15.0, mood="happy"),
                ClipMood(duration=20.0, mood="calm"),
            ],
        )
        scenes = timeline.build_scenes()
        assert len(scenes) == 2
        # First clip gets title duration added
        assert scenes[0]["duration"] == int(3.0 + 15.0)
        # Last clip gets ending + fade added
        assert scenes[1]["duration"] == int(20.0 + 5.0 + 4.0)

    def test_build_scenes_minimum_5s(self):
        """Scenes should be at least 5 seconds."""
        timeline = VideoTimeline(
            title_duration=0.0,
            ending_duration=0.0,
            fade_buffer=0.0,
            clips=[
                ClipMood(duration=2.0, mood="happy"),
                ClipMood(duration=3.0, mood="calm"),
            ],
        )
        scenes = timeline.build_scenes()
        for scene in scenes:
            assert scene["duration"] >= 5

    def test_build_scenes_mellow_mood_boosted(self):
        """Mellow moods should get upbeat prefix."""
        timeline = VideoTimeline(clips=[ClipMood(duration=30.0, mood="calm peaceful")])
        scenes = timeline.build_scenes()
        assert "upbeat" in scenes[0]["mood"].lower()

    def test_build_scenes_sad_mood_transformed(self):
        """Sad moods should be transformed to warm/uplifting."""
        timeline = VideoTimeline(clips=[ClipMood(duration=30.0, mood="sad melancholy")])
        scenes = timeline.build_scenes()
        assert "warm" in scenes[0]["mood"].lower() or "uplifting" in scenes[0]["mood"].lower()

    def test_from_clips_basic(self):
        timeline = VideoTimeline.from_clips(
            clips=[(10.0, "happy", 6), (20.0, "calm", 7)],
            title_duration=3.0,
            ending_duration=5.0,
        )
        assert len(timeline.clips) == 2
        assert timeline.clips[0].duration == 10.0
        assert timeline.clips[0].mood == "happy"
        assert timeline.clips[0].month == 6
        assert timeline.clips[1].month == 7

    def test_from_clips_with_transitions(self):
        timeline = VideoTimeline.from_clips(
            clips=[(10.0, "happy", None), (20.0, "calm", None)],
            transitions_after=[0],
        )
        assert timeline.clips[0].has_transition_after
        assert not timeline.clips[1].has_transition_after

    def test_from_clips_two_tuple(self):
        """Should support (duration, mood) tuples without month."""
        timeline = VideoTimeline.from_clips(
            clips=[(10.0, "happy"), (20.0, "calm")],
        )
        assert timeline.clips[0].month is None


# =============================================================================
# MusicGenClient mock tests
# =============================================================================


class TestMusicGenClient:
    """Tests for MusicGenClient with mocked HTTP."""

    @staticmethod
    def _make_resp(**kwargs):
        """Create a MagicMock httpx response (sync raise_for_status/json)."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "json_data" in kwargs:
            resp.json.return_value = kwargs["json_data"]
        if "content" in kwargs:
            resp.content = kwargs["content"]
        return resp

    def test_config_defaults(self):
        config = MusicGenClientConfig()
        assert config.base_url == "http://localhost:8000"
        assert config.api_key is None
        assert config.timeout_seconds == 10800
        assert config.num_versions == 3

    def test_config_from_app_config(self):
        app_config = MagicMock()
        app_config.base_url = "http://remote:9000"
        app_config.api_key = "test-key"
        app_config.timeout_seconds = 600
        app_config.num_versions = 2

        config = MusicGenClientConfig.from_app_config(app_config)
        assert config.base_url == "http://remote:9000"
        assert config.api_key == "test-key"
        assert config.timeout_seconds == 600
        assert config.num_versions == 2

    async def test_client_requires_context(self):
        client = MusicGenClient()
        with pytest.raises(RuntimeError, match="Client not initialized"):
            _ = client.client

    async def test_health_check(self):
        client = MusicGenClient()
        mock_http = MagicMock()
        resp = self._make_resp(json_data={"status": "healthy", "device": "cuda"})
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http

        result = await client.health_check()
        assert result["status"] == "healthy"
        mock_http.get.assert_called_once_with("/health")

    async def test_submit_job(self):
        client = MusicGenClient()
        mock_http = MagicMock()
        resp = self._make_resp(json_data={"job_id": "abc123"})
        mock_http.post = AsyncMock(return_value=resp)
        client._client = mock_http

        job_id = await client._submit_job("/generate", {"prompt": "test"})
        assert job_id == "abc123"
        mock_http.post.assert_called_once_with("/generate", json={"prompt": "test"})

    async def test_wait_for_job_completed(self):
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()
        resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "result_urls": ["/files/result.wav"],
            }
        )
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http

        job = await client._wait_for_job("abc123")
        assert job["status"] == "completed"

    async def test_wait_for_job_failed(self):
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()
        resp = self._make_resp(
            json_data={
                "status": "failed",
                "error": "GPU out of memory",
            }
        )
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http

        with pytest.raises(RuntimeError, match="GPU out of memory"):
            await client._wait_for_job("abc123")

    async def test_wait_for_job_timeout(self):
        client = MusicGenClient(
            MusicGenClientConfig(
                timeout_seconds=0,
                poll_interval_seconds=0.01,
            )
        )
        mock_http = MagicMock()
        resp = self._make_resp(json_data={"status": "processing", "progress": 50})
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http

        with pytest.raises(TimeoutError):
            await client._wait_for_job("abc123")

    async def test_wait_for_job_progress_callback(self):
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()
        resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "progress_detail": {"step": "done"},
            }
        )
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http

        callback = MagicMock()
        await client._wait_for_job("abc123", progress_callback=callback)
        callback.assert_called_once_with("completed", 100, {"step": "done"})

    async def test_wait_for_job_retries_on_network_error(self):
        """Should retry transient network errors up to max_consecutive_errors."""
        import httpx

        client = MusicGenClient(
            MusicGenClientConfig(
                timeout_seconds=10,
                poll_interval_seconds=0.01,
            )
        )
        mock_http = MagicMock()
        success_resp = self._make_resp(json_data={"status": "completed", "progress": 100})
        mock_http.get = AsyncMock(
            side_effect=[httpx.NetworkError("connection reset"), success_resp]
        )
        client._client = mock_http

        job = await client._wait_for_job("abc123")
        assert job["status"] == "completed"
        assert mock_http.get.call_count == 2

    async def test_download_file(self):
        client = MusicGenClient()
        mock_http = MagicMock()
        resp = self._make_resp(content=b"fake audio data")
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.wav"
            result = await client._download_file("/files/test.wav", output)
            assert result == output
            assert output.read_bytes() == b"fake audio data"

    async def test_generate_music(self):
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()

        submit_resp = self._make_resp(json_data={"job_id": "gen123"})
        poll_resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "result_urls": ["/files/result.wav"],
            }
        )
        download_resp = self._make_resp(content=b"fake wav data")

        mock_http.post = AsyncMock(return_value=submit_resp)
        mock_http.get = AsyncMock(side_effect=[poll_resp, download_resp])
        client._client = mock_http

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await client.generate_music(
                prompt="upbeat pop",
                duration=30,
                output_dir=Path(tmpdir),
            )
            assert result.exists()
            assert result.suffix == ".wav"

    async def test_generate_music_clamps_duration(self):
        """Duration should be clamped to 10-120."""
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()

        submit_resp = self._make_resp(json_data={"job_id": "gen123"})
        poll_resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "result_urls": ["/f.wav"],
            }
        )
        download_resp = self._make_resp(content=b"data")

        mock_http.post = AsyncMock(return_value=submit_resp)
        mock_http.get = AsyncMock(side_effect=[poll_resp, download_resp])
        client._client = mock_http

        with tempfile.TemporaryDirectory() as tmpdir:
            await client.generate_music(prompt="test", duration=999, output_dir=Path(tmpdir))

        call_args = mock_http.post.call_args
        assert call_args[1]["json"]["duration"] == 120

    async def test_separate_stems_4_stem(self):
        """Test 4-stem separation (htdemucs)."""
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()

        submit_resp = self._make_resp(json_data={"job_id": "sep123"})
        poll_resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "result_urls": [
                    "/files/sep123_drums.wav",
                    "/files/sep123_bass.wav",
                    "/files/sep123_vocals.wav",
                    "/files/sep123_other.wav",
                ],
            }
        )
        self._make_resp(content=b"stem data")

        mock_http.post = AsyncMock(return_value=submit_resp)
        mock_http.get = AsyncMock(
            side_effect=[poll_resp] + [self._make_resp(content=b"stem data") for _ in range(4)]
        )
        client._client = mock_http

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "input.wav"
            audio_path.write_bytes(b"fake input")

            stems = await client.separate_stems(audio_path, output_dir=Path(tmpdir))
            assert stems.has_full_stems
            assert stems.drums is not None
            assert stems.bass is not None
            assert stems.vocals is not None
            assert stems.other is not None

    async def test_separate_stems_2_stem(self):
        """Test 2-stem separation (vocals/accompaniment)."""
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()

        submit_resp = self._make_resp(json_data={"job_id": "sep456"})
        poll_resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "result_urls": [
                    "/files/sep456_vocals.wav",
                    "/files/sep456_accompaniment.wav",
                ],
            }
        )

        mock_http.post = AsyncMock(return_value=submit_resp)
        mock_http.get = AsyncMock(
            side_effect=[poll_resp] + [self._make_resp(content=b"stem data") for _ in range(2)]
        )
        client._client = mock_http

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "input.wav"
            audio_path.write_bytes(b"fake input")

            stems = await client.separate_stems(audio_path, output_dir=Path(tmpdir))
            assert not stems.has_full_stems
            assert stems.vocals is not None
            assert stems.accompaniment is not None

    async def test_generate_soundtrack(self):
        client = MusicGenClient(MusicGenClientConfig(poll_interval_seconds=0.01))
        mock_http = MagicMock()

        submit_resp = self._make_resp(json_data={"job_id": "st789"})
        poll_resp = self._make_resp(
            json_data={
                "status": "completed",
                "progress": 100,
                "result_urls": ["/files/soundtrack.wav"],
            }
        )
        download_resp = self._make_resp(content=b"soundtrack data")

        mock_http.post = AsyncMock(return_value=submit_resp)
        mock_http.get = AsyncMock(side_effect=[poll_resp, download_resp])
        client._client = mock_http

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await client.generate_soundtrack(
                base_prompt="upbeat pop",
                scenes=[
                    {"mood": "happy", "duration": 30},
                    {"mood": "calm", "duration": 20},
                ],
                output_dir=Path(tmpdir),
            )
            assert result.exists()

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert payload["base_prompt"] == "upbeat pop"
        assert len(payload["scenes"]) == 2
        assert payload["use_beat_aligned_crossfade"]


# =============================================================================
# Music generation data model tests
# =============================================================================


class TestMusicDataModels:
    """Tests for GeneratedMusic, MusicGenerationResult, MusicStems."""

    def test_music_stems_cleanup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vocals = Path(tmpdir) / "vocals.wav"
            accompaniment = Path(tmpdir) / "accompaniment.wav"
            vocals.write_bytes(b"v")
            accompaniment.write_bytes(b"a")

            stems = MusicStems(vocals=vocals, accompaniment=accompaniment)
            assert not stems.has_full_stems
            stems.cleanup()
            assert not vocals.exists()
            assert not accompaniment.exists()

    def test_music_stems_4_stem(self):
        stems = MusicStems(
            vocals=Path("/tmp/v.wav"),
            drums=Path("/tmp/d.wav"),
            bass=Path("/tmp/b.wav"),
            other=Path("/tmp/o.wav"),
        )
        assert stems.has_full_stems

    def test_generated_music_cleanup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            full_mix = Path(tmpdir) / "mix.wav"
            full_mix.write_bytes(b"mix")

            music = GeneratedMusic(version_id=0, full_mix=full_mix)
            music.cleanup()
            assert not full_mix.exists()

    def test_music_generation_result_selected(self):
        versions = [
            GeneratedMusic(version_id=0, full_mix=Path("/tmp/v0.wav")),
            GeneratedMusic(version_id=1, full_mix=Path("/tmp/v1.wav")),
            GeneratedMusic(version_id=2, full_mix=Path("/tmp/v2.wav")),
        ]
        result = MusicGenerationResult(
            versions=versions,
            timeline=VideoTimeline(),
            mood="happy",
            selected_version=1,
        )
        assert result.selected == versions[1]

    def test_music_generation_result_none_selected(self):
        result = MusicGenerationResult(
            versions=[GeneratedMusic(version_id=0, full_mix=Path("/tmp/v0.wav"))],
            timeline=VideoTimeline(),
            mood="happy",
        )
        assert result.selected is None

    def test_music_generation_result_cleanup_unselected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            files = []
            versions = []
            for i in range(3):
                f = Path(tmpdir) / f"v{i}.wav"
                f.write_bytes(b"data")
                files.append(f)
                versions.append(GeneratedMusic(version_id=i, full_mix=f))

            result = MusicGenerationResult(
                versions=versions,
                timeline=VideoTimeline(),
                mood="happy",
                selected_version=1,
            )
            result.cleanup_unselected()

            assert not files[0].exists()  # Cleaned
            assert files[1].exists()  # Selected, kept
            assert not files[2].exists()  # Cleaned


# =============================================================================
# Prompt generation tests
# =============================================================================


class TestPromptGeneration:
    """Tests for music prompt generation."""

    def test_get_base_prompt_returns_known_prompt(self):
        prompt = _get_base_prompt(variation=0)
        assert prompt in MUSIC_PROMPTS

    def test_get_base_prompt_deterministic_with_seed(self):
        p1 = _get_base_prompt(variation=0, seed=42)
        p2 = _get_base_prompt(variation=0, seed=42)
        assert p1 == p2

    def test_get_base_prompt_varies_with_variation(self):
        # With enough variations, we should get different prompts
        prompts = {_get_base_prompt(variation=i, seed=1) for i in range(20)}
        assert len(prompts) > 1

    def test_all_prompts_mention_no_singing(self):
        """All prompts should indicate instrumental/no singing."""
        for prompt in MUSIC_PROMPTS:
            lower = prompt.lower()
            assert any(kw in lower for kw in ("no vocal", "no singing", "instrumental")), (
                f"Prompt missing instrumental indicator: {prompt}"
            )


# =============================================================================
# ACE-Step mood conversion tests
# =============================================================================


class TestACEStepMoodConversion:
    """Tests for mood-to-ACE-Step-prompt conversion."""

    def test_happy_mood(self):
        tags, lyrics = _mood_to_ace_prompt("happy cheerful")
        assert "pop" in tags
        assert "upbeat" in tags
        assert "instrumental" in tags
        assert lyrics.lower() == "[instrumental]"

    def test_energetic_mood(self):
        tags, lyrics = _mood_to_ace_prompt("energetic driving")
        assert "energetic" in tags or "future bass" in tags
        assert "instrumental" in tags

    def test_calm_mood(self):
        tags, lyrics = _mood_to_ace_prompt("calm peaceful")
        assert "ambient" in tags or "chill" in tags

    def test_unknown_mood_gets_default(self):
        tags, lyrics = _mood_to_ace_prompt("xyzzy")
        assert "pop" in tags  # Default fallback
        assert "instrumental" in tags

    def test_winter_seasonal(self):
        tags, lyrics = _mood_to_ace_prompt("winter holiday happy")
        assert "festive" in tags or "warm" in tags

    def test_summer_seasonal(self):
        tags, lyrics = _mood_to_ace_prompt("summer sunny upbeat")
        assert "tropical" in tags or "sunny" in tags

    def test_all_results_instrumental(self):
        """Every mood conversion should produce instrumental output."""
        moods = ["happy", "sad", "energetic", "calm", "dramatic", "playful", "unknown"]
        for mood in moods:
            tags, lyrics = _mood_to_ace_prompt(mood)
            assert "instrumental" in tags
            assert lyrics.lower() == "[instrumental]"


# =============================================================================
# ACEStepConfig tests
# =============================================================================


class TestACEStepConfig:
    """Tests for ACE-Step configuration."""

    def test_defaults(self):
        config = ACEStepConfig()
        assert config.mode == "lib"
        assert config.model_variant == "turbo"
        assert config.lm_model_size == "1.7B"
        assert config.use_lm
        assert config.bf16
        assert config.num_versions == 3
        assert config.timeout_seconds == 3600

    def test_pascal_gpu_config(self):
        """Config for Pascal GPUs should disable bf16."""
        config = ACEStepConfig(bf16=False, lm_model_size="0.6B")
        assert not config.bf16
        assert config.lm_model_size == "0.6B"

    def test_low_memory_config(self):
        """Config for low memory should disable LM."""
        config = ACEStepConfig(use_lm=False, lm_model_size="0.6B")
        assert not config.use_lm

    def test_api_mode(self):
        config = ACEStepConfig(mode="api", api_url="http://remote:7860")
        assert config.mode == "api"
        assert config.api_url == "http://remote:7860"


# =============================================================================
# ACEStepBackend tests
# =============================================================================


class TestACEStepBackend:
    """Tests for ACE-Step backend with mocks."""

    def test_name_includes_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))
        # Before any generation, mode detection hasn't run
        assert "ACE-Step" in backend.name

    async def test_is_available_no_lib_no_api(self):
        """Should return False when neither lib nor API is available."""
        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        with (
            patch(
                "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
                return_value=False,
            ),
            patch.object(backend, "_check_api", return_value=False),
        ):
            assert not await backend.is_available()

    async def test_is_available_lib_found(self):
        """Should return True when lib is importable."""
        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        with patch(
            "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
            return_value=True,
        ):
            assert await backend.is_available()

    async def test_health_check_api_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://test:7860"))
        with (
            patch(
                "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
                return_value=False,
            ),
            patch.object(backend, "_check_api", return_value=True),
        ):
            health = await backend.health_check()
            assert health["effective_mode"] == "api"
            assert health["available"]

    def test_is_ace_step_importable_returns_false(self):
        """Should return False when acestep isn't installed."""
        assert not _is_ace_step_importable()

    async def test_exit_releases_pipeline(self):
        backend = ACEStepBackend()
        backend._pipeline = MagicMock()
        await backend.__aexit__(None, None, None)
        assert backend._pipeline is None


# =============================================================================
# MusicGenBackend tests
# =============================================================================


class TestMusicGenBackend:
    """Tests for MusicGen backend wrapper."""

    def test_name(self):
        backend = MusicGenBackend()
        assert backend.name == "MusicGen"

    async def test_is_available_no_server(self):
        """Should return False when no server is running."""
        backend = MusicGenBackend(MusicGenClientConfig(base_url="http://localhost:99999"))
        assert not await backend.is_available()

    async def test_generate_requires_context(self):
        backend = MusicGenBackend()
        with pytest.raises(RuntimeError, match="not initialized"):
            await backend.generate(GenerationRequest())

    async def test_health_check_not_initialized(self):
        backend = MusicGenBackend()
        health = await backend.health_check()
        assert not health["available"]
        assert "not initialized" in health.get("error", "")

    async def test_generate_single_track(self):
        """Test single track generation through backend."""
        backend = MusicGenBackend()

        mock_client = AsyncMock()
        mock_client.generate_music = AsyncMock(return_value=Path("/tmp/result.wav"))
        backend._client = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            request = GenerationRequest(
                prompt="upbeat pop",
                duration_seconds=30,
                output_dir=Path(tmpdir),
            )
            result = await backend.generate(request)
            assert result.audio_path == Path("/tmp/result.wav")
            assert result.backend_name == "MusicGen"

            mock_client.generate_music.assert_called_once()

    async def test_generate_multi_scene(self):
        """Test multi-scene generation uses soundtrack endpoint."""
        backend = MusicGenBackend()

        mock_client = AsyncMock()
        mock_client.generate_soundtrack = AsyncMock(return_value=Path("/tmp/soundtrack.wav"))
        backend._client = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            request = GenerationRequest(
                prompt="upbeat pop",
                scenes=[
                    {"mood": "happy", "duration": 30},
                    {"mood": "calm", "duration": 20},
                ],
                output_dir=Path(tmpdir),
            )
            result = await backend.generate(request)
            assert result.audio_path == Path("/tmp/soundtrack.wav")
            mock_client.generate_soundtrack.assert_called_once()

    async def test_generate_with_stems(self):
        """Test generation + stem separation through backend."""
        backend = MusicGenBackend()

        mock_client = AsyncMock()
        mock_client.generate_music = AsyncMock(return_value=Path("/tmp/result.wav"))
        mock_stems = MusicStems(vocals=Path("/tmp/vocals.wav"), accompaniment=Path("/tmp/acc.wav"))
        mock_client.separate_stems = AsyncMock(return_value=mock_stems)
        backend._client = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            request = GenerationRequest(
                prompt="test",
                duration_seconds=30,
                output_dir=Path(tmpdir),
            )
            result, stems = await backend.generate_with_stems(request)
            assert result.audio_path == Path("/tmp/result.wav")
            assert stems.vocals == Path("/tmp/vocals.wav")


# =============================================================================
# Factory tests
# =============================================================================


class TestCreateGenerator:
    """Tests for the generator factory function."""

    def test_create_musicgen(self):
        gen = create_generator("musicgen")
        assert gen.name == "MusicGen"

    def test_create_ace_step(self):
        gen = create_generator("ace_step")
        assert "ACE-Step" in gen.name

    def test_create_ace_step_hyphen(self):
        """Should accept both ace_step and ace-step."""
        gen = create_generator("ace-step")
        assert "ACE-Step" in gen.name

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown music generation backend"):
            create_generator("nonexistent")

    def test_case_insensitive(self):
        gen = create_generator("MusicGen")
        assert gen.name == "MusicGen"

    def test_create_ace_step_with_config(self):
        config = ACEStepConfig(model_variant="base", use_lm=False)
        gen = create_generator("ace_step", config=config)
        assert "ACE-Step" in gen.name

    def test_create_musicgen_with_app_config(self):
        """Factory should convert app config to client config."""
        app_config = MagicMock()
        app_config.base_url = "http://remote:9000"
        app_config.api_key = "key"
        app_config.timeout_seconds = 300
        app_config.num_versions = 1

        gen = create_generator("musicgen", config=app_config)
        assert gen.name == "MusicGen"


# =============================================================================
# App config integration tests
# =============================================================================


class TestConfigIntegration:
    """Tests for ACE-Step config in the main app config."""

    def test_ace_step_config_exists(self):
        from immich_memories.config import Config

        config = Config()
        assert hasattr(config, "ace_step")
        assert not config.ace_step.enabled
        assert config.ace_step.mode == "lib"
        assert config.ace_step.model_variant == "turbo"

    def test_music_source_accepts_ace_step(self):
        from immich_memories.config import AudioConfig

        audio = AudioConfig(music_source="ace_step")
        assert audio.music_source == "ace_step"

    def test_music_source_still_accepts_musicgen(self):
        from immich_memories.config import AudioConfig

        audio = AudioConfig(music_source="musicgen")
        assert audio.music_source == "musicgen"

    def test_ace_step_env_vars(self):
        """ACE-Step config should support env var overrides."""
        import os

        from immich_memories.config import get_config, set_config

        # Reset global config
        set_config(None)

        env_patch = {
            "ACE_STEP_ENABLED": "true",
            "ACE_STEP_MODE": "api",
            "ACE_STEP_API_URL": "http://gpu-server:7860",
        }
        with patch.dict(os.environ, env_patch):
            config = get_config()
            assert config.ace_step.enabled
            assert config.ace_step.mode == "api"
            assert config.ace_step.api_url == "http://gpu-server:7860"

        # Reset again
        set_config(None)


# =============================================================================
# Integration test markers (for real servers)
# =============================================================================

gpu = pytest.mark.skipif(
    True,  # Always skip unless explicitly enabled
    reason="GPU integration tests require MUSICGEN_INTEGRATION=1 or ACE_STEP_INTEGRATION=1",
)


@gpu
class TestMusicGenIntegration:
    """Integration tests that require a running MusicGen server.

    Run with: MUSICGEN_INTEGRATION=1 pytest tests/test_generators.py -k Integration -v
    """

    async def test_health_check_real(self):
        async with MusicGenClient() as client:
            health = await client.health_check()
            assert health["status"] == "healthy"

    async def test_generate_short_clip(self):
        async with MusicGenClient() as client:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = await client.generate_music(
                    prompt="upbeat lo-fi hip hop",
                    duration=10,
                    output_dir=Path(tmpdir),
                )
                assert result.exists()
                assert result.stat().st_size > 1000


@gpu
class TestACEStepIntegration:
    """Integration tests for ACE-Step.

    Run with: ACE_STEP_INTEGRATION=1 pytest tests/test_generators.py -k Integration -v
    """

    async def test_health_check_real(self):
        backend = ACEStepBackend()
        health = await backend.health_check()
        assert health["available"]
