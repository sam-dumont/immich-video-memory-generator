"""Tests for AudioMixer class — construction and configuration, no FFmpeg."""

from __future__ import annotations

from pathlib import Path

from immich_memories.audio.mixer import DuckingConfig
from immich_memories.audio.mixer_class import AudioMixer

# ---------------------------------------------------------------------------
# AudioMixer construction
# ---------------------------------------------------------------------------


class TestAudioMixerInit:
    def test_default_construction(self, tmp_path: Path):
        mixer = AudioMixer(cache_dir=tmp_path / "music")
        assert mixer.cache_dir == tmp_path / "music"
        assert mixer.cache_dir.exists()

    def test_default_ducking_config(self, tmp_path: Path):
        mixer = AudioMixer(cache_dir=tmp_path / "music")
        assert mixer.ducking_config.threshold == 0.02
        assert mixer.ducking_config.ratio == 4.0

    def test_custom_ducking_config(self, tmp_path: Path):
        custom = DuckingConfig(threshold=0.1, ratio=8.0, music_volume_db=-12.0)
        mixer = AudioMixer(ducking_config=custom, cache_dir=tmp_path / "music")
        assert mixer.ducking_config.threshold == 0.1
        assert mixer.ducking_config.ratio == 8.0
        assert mixer.ducking_config.music_volume_db == -12.0

    def test_default_cache_dir_when_none(self):
        mixer = AudioMixer()
        expected = Path.home() / ".cache" / "immich-memories" / "music"
        assert mixer.cache_dir == expected

    def test_cache_dir_created_on_init(self, tmp_path: Path):
        cache_dir = tmp_path / "nested" / "music"
        assert not cache_dir.exists()
        mixer = AudioMixer(cache_dir=cache_dir)
        assert mixer.cache_dir.exists()
