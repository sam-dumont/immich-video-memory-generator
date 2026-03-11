"""Tests for audio module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from immich_memories.audio.mixer import (
    DuckingConfig,
    MixConfig,
)
from immich_memories.audio.mood_analyzer import (
    OllamaMoodAnalyzer,
    OpenAIMoodAnalyzer,
    VideoMood,
)
from immich_memories.audio.music_sources import (
    LocalMusicSource,
    MusicTrack,
    get_music_source,
)


class TestMusicTrack:
    """Tests for MusicTrack class."""

    def test_basic_track(self):
        """Test basic track creation."""
        track = MusicTrack(
            id="123",
            title="Test Song",
            artist="Test Artist",
            duration_seconds=180.0,
            url="https://example.com/song.mp3",
        )
        assert track.id == "123"
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.duration_seconds == 180.0

    def test_default_values(self):
        """Test default values."""
        track = MusicTrack(
            id="123",
            title="Test",
            artist="Artist",
            duration_seconds=60.0,
            url="https://example.com/song.mp3",
        )
        assert track.preview_url is None
        assert track.tags == []
        assert track.mood is None
        assert track.genre is None
        assert track.license == "royalty-free"
        assert track.source == "unknown"

    def test_cache_filename(self):
        """Test cache filename generation."""
        track = MusicTrack(
            id="123",
            title="My Song Title",
            artist="Artist",
            duration_seconds=60.0,
            url="https://example.com/song.mp3",
            source="local",
        )
        filename = track.cache_filename
        assert filename.endswith(".mp3")
        assert "My_Song_Title" in filename
        # Hash should be consistent
        assert track.cache_filename == filename

    def test_cache_filename_special_chars(self):
        """Test cache filename with special characters."""
        track = MusicTrack(
            id="456",
            title="Song: With $pecial Ch@rs!",
            artist="Artist",
            duration_seconds=60.0,
            url="https://example.com/song.mp3",
        )
        filename = track.cache_filename
        # Should only contain alphanumeric and underscores
        assert all(c.isalnum() or c in "_." for c in filename)


class TestDuckingConfig:
    """Tests for DuckingConfig class."""

    def test_default_values(self):
        """Test default ducking configuration."""
        config = DuckingConfig()
        assert config.threshold == 0.02
        assert config.ratio == 4.0
        assert config.attack_ms == 100.0
        assert config.release_ms == 2500.0
        assert config.makeup_db == 0.0
        assert config.music_volume_db == -6.0

    def test_custom_values(self):
        """Test custom ducking configuration."""
        config = DuckingConfig(
            threshold=0.05,
            ratio=8.0,
            music_volume_db=-12.0,
        )
        assert config.threshold == 0.05
        assert config.ratio == 8.0
        assert config.music_volume_db == -12.0


class TestMixConfig:
    """Tests for MixConfig class."""

    def test_default_values(self):
        """Test default mix configuration."""
        config = MixConfig(ducking=DuckingConfig())
        assert config.fade_in_seconds == 2.0
        assert config.fade_out_seconds == 3.0
        assert config.music_starts_at == 0.0
        assert config.normalize_audio is True

    def test_custom_values(self):
        """Test custom mix configuration."""
        config = MixConfig(
            ducking=DuckingConfig(),
            fade_in_seconds=1.0,
            fade_out_seconds=2.0,
            normalize_audio=False,
        )
        assert config.fade_in_seconds == 1.0
        assert config.fade_out_seconds == 2.0
        assert config.normalize_audio is False


class TestVideoMood:
    """Tests for VideoMood class."""

    def test_basic_mood(self):
        """Test basic mood creation."""
        mood = VideoMood(
            primary_mood="happy",
            energy_level="high",
            tempo_suggestion="fast",
        )
        assert mood.primary_mood == "happy"
        assert mood.energy_level == "high"
        assert mood.tempo_suggestion == "fast"

    def test_default_values(self):
        """Test default values."""
        mood = VideoMood(primary_mood="calm")
        assert mood.secondary_mood is None
        assert mood.energy_level == "medium"
        assert mood.tempo_suggestion == "medium"
        assert mood.genre_suggestions == []
        assert mood.color_palette == "neutral"
        assert mood.confidence == 0.8

    def test_to_search_params(self):
        """Test conversion to search parameters."""
        mood = VideoMood(
            primary_mood="energetic",
            genre_suggestions=["electronic", "pop"],
            tempo_suggestion="fast",
        )
        params = mood.to_search_params()
        assert params["mood"] == "energetic"
        assert params["genre"] == "electronic"
        assert params["tempo"] == "fast"

    def test_to_search_params_no_genre(self):
        """Test search params without genre."""
        mood = VideoMood(primary_mood="calm")
        params = mood.to_search_params()
        assert params["mood"] == "calm"
        assert params["genre"] is None


class TestLocalMusicSource:
    """Tests for LocalMusicSource class."""

    def test_supported_extensions(self):
        """Test supported extensions are defined."""
        assert ".mp3" in LocalMusicSource.SUPPORTED_EXTENSIONS
        assert ".m4a" in LocalMusicSource.SUPPORTED_EXTENSIONS
        assert ".wav" in LocalMusicSource.SUPPORTED_EXTENSIONS

    def test_nonexistent_directory(self):
        """Test handling of nonexistent directory."""
        source = LocalMusicSource(Path("/nonexistent/path"))
        tracks = source._scan_directory()
        assert tracks == []

    @pytest.mark.asyncio
    async def test_search_empty(self):
        """Test search on empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = LocalMusicSource(Path(tmpdir))
            tracks = await source.search()
            assert tracks == []

    @pytest.mark.asyncio
    async def test_download_existing(self):
        """Test download returns local path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            test_file = Path(tmpdir) / "test.mp3"
            test_file.write_bytes(b"test content")

            track = MusicTrack(
                id="test",
                title="Test",
                artist="Artist",
                duration_seconds=60,
                url=f"file://{test_file}",
                local_path=test_file,
            )

            source = LocalMusicSource(Path(tmpdir))
            result = await source.download(track, Path(tmpdir))
            assert result == test_file


class TestGetMusicSource:
    """Tests for get_music_source function."""

    def test_local_source(self):
        """Test creating local source."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = get_music_source("local", music_dir=Path(tmpdir))
            assert isinstance(source, LocalMusicSource)

    def test_local_source_missing_dir(self):
        """Test local source requires directory."""
        with pytest.raises(ValueError, match="music_dir required"):
            get_music_source("local")

    def test_unknown_source(self):
        """Test unknown source type."""
        with pytest.raises(ValueError, match="Unknown music source"):
            get_music_source("unknown")


