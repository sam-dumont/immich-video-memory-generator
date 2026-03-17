"""Tests for audio mixer helpers — pure logic, no FFmpeg."""

from __future__ import annotations

from immich_memories.audio.mixer import (
    DuckingConfig,
    MixConfig,
    _build_ducking_filter,
    _db_to_linear,
)
from immich_memories.audio.mixer_helpers import StemDuckingLevels

# ---------------------------------------------------------------------------
# _db_to_linear
# ---------------------------------------------------------------------------


class TestDbToLinear:
    def test_zero_db_is_one(self):
        assert abs(_db_to_linear(0.0) - 1.0) < 1e-9

    def test_positive_db_increases(self):
        assert _db_to_linear(6.0) > 1.0

    def test_negative_db_clamped_to_min(self):
        # -60dB = 0.001, clamped to min_val=1.0
        assert _db_to_linear(-60.0) == 1.0

    def test_large_positive_clamped_to_max(self):
        # 100dB = 100000, clamped to 64
        assert _db_to_linear(100.0) == 64.0

    def test_custom_min_max(self):
        result = _db_to_linear(0.0, min_val=0.5, max_val=2.0)
        assert abs(result - 1.0) < 1e-9

    def test_20db_is_10(self):
        # 10^(20/20) = 10
        result = _db_to_linear(20.0)
        assert abs(result - 10.0) < 1e-6


# ---------------------------------------------------------------------------
# DuckingConfig defaults
# ---------------------------------------------------------------------------


class TestDuckingConfig:
    def test_defaults(self):
        config = DuckingConfig()
        assert config.threshold == 0.02
        assert config.ratio == 4.0
        assert config.attack_ms == 100.0
        assert config.release_ms == 2500.0
        assert config.makeup_db == 0.0
        assert config.music_volume_db == -6.0

    def test_custom_values(self):
        config = DuckingConfig(threshold=0.05, ratio=8.0)
        assert config.threshold == 0.05
        assert config.ratio == 8.0


# ---------------------------------------------------------------------------
# MixConfig defaults
# ---------------------------------------------------------------------------


class TestMixConfig:
    def test_defaults(self):
        config = MixConfig(ducking=DuckingConfig())
        assert config.fade_in_seconds == 2.0
        assert config.fade_out_seconds == 3.0
        assert config.music_starts_at == 0.0
        assert config.normalize_audio is True


# ---------------------------------------------------------------------------
# _build_ducking_filter — pure string building
# ---------------------------------------------------------------------------


class TestBuildDuckingFilter:
    def test_contains_music_volume(self):
        ducking = DuckingConfig(music_volume_db=-8.0)
        config = MixConfig(ducking=ducking)
        parts = _build_ducking_filter(config, ducking, video_duration=60.0)
        joined = ";".join(parts)
        assert "volume=-8.0dB" in joined

    def test_contains_sidechain_params(self):
        ducking = DuckingConfig(threshold=0.03, ratio=6.0)
        config = MixConfig(ducking=ducking)
        parts = _build_ducking_filter(config, ducking, video_duration=30.0)
        joined = ";".join(parts)
        assert "threshold=0.03" in joined
        assert "ratio=6.0" in joined

    def test_fade_in_present(self):
        ducking = DuckingConfig()
        config = MixConfig(ducking=ducking, fade_in_seconds=3.0)
        parts = _build_ducking_filter(config, ducking, video_duration=60.0)
        joined = ";".join(parts)
        assert "afade=t=in" in joined

    def test_fade_out_present(self):
        ducking = DuckingConfig()
        config = MixConfig(ducking=ducking, fade_out_seconds=5.0)
        parts = _build_ducking_filter(config, ducking, video_duration=60.0)
        joined = ";".join(parts)
        assert "afade=t=out" in joined
        assert "st=55.0" in joined  # 60 - 5 = 55

    def test_no_fade_when_zero(self):
        ducking = DuckingConfig()
        config = MixConfig(ducking=ducking, fade_in_seconds=0, fade_out_seconds=0)
        parts = _build_ducking_filter(config, ducking, video_duration=30.0)
        joined = ";".join(parts)
        assert "afade" not in joined

    def test_normalize_audio_uses_loudnorm(self):
        ducking = DuckingConfig()
        config = MixConfig(ducking=ducking, normalize_audio=True)
        parts = _build_ducking_filter(config, ducking, video_duration=30.0)
        joined = ";".join(parts)
        assert "loudnorm" in joined

    def test_no_normalize_uses_acopy(self):
        ducking = DuckingConfig()
        config = MixConfig(ducking=ducking, normalize_audio=False)
        parts = _build_ducking_filter(config, ducking, video_duration=30.0)
        joined = ";".join(parts)
        assert "acopy" in joined

    def test_output_is_mixed(self):
        ducking = DuckingConfig()
        config = MixConfig(ducking=ducking)
        parts = _build_ducking_filter(config, ducking, video_duration=30.0)
        joined = ";".join(parts)
        assert "[mixed]" in joined


# ---------------------------------------------------------------------------
# StemDuckingLevels defaults
# ---------------------------------------------------------------------------


class TestStemDuckingLevels:
    def test_defaults(self):
        levels = StemDuckingLevels()
        assert levels.drums_db == -3.0
        assert levels.bass_db == -6.0
        assert levels.vocals_db == -12.0
        assert levels.other_db == -9.0

    def test_custom_levels(self):
        levels = StemDuckingLevels(drums_db=-1.0, vocals_db=-20.0)
        assert levels.drums_db == -1.0
        assert levels.vocals_db == -20.0
