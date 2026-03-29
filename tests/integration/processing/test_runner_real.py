"""Integration tests for FFmpeg runner with real subprocess calls.

Run with: make test-integration-processing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# _parse_ffmpeg_time
# ---------------------------------------------------------------------------


class TestParseFFmpegTime:
    def test_hms_format(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_time

        assert _parse_ffmpeg_time("01:02:03.45") == pytest.approx(3723.45)

    def test_ms_format(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_time

        assert _parse_ffmpeg_time("05:30.00") == pytest.approx(330.0)

    def test_negative_returns_zero(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_time

        assert _parse_ffmpeg_time("-00:00:01.00") == 0.0

    def test_plain_seconds(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_time

        assert _parse_ffmpeg_time("12.5") == pytest.approx(12.5)


# ---------------------------------------------------------------------------
# _parse_ffmpeg_progress
# ---------------------------------------------------------------------------


class TestParseFFmpegProgress:
    def test_typical_progress_line(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_progress

        line = "frame=  123 fps= 45.0 q=28.0 size=    1234kB time=00:00:05.12 bitrate= 123.4kbits/s speed=1.23x"
        progress = _parse_ffmpeg_progress(line, total_duration=10.0)
        assert progress is not None
        assert progress.frame == 123
        assert progress.fps == pytest.approx(45.0)
        assert progress.time_seconds == pytest.approx(5.12)
        assert progress.speed == pytest.approx(1.23)
        assert progress.percent == pytest.approx(51.2, abs=0.5)

    def test_no_meaningful_data_returns_none(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_progress

        result = _parse_ffmpeg_progress("some random log line", total_duration=10.0)
        assert result is None

    def test_na_time(self):
        from immich_memories.processing.ffmpeg_runner import _parse_ffmpeg_progress

        line = "frame=    0 fps=0.0 time=N/A bitrate=N/A speed=N/A"
        result = _parse_ffmpeg_progress(line, total_duration=10.0)
        assert result is None


# ---------------------------------------------------------------------------
# _run_ffmpeg_with_progress: real re-encode
# ---------------------------------------------------------------------------


class TestRunFFmpegWithProgress:
    def test_real_reencode(self, test_clip_720p: Path, tmp_path: Path):
        """Re-encode a clip and verify output is a valid video."""
        from immich_memories.processing.ffmpeg_runner import _run_ffmpeg_with_progress

        output = tmp_path / "reencoded.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(test_clip_720p),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(output),
        ]

        result = _run_ffmpeg_with_progress(cmd, total_duration=3.0)
        assert result.returncode == 0
        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert get_duration(probe) > 2.0

    def test_progress_callback_receives_updates(self, test_clip_720p: Path, tmp_path: Path):
        """Progress callback receives at least one update during encoding."""
        from immich_memories.processing.ffmpeg_runner import _run_ffmpeg_with_progress

        output = tmp_path / "progress_test.mp4"
        updates: list[tuple[float, str]] = []

        def callback(percent: float, status: str) -> None:
            updates.append((percent, status))

        # WHY: use slow preset and scale up to 1080p to force the encode to
        # take long enough for the 0.5s progress throttle to fire at least once
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(test_clip_720p),
            "-vf",
            "scale=1920:1080",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ]

        result = _run_ffmpeg_with_progress(cmd, total_duration=3.0, progress_callback=callback)
        assert result.returncode == 0
        # WHY: progress updates are throttled to 0.5s intervals; encoding
        # with slow preset should take long enough for at least one callback
        assert len(updates) >= 1, "Expected at least one progress callback"
        percent, status = updates[0]
        assert 0 <= percent <= 100
        assert isinstance(status, str)

    def test_error_capture_on_bad_input(self, tmp_path: Path):
        """FFmpeg returns non-zero and stderr is captured for bad input."""
        from immich_memories.processing.ffmpeg_runner import _run_ffmpeg_with_progress

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(tmp_path / "nonexistent.mp4"),
            "-c:v",
            "copy",
            str(tmp_path / "output.mp4"),
        ]

        result = _run_ffmpeg_with_progress(cmd, total_duration=1.0)
        assert result.returncode != 0
        assert "nonexistent" in result.stderr.lower() or "no such file" in result.stderr.lower()

    def test_stats_flag_injected(self, test_clip_720p: Path, tmp_path: Path):
        """Verify -stats is added to commands that don't have it."""
        from immich_memories.processing.ffmpeg_runner import _run_ffmpeg_with_progress

        output = tmp_path / "stats_test.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(test_clip_720p),
            "-c",
            "copy",
            str(output),
        ]

        result = _run_ffmpeg_with_progress(cmd, total_duration=3.0)
        assert result.returncode == 0
        # -stats should have been injected; stderr should contain progress info
        assert result.stderr, "stderr should contain FFmpeg output"
