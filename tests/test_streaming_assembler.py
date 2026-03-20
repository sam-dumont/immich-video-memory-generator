"""Unit tests for streaming assembler components."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)  # noqa: S603, S607
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@requires_ffmpeg
class TestFrameDecoder:
    def test_yields_frames_with_correct_shape(self, tmp_path: object) -> None:
        """FrameDecoder should yield numpy arrays of (height, width, 3)."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        tmp = Path(str(tmp_path))

        # Create a tiny test clip via FFmpeg
        clip = tmp / "test.mp4"
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=10:duration=0.5",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(clip),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )

        decoder = FrameDecoder(clip, width=320, height=240, fps=10)
        frames = list(decoder)

        assert len(frames) >= 4  # 0.5s * 10fps = 5 frames (allow off-by-one)
        for frame in frames:
            assert frame.shape == (240, 320, 3)
            assert frame.dtype == np.uint8


@requires_ffmpeg
class TestStreamingEncoder:
    def test_encodes_frames_to_valid_mp4(self, tmp_path: object) -> None:
        """StreamingEncoder should produce a valid MP4 from numpy frames."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import StreamingEncoder

        tmp = Path(str(tmp_path))
        output = tmp / "test_output.mp4"
        width, height, fps = 320, 240, 10
        n_frames = 10

        encoder = StreamingEncoder(output, width, height, fps, crf=28)
        encoder.start()
        for i in range(n_frames):
            # Gradient frame — different each frame for visual verification
            frame = np.full((height, width, 3), fill_value=i * 25, dtype=np.uint8)
            encoder.write_frame(frame)
        encoder.finish()

        assert output.exists()
        assert output.stat().st_size > 0

        # Verify with ffprobe
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
        assert len(video_streams) == 1
        assert float(probe["format"]["duration"]) > 0.5


class TestFrameBlender:
    def test_crossfade_blend_produces_interpolated_frames(self) -> None:
        """Blending two frames at alpha=0.5 should average pixel values."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=0.5, out=out, temp=temp)

        # (100 * 0.5 + 200 * 0.5) = 150
        assert np.all(out == 150)

    def test_crossfade_alpha_zero_is_frame_a(self) -> None:
        """Alpha=0 should return frame_a unchanged."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=0.0, out=out, temp=temp)
        assert np.all(out == 100)

    def test_crossfade_alpha_one_is_frame_b(self) -> None:
        """Alpha=1 should return frame_b unchanged."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=1.0, out=out, temp=temp)
        assert np.all(out == 200)
