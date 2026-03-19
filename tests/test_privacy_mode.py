"""Tests for privacy/demo mode: heavy blur video + muffle audio."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
)
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.filter_builder import FilterBuilder


class TestPrivacyModeConfig:
    """AssemblySettings has privacy_mode flag."""

    def test_default_is_false(self):
        settings = AssemblySettings()
        assert not settings.privacy_mode

    def test_can_be_enabled(self):
        settings = AssemblySettings(privacy_mode=True)
        assert settings.privacy_mode


class TestPrivacyVideoBlur:
    """Video filter includes strong gaussian blur when privacy mode is on."""

    def _make_filter_builder(self, privacy_mode: bool = False):
        """Create a FilterBuilder with mocked dependencies."""
        settings = AssemblySettings(privacy_mode=privacy_mode)
        # WHY: mock prober — FilterBuilder calls ffprobe for resolution; unit tests skip I/O
        prober = MagicMock()
        prober.get_video_resolution = MagicMock(return_value=None)
        # WHY: mock face_center_fn — face detection requires real video frames
        face_center_fn = MagicMock(return_value=None)
        fb = FilterBuilder(settings, prober, face_center_fn)

        ctx = AssemblyContext(
            target_w=1920,
            target_h=1080,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None],
            clip_primaries=[None],
            colorspace_filter="",
            target_fps=60,
            fade_duration=0.5,
        )
        return fb, ctx

    def test_blur_strong_enough_when_privacy_on(self):
        """Blur must be heavy enough that faces/people are unrecognizable."""
        fb, ctx = self._make_filter_builder(privacy_mode=True)
        clip = AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0)
        result = fb.build_clip_video_filter(0, clip, ctx)
        assert "gblur=sigma=80" in result

    def test_no_blur_when_privacy_off(self):
        fb, ctx = self._make_filter_builder(privacy_mode=False)
        clip = AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0)
        result = fb.build_clip_video_filter(0, clip, ctx)
        assert "gblur" not in result

    def test_no_blur_for_title_screens(self):
        """Title screens should never be blurred, even with privacy on."""
        fb, ctx = self._make_filter_builder(privacy_mode=True)
        clip = AssemblyClip(path=Path("/tmp/title.mp4"), duration=3.0, is_title_screen=True)
        result = fb.build_clip_video_filter(0, clip, ctx)
        assert "gblur" not in result

    def test_skip_privacy_blur_prevents_double_blur(self):
        """Batch merge must not re-blur already-blurred intermediates."""
        fb, ctx = self._make_filter_builder(privacy_mode=True)
        # Intermediate batch clip (already blurred in first pass)
        clip = AssemblyClip(path=Path("/tmp/batch_000.mp4"), duration=10.0)
        result = fb.build_clip_video_filter(0, clip, ctx, skip_privacy_blur=True)
        assert "gblur" not in result


class TestPrivacyAudioMuffle:
    """Privacy mode muffles ALL audio — keeps cadence but makes speech unintelligible."""

    def _make_filter_builder(self, privacy_mode: bool = False):
        settings = AssemblySettings(privacy_mode=privacy_mode)
        # WHY: mock prober + face_center_fn — audio filter tests don't touch video I/O
        prober = MagicMock()
        face_center_fn = MagicMock(return_value=None)
        return FilterBuilder(settings, prober, face_center_fn)

    def test_all_clips_muffled_when_privacy_on(self):
        """ALL clips get muffled audio in privacy mode, not just speech-detected."""
        fb = self._make_filter_builder(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=True),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        # Should apply lowpass to make speech unintelligible
        assert "lowpass" in filter_parts[0]
        # Should keep the audio stream (not silence) so ducking/ambient works
        assert "[0:a]" in filter_parts[0]

    def test_non_speech_clip_also_muffled_when_privacy_on(self):
        """Privacy mode must muffle ALL audio — speech detection is unreliable."""
        fb = self._make_filter_builder(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=False),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        assert "lowpass" in filter_parts[0]
        assert "[0:a]" in filter_parts[0]

    def test_title_screens_stay_silent_when_privacy_on(self):
        """Title screens have no audio source — keep anullsrc."""
        fb = self._make_filter_builder(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/title.mp4"), duration=3.0, is_title_screen=True),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        assert "anullsrc" in filter_parts[0]

    def test_normal_audio_when_privacy_off(self):
        fb = self._make_filter_builder(privacy_mode=False)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=True),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        # Should keep real audio when privacy is off
        assert "[0:a]" in filter_parts[0]
        assert "lowpass" not in filter_parts[0]
