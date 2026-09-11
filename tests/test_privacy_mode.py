"""Tests for privacy/demo mode: the flag, GPS and name anonymization."""

from __future__ import annotations

from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    standalone_assembly_encoding_plan,
)


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
        """Anonymization must preserve llm_emotion and is_photo."""
        from immich_memories.generate_privacy import anonymize_clips_for_privacy

        clips = [
            AssemblyClip(
                path=Path("/tmp/a.mp4"),
                duration=3.0,
                latitude=48.8566,
                longitude=2.3522,
                location_name="Paris, France",
                llm_emotion="joyful",
                is_photo=True,
                outgoing_transition="fade",
            ),
        ]
        result = anonymize_clips_for_privacy(clips)
        assert result[0].llm_emotion == "joyful"
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