class TestOllamaMoodAnalyzer:
    """Tests for OllamaMoodAnalyzer class."""

    def test_initialization(self):
        """Test analyzer initialization."""
        analyzer = OllamaMoodAnalyzer(model="llava", base_url="http://localhost:11434")
        assert analyzer.model == "llava"
        assert analyzer.base_url == "http://localhost:11434"

    def test_base_url_trailing_slash(self):
        """Test trailing slash is removed from URL."""
        analyzer = OllamaMoodAnalyzer(base_url="http://localhost:11434/")
        assert analyzer.base_url == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_is_available_mock(self):
        """Test availability check with mock."""
        analyzer = OllamaMoodAnalyzer()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)
        analyzer._client = mock_client

        available = await analyzer.is_available()

        assert available is True

    def test_parse_mood_response_valid(self):
        """Test parsing valid mood response."""
        analyzer = OllamaMoodAnalyzer()
        response = """
        {
            "primary_mood": "happy",
            "energy_level": "high",
            "tempo_suggestion": "fast",
            "genre_suggestions": ["pop", "electronic"],
            "confidence": 0.9
        }
        """
        mood = analyzer._parse_mood_response(response)
        assert mood.primary_mood == "happy"
        assert mood.energy_level == "high"
        assert mood.tempo_suggestion == "fast"
        assert mood.confidence == 0.9

    def test_parse_mood_response_markdown(self):
        """Test parsing response with markdown code block."""
        analyzer = OllamaMoodAnalyzer()
        response = """
        ```json
        {
            "primary_mood": "calm",
            "energy_level": "low"
        }
        ```
        """
        mood = analyzer._parse_mood_response(response)
        assert mood.primary_mood == "calm"

    def test_parse_mood_response_invalid(self):
        """Test parsing invalid response returns default."""
        analyzer = OllamaMoodAnalyzer()
        mood = analyzer._parse_mood_response("not valid json")
        assert mood.primary_mood == "calm"
        assert mood.confidence == 0.3


class TestOpenAIMoodAnalyzer:
    """Tests for OpenAIMoodAnalyzer class."""

    def test_initialization(self):
        """Test analyzer initialization."""
        analyzer = OpenAIMoodAnalyzer(api_key="test_key", model="gpt-4o-mini")
        assert analyzer.api_key == "test_key"
        assert analyzer.model == "gpt-4o-mini"

    def test_base_url_trailing_slash(self):
        """Test trailing slash is removed from URL."""
        analyzer = OpenAIMoodAnalyzer(api_key="key", base_url="https://api.openai.com/v1/")
        assert analyzer.base_url == "https://api.openai.com/v1"

    def test_parse_mood_response_inherits(self):
        """Test that parse_mood_response works from base class."""
        analyzer = OpenAIMoodAnalyzer(api_key="test")
        response = '{"primary_mood": "energetic"}'
        mood = analyzer._parse_mood_response(response)
        assert mood.primary_mood == "energetic"


