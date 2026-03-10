"""Tests for audio module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
