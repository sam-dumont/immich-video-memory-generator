"""Tests for privacy/demo mode: blur video + mute speech."""

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
    """Video filter includes gaussian blur when privacy mode is on."""

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

    def test_blur_filter_present_when_privacy_on(self):
        fb, ctx = self._make_filter_builder(privacy_mode=True)
        clip = AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0)
        result = fb.build_clip_video_filter(0, clip, ctx)
        assert "gblur=sigma=30" in result

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


class TestPrivacyAudioMute:
    """Audio is replaced with silence for speech clips in privacy mode."""

    def _make_filter_builder(self, privacy_mode: bool = False):
        settings = AssemblySettings(privacy_mode=privacy_mode)
        # WHY: mock prober + face_center_fn — audio filter tests don't touch video I/O
        prober = MagicMock()
        face_center_fn = MagicMock(return_value=None)
        return FilterBuilder(settings, prober, face_center_fn)

    def test_speech_clip_muted_when_privacy_on(self):
        fb = self._make_filter_builder(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=True),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        # Should use anullsrc (silence) instead of real audio
        assert "anullsrc" in filter_parts[0]
        # Should NOT reference the input audio stream
        assert "[0:a]" not in filter_parts[0]

    def test_non_speech_clip_keeps_audio_when_privacy_on(self):
        fb = self._make_filter_builder(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=False),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        # Should keep real audio (references input stream)
        assert "[0:a]" in filter_parts[0]

    def test_speech_clip_keeps_audio_when_privacy_off(self):
        fb = self._make_filter_builder(privacy_mode=False)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=True),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        # Should keep real audio when privacy is off
        assert "[0:a]" in filter_parts[0]
