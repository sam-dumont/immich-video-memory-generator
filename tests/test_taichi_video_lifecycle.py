"""Lifecycle contracts for Taichi title rendering's FFmpeg process."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from immich_memories.titles import taichi_video
from immich_memories.titles.renderer_taichi import TaichiTitleConfig


class _FakeStderr:
    def __init__(self, *chunks: bytes, on_read: Callable[[], None] | None = None) -> None:
        self._chunks = list(chunks)
        self._on_read = on_read
        self.read_calls = 0

    def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        if self._on_read is not None:
            self._on_read()
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _FakeStdin:
    def __init__(
        self,
        *,
        before_write: Callable[[], None] | None = None,
        write_error: OSError | None = None,
    ) -> None:
        self._before_write = before_write
        self._write_error = write_error
        self.closed = False
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        if self._before_write is not None:
            self._before_write()
        if self._write_error is not None:
            raise self._write_error
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(
        self,
        *,
        stdin: _FakeStdin | None = None,
        stderr: _FakeStderr | None = None,
        returncode: int = 0,
    ) -> None:
        self.stdin = stdin or _FakeStdin()
        self.stderr = stderr or _FakeStderr()
        self.returncode = returncode
        self.wait_calls = 0

    def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode


class _OneFrameRenderer:
    total_frames = 1

    def __init__(self, _config) -> None:
        pass

    def render_frame(self, _frame_num: int, _title: str, _subtitle: str | None) -> np.ndarray:
        return np.zeros((1, 1, 3), dtype=np.uint8)


class _FailingRenderer(_OneFrameRenderer):
    def render_frame(self, _frame_num: int, _title: str, _subtitle: str | None) -> np.ndarray:
        raise ValueError("render exploded")


def _render_with_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process: _FakeProcess,
    renderer: type[_OneFrameRenderer] = _OneFrameRenderer,
) -> Path:
    monkeypatch.setattr(taichi_video, "TaichiTitleRenderer", renderer)
    monkeypatch.setattr(taichi_video.subprocess, "Popen", lambda *_args, **_kwargs: process)
    config = TaichiTitleConfig(
        width=1,
        height=1,
        fps=1.0,
        duration=1.0,
        enable_bokeh=False,
    )
    return taichi_video.create_title_video_taichi(
        "Title",
        None,
        tmp_path / "title.mp4",
        config=config,
    )


def test_stderr_is_drained_while_frame_writer_is_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pressure_released = threading.Event()
    stdin = _FakeStdin(before_write=pressure_released.wait)
    stderr = _FakeStderr(b"diagnostic", on_read=pressure_released.set)
    process = _FakeProcess(stdin=stdin, stderr=stderr)
    errors: list[BaseException] = []

    def render() -> None:
        try:
            _render_with_process(monkeypatch, tmp_path, process)
        except BaseException as exc:  # pragma: no cover - reported by assertion below
            errors.append(exc)

    caller = threading.Thread(target=render, daemon=True)
    caller.start()
    caller.join(timeout=0.5)
    completed_under_pressure = not caller.is_alive()
    pressure_released.set()
    caller.join(timeout=1.0)

    assert completed_under_pressure, "title rendering deadlocked before stderr was drained"
    assert not errors
    assert stderr.read_calls >= 1
    assert stdin.closed


def test_stderr_tail_is_bounded_and_keeps_latest_bytes() -> None:
    from immich_memories.titles.taichi_video import _drain_stderr_tail

    stream = _FakeStderr(b"oldest-", b"middle-", b"latest")
    tail = bytearray()

    _drain_stderr_tail(stream, tail, limit=13)

    assert bytes(tail) == b"middle-latest"


def test_writer_error_is_propagated_and_stdin_is_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdin = _FakeStdin(write_error=OSError("writer exploded"))
    process = _FakeProcess(stdin=stdin)

    with pytest.raises(RuntimeError, match="frame writer failed") as raised:
        _render_with_process(monkeypatch, tmp_path, process)

    assert isinstance(raised.value.__cause__, OSError)
    assert "writer exploded" in str(raised.value.__cause__)
    assert stdin.closed
    assert process.wait_calls == 1


def test_producer_error_is_preserved_after_process_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _FakeProcess()

    with pytest.raises(ValueError, match="render exploded"):
        _render_with_process(monkeypatch, tmp_path, process, _FailingRenderer)

    assert process.stdin.closed
    assert process.wait_calls == 1


def test_ffmpeg_failure_contains_bounded_diagnostic_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stderr = _FakeStderr(b"obsolete-prefix-", b"useful-tail-marker")
    process = _FakeProcess(stderr=stderr, returncode=7)

    with pytest.raises(RuntimeError, match="useful-tail-marker") as raised:
        _render_with_process(monkeypatch, tmp_path, process)

    assert "return code 7" in str(raised.value)
    assert process.stdin.closed