class TestAudioCategories:
    """Tests for audio category classification."""

    def test_classify_laughter(self):
        """Test laughter class names map to laughter category."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Laughter") == "laughter"
        assert classify_audio_event("Giggle") == "laughter"
        assert classify_audio_event("Chuckle, chortle") == "laughter"

    def test_classify_baby_sounds(self):
        """Test baby class names map to baby category."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Baby laughter") == "baby"
        assert classify_audio_event("Baby cry, infant cry") == "baby"

    def test_classify_speech(self):
        """Test speech class names map to speech category."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Speech") == "speech"
        assert classify_audio_event("Conversation") == "speech"

    def test_classify_singing(self):
        """Test singing is its own category, not speech or music."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Singing") == "singing"
        assert classify_audio_event("Choir") == "singing"

    def test_classify_engine(self):
        """Test engine/vehicle sounds are detected."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Engine") == "engine"
        assert classify_audio_event("Motor vehicle (road)") == "engine"
        assert classify_audio_event("Race car, racing car") == "engine"
        assert classify_audio_event("Motorcycle") == "engine"

    def test_classify_nature(self):
        """Test nature sounds are detected."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Bird") == "nature"
        assert classify_audio_event("Rain") == "nature"
        assert classify_audio_event("Thunder") == "nature"

    def test_classify_animals(self):
        """Test animal sounds are detected."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Dog") == "animals"
        assert classify_audio_event("Cat") == "animals"

    def test_classify_unknown(self):
        """Test unknown class returns None."""
        from immich_memories.audio.audio_models import classify_audio_event

        assert classify_audio_event("Silence") is None
        assert classify_audio_event("White noise") is None

    def test_detected_categories_in_result(self):
        """Test AudioAnalysisResult tracks detected categories."""
        from immich_memories.audio.audio_models import AudioAnalysisResult

        result = AudioAnalysisResult(detected_categories={"laughter", "engine"})
        assert "laughter" in result.detected_categories
        assert "engine" in result.detected_categories
        assert "speech" not in result.detected_categories


class TestPANNsAnalysis:
    """Tests for PANNs-based audio classification mixin."""

    def test_classify_frame_laughter(self):
        """Test frame classification detects laughter."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin.min_confidence = 0.3
        mixin.laughter_confidence = 0.2

        meets, category = mixin._classify_frame("Laughter", 0.5)
        assert meets is True
        assert category == "laughter"

    def test_classify_frame_laughter_low_threshold(self):
        """Test laughter uses lower confidence threshold."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin.min_confidence = 0.3
        mixin.laughter_confidence = 0.2

        # Score 0.25 is below min_confidence but above laughter_confidence
        meets, category = mixin._classify_frame("Laughter", 0.25)
        assert meets is True
        assert category == "laughter"

    def test_classify_frame_below_threshold(self):
        """Test frame below all thresholds is rejected."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin.min_confidence = 0.3
        mixin.laughter_confidence = 0.2

        meets, category = mixin._classify_frame("Laughter", 0.1)
        assert meets is False

    def test_classify_frame_engine(self):
        """Test frame classification detects engine sounds."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin.min_confidence = 0.3
        mixin.laughter_confidence = 0.2

        meets, category = mixin._classify_frame("Motor vehicle (road)", 0.5)
        assert meets is True
        assert category == "engine"

    def test_classify_frame_singing(self):
        """Test singing is detected as its own category."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin.min_confidence = 0.3
        mixin.laughter_confidence = 0.2

        meets, category = mixin._classify_frame("Singing", 0.5)
        assert meets is True
        assert category == "singing"

    def test_collect_events_from_scores(self):
        """Test event collection from PANNs score frames."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin.min_confidence = 0.3
        mixin.laughter_confidence = 0.2

        # Create fake scores: 10 frames, 5 classes
        # Class names: [Laughter, Speech, Music, Silence, Engine]
        class_names = ["Laughter", "Speech", "Music", "Silence", "Engine"]
        scores = np.zeros((10, 5))
        # Frames 2-4: laughter dominant
        scores[2, 0] = 0.8
        scores[3, 0] = 0.7
        scores[4, 0] = 0.6
        # Frames 7-8: speech dominant
        scores[7, 1] = 0.5
        scores[8, 1] = 0.4

        events, energy, categories = mixin._collect_events(
            scores, class_names, frame_duration=1.0, audio_length_samples=320000
        )

        assert len(events) >= 2
        assert "laughter" in categories
        assert "speech" in categories

        # Check laughter event timing
        laugh_events = [e for e in events if "Laughter" in e.event_class]
        assert len(laugh_events) == 1
        assert laugh_events[0].start_time == pytest.approx(2.0)

    def test_panns_mixin_check_available_import_error(self):
        """Test graceful fallback when panns_inference is not installed."""
        from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

        mixin = PANNsAnalysisMixin.__new__(PANNsAnalysisMixin)
        mixin._panns_available = None
        mixin._panns_model = None
        mixin._class_names = None

        with patch.dict("sys.modules", {"panns_inference": None}):
            result = mixin._check_panns_available()
            assert result is False
            assert mixin._panns_available is False
