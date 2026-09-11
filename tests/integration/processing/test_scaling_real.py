"""Real FFmpeg integration tests for scaling utilities.

Tests _get_video_duration, _detect_face_center_in_video, and
aggregate_mood_from_clips against actual FFmpeg on synthetic clips.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("ffmpeg")]


# ---------------------------------------------------------------------------
# _get_video_duration (scaling_utilities)
# ---------------------------------------------------------------------------


class TestGetVideoDuration:
    def test_returns_approx_3s(self, test_clip_720p: Path):
        from immich_memories.processing.scaling_utilities import _get_video_duration

        duration = _get_video_duration(test_clip_720p)
        assert 2.5 <= duration <= 3.5

    def test_short_clip(self, short_clip: Path):
        from immich_memories.processing.scaling_utilities import _get_video_duration

        duration = _get_video_duration(short_clip)
        assert 0.5 <= duration <= 1.5


# ---------------------------------------------------------------------------
# _detect_face_center_in_video
# ---------------------------------------------------------------------------


class TestDetectFaceCenterInVideo:
    def test_no_faces_in_test_pattern(self, test_clip_720p: Path):
        """Test pattern has no real faces -- expect None or a valid center tuple."""
        from immich_memories.processing.scaling_utilities import (
            _detect_face_center_in_video,
        )

        result = _detect_face_center_in_video(test_clip_720p)
        if result is not None:
            # If detection returns something, it should be a valid 0-1 range
            x, y = result
            assert 0.0 <= x <= 1.0
            assert 0.0 <= y <= 1.0
        # None is the expected result for a test pattern


# ---------------------------------------------------------------------------
# aggregate_mood_from_clips
# ---------------------------------------------------------------------------


class TestAggregateMoodFromClips:
    def test_returns_dominant_mood(self):
        from immich_memories.processing.scaling_utilities import (
            aggregate_mood_from_clips,
        )

        clips = [
            SimpleNamespace(llm_emotion="happy"),
            SimpleNamespace(llm_emotion="joyful"),
            SimpleNamespace(llm_emotion="calm"),
            SimpleNamespace(llm_emotion="happy"),
        ]
        result = aggregate_mood_from_clips(clips)
        # "happy" and "joyful" both map to "happy" (3 total), "calm" has 1
        assert result == "happy"

    def test_returns_none_for_empty(self):
        from immich_memories.processing.scaling_utilities import (
            aggregate_mood_from_clips,
        )

        clips = [SimpleNamespace(other_attr="x")]
        assert aggregate_mood_from_clips(clips) is None

    def test_handles_unknown_emotions(self):
        from immich_memories.processing.scaling_utilities import (
            aggregate_mood_from_clips,
        )

        clips = [
            SimpleNamespace(llm_emotion="mysterious"),
            SimpleNamespace(llm_emotion="mysterious"),
        ]
        # Unknown emotions pass through unmapped
        result = aggregate_mood_from_clips(clips)
        assert result == "mysterious"
