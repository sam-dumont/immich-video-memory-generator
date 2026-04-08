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
        """Blur must be present and scale with resolution."""
        fb, ctx = self._make_filter_builder(privacy_mode=True)
        clip = AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0)
        result = fb.build_clip_video_filter(0, clip, ctx)
        # default_resolution=None → fallback 1080, 1080 * 0.025 = 27
        assert "gblur=sigma=37" in result

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


class TestPrivacyGpsAnonymization:
    """GPS coordinates must be randomized in privacy mode."""

    def test_clip_gps_preserved_in_privacy_mode(self):
        """Privacy mode keeps real clip GPS — only home base is faked."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        clips = [
            AssemblyClip(
                path=Path("/tmp/a.mp4"),
                duration=3.0,
                latitude=48.8566,
                longitude=2.3522,
                location_name="Paris, France",
            ),
        ]
        result = anonymize_clips_for_privacy(clips)
        assert result[0].latitude == 48.8566
        assert result[0].longitude == 2.3522
        assert result[0].location_name == "Paris, France"

    def test_clips_returned_unchanged(self):
        """All clips pass through without modification."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0, latitude=50.0, longitude=3.0),
            AssemblyClip(path=Path("/tmp/b.mp4"), duration=3.0, latitude=50.1, longitude=3.1),
        ]
        result = anonymize_clips_for_privacy(clips)
        assert result[0].latitude == 50.0
        assert result[1].latitude == 50.1

    def test_home_gps_anonymized_in_preset(self):
        """home_lat/home_lon shifted, but location_name preserved."""
        from immich_memories.generate_privacy import anonymize_preset_params

        preset = {"home_lat": 48.85, "home_lon": 2.35, "location_name": "TestCity"}
        result = anonymize_preset_params(preset)
        assert result["home_lat"] != 48.85
        assert result["home_lon"] != 2.35
        assert result["location_name"] == "TestCity"

    def test_clip_without_gps_unchanged(self):
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0),
        ]
        result = anonymize_clips_for_privacy(clips)
        assert result[0].latitude is None
        assert result[0].longitude is None

    def test_anonymize_preserves_all_fields(self):
        """Anonymization must preserve llm_emotion, original_segment, is_photo."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy
        from immich_memories.processing.clips import ClipSegment

        segment = ClipSegment(
            source_path=Path("/tmp/src.mp4"),
            start_time=1.0,
            end_time=4.0,
            asset_id="test-id",
        )
        clips = [
            AssemblyClip(
                path=Path("/tmp/a.mp4"),
                duration=3.0,
                latitude=48.8566,
                longitude=2.3522,
                location_name="Paris, France",
                llm_emotion="joyful",
                original_segment=segment,
                is_photo=True,
                has_speech=True,
                outgoing_transition="fade",
            ),
        ]
        result = anonymize_clips_for_privacy(clips)
        assert result[0].llm_emotion == "joyful"
        assert result[0].original_segment is segment
        assert result[0].is_photo is True
        assert result[0].has_speech is True
        assert result[0].outgoing_transition == "fade"


class TestPrivacyNameAnonymization:
    """Person names must be replaced with fake names in privacy mode."""

    def test_person_name_anonymized(self):
        from immich_memories.generate_privacy import anonymize_name

        assert anonymize_name("TestPerson") != "TestPerson"
        # Should return a consistent fake name
        assert anonymize_name("TestPerson") == anonymize_name("TestPerson")

    def test_deterministic_across_calls(self):
        """Same name always maps to the same fake (even across processes)."""
        from immich_memories.generate_privacy import anonymize_name

        # Determinism: same input → same output every time
        assert anonymize_name("PersonA") == anonymize_name("PersonA")
        assert anonymize_name("PersonB") == anonymize_name("PersonB")
        # Known SHA256-based values (won't change across processes)
        assert anonymize_name("PersonA") in [
            "Alice",
            "Bob",
            "Charlie",
            "Diana",
            "Eve",
            "Frank",
            "Grace",
            "Hank",
            "Iris",
            "Jack",
            "Kim",
            "Leo",
        ]

    def test_none_name_stays_none(self):
        from immich_memories.generate_privacy import anonymize_name

        assert anonymize_name(None) is None
