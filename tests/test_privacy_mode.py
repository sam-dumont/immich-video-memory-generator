"""Tests for privacy/demo mode: blur video + mute speech."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
)


class TestPrivacyModeConfig:
    """AssemblySettings has privacy_mode flag."""

    def test_default_is_false(self):
        settings = AssemblySettings()
        assert settings.privacy_mode is False

    def test_can_be_enabled(self):
        settings = AssemblySettings(privacy_mode=True)
        assert settings.privacy_mode is True


class TestPrivacyVideoBlur:
    """Video filter includes gaussian blur when privacy mode is on."""

    def _make_assembler(self, privacy_mode: bool = False):
        """Create a minimal assembler with mocked dependencies."""
        from immich_memories.processing.assembler_helpers import (
            AssemblerHelpersMixin,
            AssemblyContext,
        )

        assembler = MagicMock(spec=AssemblerHelpersMixin)
        assembler.settings = AssemblySettings(privacy_mode=privacy_mode)
        assembler._get_video_resolution = MagicMock(return_value=None)
        assembler._get_face_center = MagicMock(return_value=None)
        # Call the real method
        assembler._build_clip_video_filter = AssemblerHelpersMixin._build_clip_video_filter.__get__(
            assembler
        )

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
        return assembler, ctx

    def test_blur_filter_present_when_privacy_on(self):
        assembler, ctx = self._make_assembler(privacy_mode=True)
        clip = AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0)
        result = assembler._build_clip_video_filter(0, clip, ctx)
        assert "gblur=sigma=30" in result

    def test_no_blur_when_privacy_off(self):
        assembler, ctx = self._make_assembler(privacy_mode=False)
        clip = AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0)
        result = assembler._build_clip_video_filter(0, clip, ctx)
        assert "gblur" not in result

    def test_no_blur_for_title_screens(self):
        """Title screens should never be blurred, even with privacy on."""
        assembler, ctx = self._make_assembler(privacy_mode=True)
        clip = AssemblyClip(path=Path("/tmp/title.mp4"), duration=3.0, is_title_screen=True)
        result = assembler._build_clip_video_filter(0, clip, ctx)
        assert "gblur" not in result


class TestPrivacyAudioMute:
    """Audio is replaced with silence for speech clips in privacy mode."""

    def _make_assembler(self, privacy_mode: bool = False):
        from immich_memories.processing.assembler_helpers import AssemblerHelpersMixin

        assembler = MagicMock(spec=AssemblerHelpersMixin)
        assembler.settings = AssemblySettings(privacy_mode=privacy_mode)
        assembler._build_audio_prep_filters = (
            AssemblerHelpersMixin._build_audio_prep_filters.__get__(assembler)
        )
        return assembler

    def test_speech_clip_muted_when_privacy_on(self):
        assembler = self._make_assembler(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=True),
        ]
        filter_parts, labels = assembler._build_audio_prep_filters(clips)
        # Should use anullsrc (silence) instead of real audio
        assert "anullsrc" in filter_parts[0]
        # Should NOT reference the input audio stream
        assert "[0:a]" not in filter_parts[0]

    def test_non_speech_clip_keeps_audio_when_privacy_on(self):
        assembler = self._make_assembler(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=False),
        ]
        filter_parts, labels = assembler._build_audio_prep_filters(clips)
        # Should keep real audio (references input stream)
        assert "[0:a]" in filter_parts[0]

    def test_speech_clip_keeps_audio_when_privacy_off(self):
        assembler = self._make_assembler(privacy_mode=False)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, has_speech=True),
        ]
        filter_parts, labels = assembler._build_audio_prep_filters(clips)
        # Should keep real audio when privacy is off
        assert "[0:a]" in filter_parts[0]
