"""Real FFmpeg integration tests for downscaler and scaling utilities.

Tests downscale_video, needs_downscaling, get_video_height, _get_video_duration,
_detect_face_center_in_video, and aggregate_mood_from_clips against actual
FFmpeg on synthetic clips.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration.conftest import ffprobe_json

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("ffmpeg")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_height(probe: dict) -> int:
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["height"])
    raise ValueError("No video stream found")


# ---------------------------------------------------------------------------
# get_video_height
# ---------------------------------------------------------------------------


class TestGetVideoHeight:
    def test_returns_720_for_720p(self, test_clip_720p: Path):
        from immich_memories.processing.downscaler import get_video_height

        assert get_video_height(test_clip_720p) == 720

    def test_returns_1280_for_portrait(self, portrait_clip: Path):
        from immich_memories.processing.downscaler import get_video_height

        assert get_video_height(portrait_clip) == 1280


# ---------------------------------------------------------------------------
# needs_downscaling
# ---------------------------------------------------------------------------


class TestNeedsDownscaling:
    def test_true_when_much_larger(self, test_clip_720p: Path):
        """720p is well above 240p * 1.5 = 360p, so needs downscaling."""
        from immich_memories.processing.downscaler import needs_downscaling

        assert needs_downscaling(test_clip_720p, target_height=240) is True

    def test_false_at_same_height(self, test_clip_720p: Path):
        """720p is not > 720 * 1.5 = 1080, so no downscaling needed."""
        from immich_memories.processing.downscaler import needs_downscaling

        assert needs_downscaling(test_clip_720p, target_height=720) is False

    def test_false_slightly_above(self, test_clip_720p: Path):
        """720p is not > 480 * 1.5 = 720, so no downscaling at 480p target."""
        from immich_memories.processing.downscaler import needs_downscaling

        assert needs_downscaling(test_clip_720p, target_height=480) is False


# ---------------------------------------------------------------------------
# downscale_video
# ---------------------------------------------------------------------------


class TestDownscaleVideo:
    def test_produces_output_at_target_height(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.downscaler import downscale_video

        target_h = 240
        out = tmp_path / "downscaled_240p.mp4"
        result = downscale_video(test_clip_720p, target_height=target_h, output_path=out)

        assert result == out
        assert out.exists()
        probe = ffprobe_json(out)
        h = _get_height(probe)
        assert h == target_h

    def test_returns_original_when_no_downscale_needed(self, short_clip: Path, tmp_path: Path):
        """480p clip at 480p target -- under the 1.5x threshold, returns original."""
        from immich_memories.processing.downscaler import downscale_video

        out = tmp_path / "should_not_exist.mp4"
        result = downscale_video(short_clip, target_height=480, output_path=out)

        # Short clip is 480p which is not > 480 * 1.5 = 720
        assert result == short_clip
        assert not out.exists()


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
