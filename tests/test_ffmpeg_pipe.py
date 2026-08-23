"""Tests for the FFmpeg stdin/stderr pipe plumbing.

Writing frames to a subprocess's stdin while its stderr is piped and unread is
a classic deadlock: once the child fills the stderr pipe buffer it blocks on
that write, stops draining stdin, and the producer blocks in turn. Neither side
can progress and the render hangs forever at 0% CPU.

These tests use a real child process rather than a mock, because the deadlock
lives in OS pipe buffering — a fake would not reproduce it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from immich_memories.titles.ffmpeg_pipe import drain_stderr_tail
from immich_memories.titles.styles import TitleStyle

# Child that floods stderr *before* reading any stdin. Without a concurrent
# drain the parent's stdin write blocks as soon as the stderr buffer fills.
_NOISY_CHILD = (
    "import sys;"
    "sys.stderr.buffer.write(b'x' * 400000);"
    "sys.stderr.buffer.flush();"
    "data = sys.stdin.buffer.read();"
    "sys.stderr.buffer.write(b'read %d bytes' % len(data));"
    "sys.stderr.buffer.flush()"
)


class TestDrainStderrTail:
    def test_concurrent_drain_prevents_deadlock(self) -> None:
        """The whole point: a noisy child must not be able to stall the writer."""
        process = subprocess.Popen(
            [sys.executable, "-c", _NOISY_CHILD],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tail = bytearray()
        reader = threading.Thread(target=drain_stderr_tail, args=(process.stderr, tail))
        reader.start()

        assert process.stdin is not None
        process.stdin.write(b"f" * 300000)
        process.stdin.close()

        assert process.wait(timeout=30) == 0
        reader.join(timeout=10)
        assert not reader.is_alive()
        assert b"read 300000 bytes" in bytes(tail)

    def test_without_drain_the_writer_actually_deadlocks(self) -> None:
        """Proves the child reproduces the bug, so the test above means something.

        The blocking write is done on a daemon thread: without a stderr drain it
        never returns, so it cannot be awaited on the main thread.
        """
        process = subprocess.Popen(
            [sys.executable, "-c", _NOISY_CHILD],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        finished = threading.Event()

        def _write_without_draining() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(b"f" * 300000)
                process.stdin.flush()
            except OSError:
                pass
            finally:
                finished.set()

        writer = threading.Thread(target=_write_without_draining, daemon=True)
        writer.start()
        try:
            assert not finished.wait(timeout=5.0), (
                "the write completed without a stderr drain, so this child no "
                "longer reproduces the deadlock and the test above proves nothing"
            )
        finally:
            process.kill()
            process.wait(timeout=10)
            finished.wait(timeout=5)

    def test_tail_is_bounded_to_the_newest_bytes(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'a'*50000 + b'TAIL')"],
            stderr=subprocess.PIPE,
        )
        tail = bytearray()
        drain_stderr_tail(process.stderr, tail, limit=1024)
        process.wait(timeout=30)

        assert len(tail) <= 1024
        assert bytes(tail).endswith(b"TAIL")


class TestDrainGuards:
    def test_missing_stream_is_a_no_op(self) -> None:
        """Popen without stderr=PIPE leaves process.stderr as None."""
        tail = bytearray()

        drain_stderr_tail(None, tail)

        assert tail == bytearray()


class TestFailuresCarryStderrTail:
    """Every rewritten call site must surface FFmpeg's drained stderr in its error.

    These also pin the drain lifecycle: without start()/stop() around the frame
    loop the tail would be empty and these assertions would fail.
    """

    @staticmethod
    def _failing_process(message: bytes = b"ffmpeg exploded"):
        from unittest.mock import MagicMock

        process = MagicMock()
        process.stdin = MagicMock()
        process.stdin.closed = False
        process.stderr = MagicMock()
        # Drained in a loop until it yields b"", mirroring a real pipe reaching EOF.
        process.stderr.read.side_effect = [message, b""]
        process.returncode = 1
        return process

    def test_video_encoding_failure_reports_stderr(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from immich_memories.titles.video_encoding import create_title_video

        with (
            # WHY: replaces the FFmpeg subprocess
            patch("immich_memories.titles.video_encoding.subprocess.Popen") as popen,
            # WHY: replaces the frame renderer, which needs fonts and a real canvas
            patch("immich_memories.titles.video_encoding._render_frame_with_animation") as render,
        ):
            popen.return_value = self._failing_process()
            render.return_value = Image.new("RGB", (32, 18))
            with pytest.raises(RuntimeError, match="ffmpeg exploded"):
                create_title_video(
                    title="t",
                    subtitle=None,
                    style=TitleStyle(),
                    output_path=tmp_path / "title.mp4",
                    width=32,
                    height=18,
                    duration=0.2,
                    fps=10.0,
                )

    def test_video_encoding_does_not_hang_when_ffmpeg_dies_before_the_queue_fills(
        self, tmp_path: Path
    ) -> None:
        """A dead encoder used to leave the render loop blocked forever on a full queue (#343)."""
        import threading
        from unittest.mock import patch

        from immich_memories.titles.video_encoding import create_title_video

        process = self._failing_process(b"Cannot load libcuda")
        process.stdin.write.side_effect = BrokenPipeError(32, "Broken pipe")
        outcome: list[BaseException | None] = []

        def run() -> None:
            try:
                with (
                    # WHY: replaces the FFmpeg subprocess with one that dies at start
                    patch("immich_memories.titles.video_encoding.subprocess.Popen") as popen,
                    # WHY: replaces the frame renderer, which needs fonts and a real canvas
                    patch(
                        "immich_memories.titles.video_encoding._render_frame_with_animation"
                    ) as render,
                ):
                    popen.return_value = process
                    render.return_value = Image.new("RGB", (32, 18))
                    create_title_video(
                        title="t",
                        subtitle=None,
                        style=TitleStyle(),
                        output_path=tmp_path / "title.mp4",
                        width=32,
                        height=18,
                        duration=4.0,  # 40 frames: far more than the 10-slot queue
                        fps=10.0,
                    )
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001
                outcome.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=15)

        assert not worker.is_alive(), "create_title_video hung after the encoder died"
        assert isinstance(outcome[0], RuntimeError)
        assert "libcuda" in str(outcome[0])

    def test_ending_service_failure_reports_stderr(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from immich_memories.titles.encoding import standalone_title_encoding_plan
        from immich_memories.titles.ending_service import EndingService

        with patch("subprocess.Popen") as popen:  # WHY: replaces the FFmpeg subprocess
            popen.return_value = self._failing_process()
            with pytest.raises(RuntimeError, match="ffmpeg exploded"):
                EndingService(TitleStyle()).create_ending_video(
                    output_path=tmp_path / "ending.mp4",
                    fade_to_color=(0, 0, 0),
                    width=32,
                    height=18,
                    duration=0.2,
                    fps=10.0,
                    encoding_plan=standalone_title_encoding_plan(),
                )

    def test_map_animation_failure_reports_stderr(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from immich_memories.titles.map_animation import _FlyConfig, _pipe_frames

        with (
            # WHY: replaces the FFmpeg subprocess
            patch("immich_memories.titles.map_animation.subprocess.Popen") as popen,
            # WHY: replaces map tile rendering, which would fetch tiles over the network
            patch("immich_memories.titles.map_animation._get_frame_at") as get_frame,
        ):
            popen.return_value = self._failing_process()
            get_frame.return_value = (Image.new("RGB", (32, 18)), 9.0)
            with pytest.raises(RuntimeError, match="ffmpeg exploded"):
                _pipe_frames(
                    _FlyConfig(width=32, height=18),
                    tmp_path / "map.mp4",
                    duration=0.2,
                    fps=10.0,
                    hold_start=0.0,
                    hold_end=0.0,
                    encoding_plan=None,
                )
