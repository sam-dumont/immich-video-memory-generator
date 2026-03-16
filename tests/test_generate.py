"""Tests for the generate_memory() orchestrator and helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import (
    GenerationError,
    GenerationParams,
    _build_assembly_settings,
    _clip_location_name,
    _report,
    _total_clip_duration,
    assets_to_clips,
)
from tests.conftest import make_asset, make_clip


class TestGenerationParams:
    def test_defaults(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        assert params.transition == "crossfade"
        assert params.upload_enabled is False
        assert params.music_path is None
        assert params.privacy_mode is False

    def test_progress_callback_called(self):
        calls = []
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            progress_callback=lambda phase, pct, msg: calls.append((phase, pct, msg)),
        )
        _report(params, "test", 0.5, "halfway")
        assert len(calls) == 1
        assert calls[0] == ("test", 0.5, "halfway")

    def test_no_callback_is_noop(self):
        params = GenerationParams(clips=[], output_path=Path("/tmp/out.mp4"), config=Config())
        _report(params, "test", 0.5, "halfway")  # Should not raise


class TestGenerationError:
    def test_is_exception(self):
        with pytest.raises(GenerationError, match="test error"):
            raise GenerationError("test error")


class TestBuildAssemblySettings:
    def test_crossfade_transition(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            transition="crossfade",
            transition_duration=0.3,
        )
        settings = _build_assembly_settings(params, [])
        from immich_memories.processing.assembly_config import TransitionType

        assert settings.transition == TransitionType.CROSSFADE
        assert settings.transition_duration == 0.3

    def test_cut_transition(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            transition="cut",
        )
        settings = _build_assembly_settings(params, [])
        from immich_memories.processing.assembly_config import TransitionType

        assert settings.transition == TransitionType.CUT

    def test_explicit_resolution(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            output_resolution="1080p",
        )
        settings = _build_assembly_settings(params, [])
        assert settings.target_resolution == (1920, 1080)
        assert settings.auto_resolution is False

    def test_auto_resolution(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        settings = _build_assembly_settings(params, [])
        assert settings.auto_resolution is True

    def test_privacy_mode_passed_through(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            privacy_mode=True,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.privacy_mode is True


class TestAssetsToClips:
    def test_filters_short_clips(self):
        assets = [
            make_asset(duration="0:00:00.500"),  # Too short
            make_asset("a2", duration="0:00:05.000"),  # OK
            make_asset("a3", duration="0:00:10.000"),  # OK
        ]
        clips = assets_to_clips(assets)
        assert len(clips) == 2

    def test_empty_assets(self):
        assert assets_to_clips([]) == []

    def test_preserves_duration(self):
        assets = [make_asset(duration="0:00:07.500")]
        clips = assets_to_clips(assets)
        assert clips[0].duration_seconds == 7.5


class TestTotalClipDuration:
    def test_sums_durations(self):
        clips = [make_clip(duration=3.0), make_clip("c2", duration=5.0)]
        params = GenerationParams(
            clips=clips,
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        assert _total_clip_duration(params) == 8


class TestClipLocationName:
    def test_returns_city_and_country(self):
        exif = type("Exif", (), {"city": "Paris", "country": "France"})()
        assert _clip_location_name(exif) == "Paris, France"

    def test_returns_country_if_no_city(self):
        exif = type("Exif", (), {"city": None, "country": "US"})()
        assert _clip_location_name(exif) == "US"

    def test_returns_none_if_no_location(self):
        exif = type("Exif", (), {"city": None, "country": None})()
        assert _clip_location_name(exif) is None

    def test_returns_none_for_none_exif(self):
        assert _clip_location_name(None) is None
