"""Video creation using Taichi GPU-rendered title frames.

Pipes rendered frames into FFmpeg to produce the final title video file.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer
from immich_memories.processing.ffmpeg_runner import drain_stderr_tail

from .encoding import standalone_title_encoding_plan, title_color_filter, title_encoder_args
from .renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

logger = logging.getLogger(__name__)

_FFMPEG_STDERR_TAIL_BYTES = 8192
_FRAME_QUEUE_POLL_SECONDS = 0.1


def _pipe_writer(
    proc: subprocess.Popen,
    q: queue.Queue[bytes | None],
    errors: list[Exception],
    done: threading.Event,
) -> None:
    """Background thread: drain frame queue into FFmpeg stdin."""
    try:
        while True:
            data = q.get()
            if data is None:
                break
            proc.stdin.write(data)  # type: ignore[union-attr]
    except Exception as exc:
        errors.append(exc)
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()  # type: ignore[union-attr]
        done.set()


def _put_while_writer_active(
    frame_q: queue.Queue[bytes | None],
    data: bytes,
    writer_done: threading.Event,
) -> bool:
    """Enqueue one frame without blocking forever after the writer exits."""
    while not writer_done.is_set():
        try:
            frame_q.put(data, timeout=_FRAME_QUEUE_POLL_SECONDS)
            return True
        except queue.Full:
            continue
    return False


def _finish_pipe_writer(
    frame_q: queue.Queue[bytes | None],
    writer: threading.Thread,
    writer_done: threading.Event,
) -> None:
    """Signal EOF when possible and always join the stdin writer."""
    while not writer_done.is_set():
        try:
            frame_q.put(None, timeout=_FRAME_QUEUE_POLL_SECONDS)
            break
        except queue.Full:
            continue
    writer.join()


def _apply_fade_from_white(
    frame: np.ndarray,
    frame_num: int,
    fade_in_frames: int,
    white_val: int,
    blend_buffer: np.ndarray | None,
) -> np.ndarray:
    """Apply fade-from-white effect to a frame."""
    if blend_buffer is not None and frame_num < fade_in_frames:
        alpha = 1.0 - (1.0 - frame_num / fade_in_frames) ** 2
        np.multiply(white_val * (1 - alpha), 1.0, out=blend_buffer, casting="unsafe")
        np.add(blend_buffer, frame * alpha, out=blend_buffer, casting="unsafe")
        return blend_buffer
    return frame


def _apply_fade_to_white(
    frame: np.ndarray,
    frame_num: int,
    fade_out_start: int,
    fade_out_frames: int,
    white_val: int,
    blend_buffer: np.ndarray | None,
) -> np.ndarray:
    """Apply fade-to-white effect at the end of a video."""
    if blend_buffer is not None and fade_out_frames > 0 and frame_num >= fade_out_start:
        t = (frame_num - fade_out_start) / max(1, fade_out_frames)
        alpha = t * t  # quadratic ease-in
        np.multiply(white_val * alpha, 1.0, out=blend_buffer, casting="unsafe")
        np.add(blend_buffer, frame * (1 - alpha), out=blend_buffer, casting="unsafe")
        return blend_buffer
    return frame


@dataclass(frozen=True)
class _FrameRenderContext:
    renderer: TaichiTitleRenderer
    title: str
    subtitle: str | None
    fade_in_frames: int
    fade_out_start: int
    fade_out_frames: int
    white_val: int
    blend_buffer: np.ndarray | None
    frame_progress: Callable[[int, int], None] | None


def _produce_frames(
    context: _FrameRenderContext,
    frame_q: queue.Queue[bytes | None],
    writer_done: threading.Event,
) -> None:
    """Render frames until completion or until the FFmpeg writer exits."""
    for frame_num in range(context.renderer.total_frames):
        frame = context.renderer.render_frame(frame_num, context.title, context.subtitle)
        out = _apply_fade_from_white(
            frame,
            frame_num,
            context.fade_in_frames,
            context.white_val,
            context.blend_buffer,
        )
        out = _apply_fade_to_white(
            out,
            frame_num,
            context.fade_out_start,
            context.fade_out_frames,
            context.white_val,
            context.blend_buffer,
        )
        if not _put_while_writer_active(frame_q, bytes(out.data), writer_done):
            break
        if context.frame_progress and frame_num % 10 == 0:
            context.frame_progress(frame_num, context.renderer.total_frames)


def _raise_ffmpeg_errors(
    process: subprocess.Popen,
    stderr_tail: bytearray,
    writer_errors: list[Exception],
) -> None:
    """Raise the most useful error while preserving the writer exception as cause."""
    stderr = bytes(stderr_tail).decode(errors="replace").strip()
    if process.returncode != 0:
        message = f"FFmpeg failed with return code {process.returncode}: {stderr}"
        if writer_errors:
            raise RuntimeError(message) from writer_errors[0]
        raise RuntimeError(message)
    if writer_errors:
        raise RuntimeError("FFmpeg frame writer failed") from writer_errors[0]


def create_title_video_taichi(
    title: str,
    subtitle: str | None,
    output_path: Path,
    config: TaichiTitleConfig | None = None,
    fade_from_white: bool = False,
    fade_to_white: bool = False,
    encoding_plan: EncodingPlan | None = None,
    frame_progress: Callable[[int, int], None] | None = None,
    frame_transfer: HdrTransfer = HdrTransfer.NONE,
) -> Path:
    """Create title video using Taichi GPU rendering."""
    cfg = config or TaichiTitleConfig()
    plan = encoding_plan or standalone_title_encoding_plan()
    cfg.hdr = plan.hdr
    output_path.parent.mkdir(parents=True, exist_ok=True)

    renderer = TaichiTitleRenderer(cfg)

    encoder_args = title_encoder_args(plan)

    # WHY: rgb48le (16-bit) for HDR preserves full 10-bit+ precision.
    # rgb24 (8-bit) for SDR. No zscale conversion needed — data is
    # already in the correct color space from the source clip.
    pix_fmt = "rgb48le" if plan.hdr else "rgb24"

    # A content-backed HDR title is already rendered in the final transfer.
    # Preserve the historical working path: describe that raw input precisely
    # and let the encoder quantize it, without converting HLG/PQ to SDR and back.
    transfer_matches_output = plan.hdr and frame_transfer is plan.target_transfer
    input_color_args: list[str] = []
    if transfer_matches_output:
        input_color_args = [
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084" if frame_transfer is HdrTransfer.PQ else "arib-std-b67",
            "-colorspace",
            "bt2020nc",
        ]
        video_filter_args = []
    else:
        video_filter_args = ["-vf", title_color_filter(plan)]

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{cfg.width}x{cfg.height}",
        "-pix_fmt",
        pix_fmt,
        *input_color_args,
        "-r",
        str(cfg.fps),
        "-i",
        "-",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        *video_filter_args,
        *encoder_args,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        str(cfg.duration),
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info(f"Generating title with Taichi: {title}")

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr_tail = bytearray()
    stderr_reader = threading.Thread(
        target=drain_stderr_tail,
        args=(process.stderr, stderr_tail),
        kwargs={"limit": _FFMPEG_STDERR_TAIL_BYTES},
        name="title-ffmpeg-stderr",
        daemon=True,
    )
    stderr_reader.start()

    fade_in_frames = int(0.8 * cfg.fps) if fade_from_white else 0
    # Fade TO white in last 1.5 seconds (for ending screens)
    fade_out_frames = int(1.5 * cfg.fps) if fade_to_white else 0
    fade_out_start = renderer.total_frames - fade_out_frames

    white_val = 65535 if plan.hdr else 255
    blend_dtype = np.uint16 if plan.hdr else np.uint8
    blend_buffer = (
        np.zeros((cfg.height, cfg.width, 3), dtype=blend_dtype)
        if (fade_from_white or fade_to_white)
        else None
    )

    # WHY: Buffered writer decouples GPU rendering from FFmpeg encoding.
    # Without it, process.stdin.write() blocks ~500ms/frame at 4K because
    # the pipe buffer (32KB) is tiny relative to frame size (12MB).
    frame_q: queue.Queue[bytes | None] = queue.Queue(maxsize=4)
    writer_errors: list[Exception] = []
    writer_done = threading.Event()
    writer = threading.Thread(
        target=_pipe_writer,
        args=(process, frame_q, writer_errors, writer_done),
        name="title-ffmpeg-stdin",
        daemon=True,
    )
    writer.start()

    render_context = _FrameRenderContext(
        renderer=renderer,
        title=title,
        subtitle=subtitle,
        fade_in_frames=fade_in_frames,
        fade_out_start=fade_out_start,
        fade_out_frames=fade_out_frames,
        white_val=white_val,
        blend_buffer=blend_buffer,
        frame_progress=frame_progress,
    )
    try:
        _produce_frames(render_context, frame_q, writer_done)
    finally:
        _finish_pipe_writer(frame_q, writer, writer_done)
        process.wait()
        stderr_reader.join()

    _raise_ffmpeg_errors(process, stderr_tail, writer_errors)

    logger.info(f"Title generated: {output_path}")
    return output_path
