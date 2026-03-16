"""Tests for scoring priority simplification (6 weights -> 3 knobs)."""

from __future__ import annotations

import pytest

from immich_memories.memory_types.presets import ScoringProfile


class TestScoringPriorities:
    def test_default_priorities_produce_valid_weights(self):
        profile = ScoringProfile.from_priorities()
        assert profile.face_weight > 0
        assert profile.motion_weight > 0
        assert profile.duration_weight > 0

    def test_people_high_boosts_face_weight(self):
        default = ScoringProfile.from_priorities(people="medium")
        people_high = ScoringProfile.from_priorities(people="high")
        assert people_high.face_weight > default.face_weight

    def test_quality_high_boosts_stability_weight(self):
        default = ScoringProfile.from_priorities(quality="medium")
        quality_high = ScoringProfile.from_priorities(quality="high")
        assert quality_high.stability_weight > default.stability_weight

    def test_moment_high_boosts_audio_weight(self):
        default = ScoringProfile.from_priorities(moment="medium")
        moment_high = ScoringProfile.from_priorities(moment="high")
        assert moment_high.audio_weight > default.audio_weight

    def test_low_reduces_weights(self):
        default = ScoringProfile.from_priorities(people="medium")
        low = ScoringProfile.from_priorities(people="low")
        assert low.face_weight < default.face_weight

    def test_invalid_priority_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            ScoringProfile.from_priorities(people="extreme")

    def test_all_combinations_produce_positive_weights(self):
        for p in ["low", "medium", "high"]:
            for q in ["low", "medium", "high"]:
                for m in ["low", "medium", "high"]:
                    profile = ScoringProfile.from_priorities(people=p, quality=q, moment=m)
                    assert profile.face_weight >= 0
                    assert profile.motion_weight >= 0
                    assert profile.stability_weight >= 0
                    assert profile.audio_weight >= 0
                    assert profile.duration_weight >= 0
