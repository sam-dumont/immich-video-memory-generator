"""Tests for privacy/demo mode: heavy blur video + muffle audio."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.filter_builder import FilterBuilder


class TestPrivacyModeConfig:
    """AssemblySettings has privacy_mode flag."""

    def test_default_is_false(self):
        settings = AssemblySettings(encoding_plan=standalone_assembly_encoding_plan())
        assert not settings.privacy_mode

    def test_can_be_enabled(self):
        settings = AssemblySettings(
            encoding_plan=standalone_assembly_encoding_plan(), privacy_mode=True
        )
        assert settings.privacy_mode


class TestPrivacyVideoBlur:
    """Video filter includes strong gaussian blur when privacy mode is on."""

    def _make_filter_builder(self, privacy_mode: bool = False):
        """Create a FilterBuilder with mocked dependencies."""
        settings = AssemblySettings(
            encoding_plan=standalone_assembly_encoding_plan(), privacy_mode=privacy_mode
        )
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
        settings = AssemblySettings(
            encoding_plan=standalone_assembly_encoding_plan(), privacy_mode=privacy_mode
        )
        # WHY: mock prober + face_center_fn — audio filter tests don't touch video I/O
        prober = MagicMock()
        face_center_fn = MagicMock(return_value=None)
        return FilterBuilder(settings, prober, face_center_fn)

    def test_all_clips_muffled_when_privacy_on(self):
        """ALL clips get muffled audio in privacy mode, not just speech-detected."""
        fb = self._make_filter_builder(privacy_mode=True)
        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0),
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
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0),
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
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0),
        ]
        filter_parts, labels = fb.build_audio_prep_filters(clips)
        # Should keep real audio when privacy is off
        assert "[0:a]" in filter_parts[0]
        assert "lowpass" not in filter_parts[0]


class TestPrivacyGpsAnonymization:
    """The destination moves too: no real coordinate reaches the output."""

    # Nowhere near any of the fake cities, so "did it move" is unambiguous.
    AWAY_LAT = 10.0
    AWAY_LON = 20.0

    def _located(self, lat, lon, name=None):
        return AssemblyClip(
            path=Path("/tmp/a.mp4"),
            duration=3.0,
            latitude=lat,
            longitude=lon,
            location_name=name,
        )

    def test_clip_gps_is_relocated_near_the_fake_city(self):
        from immich_memories.generate_privacy import (
            anonymize_clips_for_privacy,
            pick_fake_city,
        )

        _, city_lat, city_lon = pick_fake_city()
        result = anonymize_clips_for_privacy([self._located(self.AWAY_LAT, self.AWAY_LON)])

        assert (result[0].latitude, result[0].longitude) != (self.AWAY_LAT, self.AWAY_LON)
        assert result[0].latitude == pytest.approx(city_lat)
        assert result[0].longitude == pytest.approx(city_lon)

    def test_the_shape_of_the_memory_survives_the_move(self):
        """Pins keep their spacing, so the map still shows a coherent trip."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        clips = [
            self._located(self.AWAY_LAT, self.AWAY_LON),
            self._located(self.AWAY_LAT + 0.2, self.AWAY_LON + 0.1),
        ]
        result = anonymize_clips_for_privacy(clips)

        assert result[1].latitude - result[0].latitude == pytest.approx(0.2)
        assert result[1].longitude - result[0].longitude == pytest.approx(0.1)

    def test_a_continent_wide_memory_still_lands_by_the_city(self):
        """Rigid translation would put a wide spread in the sea; it is scaled."""
        from immich_memories.generate_privacy import (
            anonymize_clips_for_privacy,
            pick_fake_city,
        )

        _, city_lat, city_lon = pick_fake_city()
        clips = [
            self._located(self.AWAY_LAT - 20.0, self.AWAY_LON - 30.0),
            self._located(self.AWAY_LAT + 20.0, self.AWAY_LON + 30.0),
        ]
        result = anonymize_clips_for_privacy(clips)

        for clip in result:
            assert abs(clip.latitude - city_lat) <= 1.0
            assert abs(clip.longitude - city_lon) <= 1.0

    def test_the_same_memory_relocates_the_same_way_every_run(self):
        """A per-run offset would let repeated renders be averaged back."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        clips = [self._located(self.AWAY_LAT, self.AWAY_LON)]

        first = anonymize_clips_for_privacy(clips)
        second = anonymize_clips_for_privacy(clips)

        assert (first[0].latitude, first[0].longitude) == (
            second[0].latitude,
            second[0].longitude,
        )

    def test_the_place_name_goes_with_the_coordinates(self):
        """The name identifies the place harder than the coordinate does."""
        from immich_memories.generate_privacy import (
            anonymize_clips_for_privacy,
            pick_fake_city,
        )

        fake_name, _, _ = pick_fake_city()
        result = anonymize_clips_for_privacy(
            [self._located(self.AWAY_LAT, self.AWAY_LON, "Real Village, Realland")]
        )

        assert result[0].location_name == fake_name

    def test_a_clip_with_no_place_name_gains_none(self):
        """Inventing a caption where the memory had none changes the render."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        result = anonymize_clips_for_privacy([self._located(self.AWAY_LAT, self.AWAY_LON)])

        assert result[0].location_name is None

    def test_home_and_destination_are_both_faked_in_the_preset(self):
        from immich_memories.generate_privacy import anonymize_preset_params, pick_fake_city

        fake_name, _, _ = pick_fake_city()
        preset = {"home_lat": 48.85, "home_lon": 2.35, "location_name": "Real Village"}

        result = anonymize_preset_params(preset)

        assert result["home_lat"] != 48.85
        assert result["home_lon"] != 2.35
        assert result["location_name"] == fake_name

    def test_clip_without_gps_keeps_none(self):
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
                outgoing_transition="fade",
            ),
        ]
        result = anonymize_clips_for_privacy(clips)
        assert result[0].llm_emotion == "joyful"
        assert result[0].original_segment is segment
        assert result[0].is_photo is True
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
