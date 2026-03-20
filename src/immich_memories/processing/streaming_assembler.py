"""Streaming video assembler — constant-memory frame blending.

Decodes clips one at a time, blends crossfade transitions with numpy,
and pipes frames to a single FFmpeg encode process. Memory stays constant
regardless of clip count (~550 MB at 4K, ~300 MB at 1080p).

Extends the proven photo pipeline pattern (photos/renderer.py + photo_pipeline.py).
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def blend_crossfade(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    alpha: float,
    out: np.ndarray,
    temp: np.ndarray,
) -> None:
    """Blend two frames for crossfade transition. Fully in-place — zero allocation.

    alpha=0.0 → frame_a, alpha=1.0 → frame_b.
    Both `out` and `temp` must be pre-allocated with the same shape as the frames.
    """
    # WHY: Two pre-allocated buffers avoid ALL temporaries during the blend.
    # At 4K (3840*2160*3 = 25 MB), even one temporary doubles memory per frame.
    inv_alpha = 1.0 - alpha
    np.multiply(frame_a, inv_alpha, out=out, casting="unsafe")
    np.multiply(frame_b, alpha, out=temp, casting="unsafe")
    np.add(out, temp, out=out, casting="unsafe")


class FrameDecoder:
    """Decode a video clip to raw frames via FFmpeg stdout pipe.

    Yields one numpy frame (H, W, 3, uint8) at a time. Only one FFmpeg
    process is alive per decoder instance.
    """

    def __init__(
        self,
        clip_path: Path,
        width: int,
        height: int,
        fps: int,
        pix_fmt: str = "rgb24",
    ) -> None:
        self._clip_path = clip_path
        self._width = width
        self._height = height
        self._fps = fps
        self._pix_fmt = pix_fmt
        self._frame_size = width * height * 3  # rgb24 = 3 bytes/pixel

    def __iter__(self) -> Iterator[np.ndarray]:
        """Yield decoded frames one at a time."""
        cmd = [
            "ffmpeg",
            "-i",
            str(self._clip_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            self._pix_fmt,
            "-vf",
            (
                f"scale={self._width}:{self._height}"
                f":force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={self._fps},setsar=1"
            ),
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "pipe:1",
        ]
        proc = subprocess.Popen(  # noqa: S603, S607
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self._frame_size,
        )
        assert proc.stdout is not None  # noqa: S101

        try:
            while True:
                raw = proc.stdout.read(self._frame_size)
                if len(raw) < self._frame_size:
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(self._height, self._width, 3)
                yield frame
        finally:
            proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)


class StreamingEncoder:
    """Encode raw frames to video via FFmpeg stdin pipe.

    Uses ndarray.data (memoryview) for zero-copy writes — saves ~25 MB
    per frame at 4K vs .tobytes().
    """

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        crf: int = 18,
        pix_fmt: str = "yuv420p",
        codec: str = "libx264",
    ) -> None:
        self._output_path = output_path
        self._width = width
        self._height = height
        self._fps = fps
        self._crf = crf
        self._pix_fmt = pix_fmt
        self._codec = codec
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start the FFmpeg encode process."""
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-c:v",
            self._codec,
            "-preset",
            "medium",
            "-crf",
            str(self._crf),
            "-pix_fmt",
            self._pix_fmt,
            "-movflags",
            "+faststart",
            str(self._output_path),
        ]
        self._proc = subprocess.Popen(  # noqa: S603, S607
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def write_frame(self, frame: np.ndarray) -> None:
        """Write one frame to the encoder. Uses memoryview for zero-copy."""
        assert self._proc is not None and self._proc.stdin is not None  # noqa: S101
        # WHY: ndarray.data is a memoryview — avoids copying ~25 MB per 4K frame
        # that .tobytes() would allocate
        self._proc.stdin.write(frame.data)

    def finish(self) -> None:
        """Close stdin pipe and wait for FFmpeg to finish."""
        if self._proc is None:
            return
        assert self._proc.stdin is not None  # noqa: S101
        with contextlib.suppress(BrokenPipeError):
            self._proc.stdin.close()
        self._proc.wait(timeout=300)
        if self._proc.returncode != 0:
            stderr = self._proc.stderr.read().decode(errors="replace") if self._proc.stderr else ""
            raise RuntimeError(
                f"Streaming encode failed (exit {self._proc.returncode}): {stderr[-500:]}"
            )


def _emit_body_frames(
    active_iter: Iterator[np.ndarray],
    count: int,
    encoder: StreamingEncoder,
) -> None:
    """Write body frames (straight passthrough, no blending) to the encoder."""
    for _ in range(max(count, 0)):
        frame = next(active_iter, None)
        if frame is None:
            break
        encoder.write_frame(frame)


def _emit_crossfade(
    active_iter: Iterator[np.ndarray],
    next_iter: Iterator[np.ndarray],
    fade_frames: int,
    encoder: StreamingEncoder,
    blend_buf: np.ndarray,
    temp_buf: np.ndarray,
    height: int,
    width: int,
) -> None:
    """Blend fade_frames from two iterators and write to encoder."""
    black = np.zeros((height, width, 3), dtype=np.uint8)
    for fade_idx in range(fade_frames):
        frame_a = next(active_iter, None)
        frame_b = next(next_iter, None)

        if frame_a is None is frame_b:
            break
        if frame_a is None:
            frame_a = black
        if frame_b is None:
            frame_b = black

        alpha = (fade_idx + 1) / fade_frames
        blend_crossfade(frame_a, frame_b, alpha, out=blend_buf, temp=temp_buf)
        encoder.write_frame(blend_buf)


def assemble_streaming(
    clips: list,  # list[AssemblyClip]
    transitions: list[str],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fade_duration: float = 0.5,
    crf: int = 18,
    codec: str = "libx264",
    pix_fmt: str = "yuv420p",
) -> None:
    """Assemble clips via streaming frame blending. Constant memory.

    Decodes one clip at a time (two during crossfade zones), blends
    frames with numpy, and pipes to a single FFmpeg encode process.

    Does NOT mutate the input clips list. Carries decoder state
    forward between iterations to handle crossfade overlap.
    """
    if len(transitions) != len(clips) - 1:
        raise ValueError(f"Expected {len(clips) - 1} transitions, got {len(transitions)}")

    fade_frames = int(fade_duration * fps)
    encoder = StreamingEncoder(
        output_path, width, height, fps, crf=crf, pix_fmt=pix_fmt, codec=codec
    )
    encoder.start()

    blend_buf = np.zeros((height, width, 3), dtype=np.uint8)
    temp_buf = np.zeros((height, width, 3), dtype=np.uint8)

    # WHY: When clip A crossfades into clip B, the decoder for B has already
    # yielded fade_frames. We carry the iterator forward instead of creating
    # a new decoder (which would re-decode those frames).
    active_iter: Iterator[np.ndarray] | None = None
    skip_frames = 0

    try:
        for clip_idx, clip in enumerate(clips):
            if active_iter is None:
                active_iter = iter(FrameDecoder(clip.path, width, height, fps))

            clip_frames = int(clip.duration * fps)
            has_fade_out = clip_idx < len(transitions) and transitions[clip_idx] == "fade"
            body_frames = clip_frames - skip_frames - (fade_frames if has_fade_out else 0)

            _emit_body_frames(active_iter, body_frames, encoder)

            if has_fade_out and clip_idx + 1 < len(clips):
                next_iter = iter(FrameDecoder(clips[clip_idx + 1].path, width, height, fps))
                _emit_crossfade(
                    active_iter,
                    next_iter,
                    fade_frames,
                    encoder,
                    blend_buf,
                    temp_buf,
                    height,
                    width,
                )
                # Carry next clip's iterator — it already yielded fade_frames
                active_iter = next_iter
                skip_frames = fade_frames
            else:
                active_iter = None
                skip_frames = 0

        encoder.finish()
        logger.info(f"Streaming assembly complete: {len(clips)} clips → {output_path.name}")
    except Exception:
        encoder.finish()
        raise


def _build_audio_filter_graph(
    clips: list,
    transitions: list[str],
    fade_duration: float,
) -> str:
    """Build FFmpeg filter_complex string for audio crossfade/concat chain."""
    filter_parts: list[str] = [
        f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{clip.duration},asetpts=PTS-STARTPTS[a{i}]"
        for i, clip in enumerate(clips)
    ]

    current_label = "a0"
    for i, transition in enumerate(transitions):
        next_label = f"a{i + 1}"
        out_label = f"mix{i}" if i < len(transitions) - 1 else "aout"
        if transition == "fade":
            filter_parts.append(
                f"[{current_label}][{next_label}]"
                f"acrossfade=d={fade_duration}:c1=tri:c2=tri[{out_label}]"
            )
        else:
            filter_parts.append(f"[{current_label}][{next_label}]concat=n=2:v=0:a=1[{out_label}]")
        current_label = out_label

    return ";".join(filter_parts)


def _probe_max_audio_bitrate(clips: list) -> str:
    """Probe clips for highest audio bitrate. Returns e.g. "256k".

    Falls back to 192k if probing fails (reasonable for iPhone/modern cameras).
    """
    max_bitrate = 0
    for clip in clips:
        try:
            result = subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=bit_rate",
                    "-of",
                    "csv=p=0",
                    str(clip.path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                bitrate = int(result.stdout.strip())
                max_bitrate = max(max_bitrate, bitrate)
        except (ValueError, subprocess.TimeoutExpired):
            continue

    if max_bitrate <= 0:
        return "192k"
    # Round up to nearest standard AAC bitrate
    kbps = max_bitrate // 1000
    for standard in (96, 128, 160, 192, 256, 320):
        if kbps <= standard:
            return f"{standard}k"
    return "320k"


def extract_and_mix_audio(
    clips: list,
    transitions: list[str],
    output_path: Path,
    fade_duration: float = 0.5,
) -> None:
    """Extract audio from clips and mix with crossfade transitions.

    Runs a single FFmpeg command with audio-only inputs and acrossfade filters.
    Memory usage is negligible (audio is tiny compared to video frames).
    Output bitrate matches the highest source bitrate (no quality downgrade).
    """
    # WHY: Use the highest source bitrate so we never downgrade audio quality.
    # iPhone clips are typically 256k AAC; re-encoding at 128k loses quality.
    audio_bitrate = _probe_max_audio_bitrate(clips)
    logger.info(f"Audio output bitrate: {audio_bitrate} (matched to source max)")

    if len(clips) == 1:
        result = subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-i",
                str(clips[0].path),
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                audio_bitrate,
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {result.stderr[-500:]}")
        return

    inputs: list[str] = []
    for clip in clips:
        inputs.extend(["-i", str(clip.path)])

    filter_complex = _build_audio_filter_graph(clips, transitions, fade_duration)

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"Audio mixing failed: {result.stderr[-500:]}")
