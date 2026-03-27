"""Real integration test for titles/map_animation.py fly-over video.

Fetches real satellite tiles from ArcGIS, renders frames with PIL,
pipes to real FFmpeg. Verifies the output video exists, has correct
duration, and contains actual rendered content (not blank frames).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np
import pytest

from immich_memories.titles.map_animation import create_map_fly_video
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, requires_ffmpeg]


class TestCreateMapFlyVideo:
    def test_fly_paris_to_london_produces_valid_video(self, tmp_path):
        """Fly-over from Paris to London should produce a valid video with tiles."""
        output = tmp_path / "fly_paris_london.mp4"

        result = create_map_fly_video(
            departure=(48.856, 2.352),  # Paris
            destinations=[(51.507, -0.128)],  # London
            title_text="Paris → London",
            output_path=output,
            width=640,
            height=360,
            duration=3.0,
            fps=15,
        )

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

        duration = get_duration(probe)
        assert 2.5 < duration < 4.0

        # Verify frames have actual content (not black)
        brightness = _extract_frame_brightness(result, 1.5)
        assert brightness > 10.0, "Frame appears blank — tiles may not have loaded"
        logger.info(f"Map fly: {duration:.1f}s, frame brightness={brightness:.0f}")

    def test_fly_multiple_destinations(self, tmp_path):
        """Fly-over with multiple destinations should work."""
        output = tmp_path / "fly_multi.mp4"

        result = create_map_fly_video(
            departure=(48.856, 2.352),  # Paris
            destinations=[
                (41.390, 2.154),  # Barcelona
                (40.416, -3.703),  # Madrid
            ],
            title_text="Summer Trip",
            output_path=output,
            width=640,
            height=360,
            duration=4.0,
            fps=15,
            destination_names=["Barcelona", "Madrid"],
            departure_name="Paris",
        )

        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 3.0

    def test_fly_short_hop_same_city(self, tmp_path):
        """Very short distance should use linear pan, not zoom out to world view."""
        output = tmp_path / "fly_short.mp4"

        result = create_map_fly_video(
            departure=(48.856, 2.352),  # Paris center
            destinations=[(48.873, 2.295)],  # Arc de Triomphe (~2km)
            title_text="Around Paris",
            output_path=output,
            width=640,
            height=360,
            duration=2.0,
            fps=15,
        )

        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 1.5

    def test_empty_title_still_produces_video(self, tmp_path):
        """Empty title text should produce a video without title overlay."""
        output = tmp_path / "fly_no_title.mp4"

        result = create_map_fly_video(
            departure=(48.856, 2.352),
            destinations=[(51.507, -0.128)],
            title_text="",
            output_path=output,
            width=640,
            height=360,
            duration=2.0,
            fps=15,
        )

        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 1.5


def _extract_frame_brightness(video_path: Path, timestamp: float) -> float:
    """Extract a frame at the given timestamp and return its mean brightness."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0 or len(result.stdout) == 0:
        return -1.0
    pixels = np.frombuffer(result.stdout, dtype=np.uint8)
    return float(np.mean(pixels))
