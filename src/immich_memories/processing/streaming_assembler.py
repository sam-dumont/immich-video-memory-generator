"""Streaming video assembler — constant-memory frame blending."""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

from immich_memories.processing.hdr_utilities import _resolve_clip_hdr
from immich_memories.processing.streaming_audio import (
    _probe_duration,
    extract_and_mix_audio,
    mux_video_audio,
)

logger = logging.getLogger(__name__)


def blend_crossfade(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    alpha: float,
    out: np.ndarray,
    temp: np.ndarray,
) -> None:
    """In-place crossfade blend: alpha=0 → frame_a, alpha=1 → frame_b."""
    # WHY: Two pre-allocated buffers avoid ALL temporaries during the blend.
    # At 4K (3840*2160*3 = 25 MB), even one temporary doubles memory per frame.
    inv_alpha = 1.0 - alpha
    np.multiply(frame_a, inv_alpha, out=out, casting="unsafe")
    np.multiply(frame_b, alpha, out=temp, casting="unsafe")
    np.add(out, temp, out=out, casting="unsafe")


class FrameDecoder:
    """Decode a video clip to raw frames via FFmpeg stdout pipe."""

    def __init__(
        self,
        clip_path: Path,
        width: int,
        height: int,
        fps: int,
        pix_fmt: str = "rgb24",
        rotation: int = 0,
        privacy_blur: bool = False,
        hdr_conversion: str = "",
        colorspace_filter: str = "",
        output_pix_fmt: str = "",
        scale_mode: str = "black",
        sdr_to_hdr_filter: str = "",
        input_seek: float = 0.0,
        audio_output: Path | None = None,
    ) -> None:
        self._clip_path = clip_path
        self._input_seek = input_seek
        self._audio_output = audio_output
        self._width = width
        self._height = height
        self._fps = fps
        self._pix_fmt = pix_fmt
        self._frame_size = width * height * 3  # Same for yuv420p10le and rgb24
        self._rotation = rotation
        self._privacy_blur = privacy_blur
        self._hdr_conversion = hdr_conversion
        self._colorspace_filter = colorspace_filter
        self._output_pix_fmt = output_pix_fmt
        self._scale_mode = scale_mode
        # WHY: SDR clips in HDR output need zscale to convert sRGB→HLG/PQ.
        # Without this, SDR full-range data piped as yuv420p10le gets
        # interpreted as TV-range HLG = red/wrong tint.
        self._sdr_to_hdr_filter = sdr_to_hdr_filter

    def _build_vf(self) -> str:
        """Build the -vf filter chain matching filter_builder.build_clip_video_filter."""
        parts: list[str] = []

        # Rotation (transpose/hflip) — must come before scale
        if self._rotation == 90:
            parts.append("transpose=1")
        elif self._rotation == 180:
            parts.append("hflip,vflip")
        elif self._rotation == 270:
            parts.append("transpose=2")

        # WHY: frosted glass effect — gaussian blur + noise texture + smooth.
        # Looks cinematic/artistic rather than surveillance-like pixelation.
        # Scales with shorter dimension so portrait/landscape match.
        if self._privacy_blur:
            short_side = min(self._width, self._height)
            sigma = int(short_side * 0.035)
            parts.append(f"gblur=sigma={sigma},noise=alls=15:allf=t,gblur=sigma=10")

        # PTS reset — critical for multi-clip concat
        parts.append("setpts=PTS-STARTPTS")

        # Scale + fill to target resolution
        if self._scale_mode == "blur":
            # WHY: Blur background fills the entire frame with a blurred, zoomed version
            # of the source, then overlays the sharp scaled version centered on top.
            # Uses split to avoid re-reading the source.
            # When privacy blur is active, skip the extra sigma=30 on the background
            # because the frame is already blurred — adding more makes it unrecognizable.
            bg_blur = "" if self._privacy_blur else ",gblur=sigma=30"
            parts.extend(
                (
                    "split[_bg][_fg]",
                    f"[_bg]scale={self._width}:{self._height}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={self._width}:{self._height}{bg_blur}[_blurred]",
                    f"[_fg]scale={self._width}:{self._height}:force_original_aspect_ratio=decrease:flags=lanczos[_sharp]",
                    "[_blurred][_sharp]overlay=(W-w)/2:(H-h)/2",
                )
            )
            self._use_filter_complex = True
        else:
            parts.extend(
                (
                    f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease:flags=lanczos",
                    f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2:black",
                )
            )
            self._use_filter_complex = False

        # FPS + timebase reset
        parts.append(f"fps={self._fps},settb=1/{self._fps}")

        # SDR→HDR conversion (only for SDR clips in HDR output)
        if self._sdr_to_hdr_filter:
            parts.append(self._sdr_to_hdr_filter)

        # Square pixels
        parts.append("setsar=1")

        return ",".join(parts)

    def __iter__(self) -> Iterator[np.ndarray]:
        """Yield decoded frames one at a time."""
        vf = self._build_vf()
        use_fc = getattr(self, "_use_filter_complex", False)

        if use_fc:
            # WHY: Blur background uses split which requires -filter_complex
            filter_args = ["-filter_complex", f"[0:v]{vf}[out]", "-map", "[out]"]
        else:
            filter_args = ["-vf", vf]

        seek_args = ["-ss", str(self._input_seek)] if self._input_seek > 0 else []

        # WHY: Extract audio alongside video in the same FFmpeg pass.
        # Audio timing matches the decoded video frames exactly, preventing
        # the cumulative drift from independent video/audio assembly.
        audio_args: list[str] = []
        if self._audio_output:
            audio_args = [
                "-map",
                "0:a?",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(self._audio_output),
            ]

        cmd = [
            "ffmpeg",
            *seek_args,
            "-i",
            str(self._clip_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            self._pix_fmt,
            *filter_args,
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "pipe:1",
            *audio_args,
        ]
        logger.debug(f"FrameDecoder cmd: {' '.join(cmd)}")
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
                frame: np.ndarray
                if self._pix_fmt == "yuv420p10le":
                    # WHY: Keep as flat uint16 — YUV planar can't reshape to (H,W,3).
                    # Crossfade blends each sample independently which works for all planes.
                    frame = np.frombuffer(raw, dtype=np.uint16).copy()
                else:
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(self._height, self._width, 3)
                yield frame
        finally:
            proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)


class StreamingEncoder:
    """Encode raw frames to video via FFmpeg stdin pipe."""

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        encoder_args: list[str] | None = None,
        hdr_type: str | None = None,
    ) -> None:
        self._output_path = output_path
        self._width = width
        self._height = height
        self._fps = fps
        self._encoder_args = encoder_args or [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
        # WHY: Frames arrive as rgb24 (sRGB). For HDR output, zscale converts
        # sRGB → HLG/PQ on the encoder side. Same pattern as photo pipeline.
        self._hdr_type = hdr_type
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start the FFmpeg encode process."""
        # WHY: For HDR, data arrives as yuv420p10le (native format, zero conversion).
        # The rawvideo pipe STRIPS color range metadata — without explicit flags,
        # the encoder assumes full range (0-1023) when data is tv range (64-940)
        # = washed out colors. Must tag input with color metadata.
        vf_args: list[str] = []
        input_color_args: list[str] = []
        if self._hdr_type == "hlg":
            input_color_args = [
                "-color_range",
                "tv",
                "-color_trc",
                "arib-std-b67",
                "-color_primaries",
                "bt2020",
                "-colorspace",
                "bt2020nc",
            ]
        elif self._hdr_type == "pq":
            input_color_args = [
                "-color_range",
                "tv",
                "-color_trc",
                "smpte2084",
                "-color_primaries",
                "bt2020",
                "-colorspace",
                "bt2020nc",
            ]

        # WHY: yuv420p10le for HDR (native format, zero conversion), rgb24 for SDR
        input_pix_fmt = "yuv420p10le" if self._hdr_type else "rgb24"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            input_pix_fmt,
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            *input_color_args,
            "-i",
            "pipe:0",
            *vf_args,
            *self._encoder_args,
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
        # WHY: Popen.wait() with stderr=PIPE deadlocks when FFmpeg fills the
        # 64KB OS pipe buffer with progress/warnings. Drain stderr first —
        # read() blocks until FFmpeg exits and closes its end of the pipe,
        # which is fine since stdin is already closed (FFmpeg will finish).
        stderr_bytes = self._proc.stderr.read() if self._proc.stderr else b""
        self._proc.wait(timeout=3600)
        if self._proc.returncode != 0:
            stderr = stderr_bytes.decode(errors="replace")
            raise RuntimeError(
                f"Streaming encode failed (exit {self._proc.returncode}): {stderr[-500:]}"
            )


def _emit_body_frames(
    active_iter: Iterator[np.ndarray],
    count: int,
    encoder: StreamingEncoder,
    progress_callback: Callable[[int, int], None] | None = None,
    frames_written: int = 0,
    total_frames: int = 0,
    report_interval: int = 1,
    frame_preview_callback: Callable[[bytes], None] | None = None,
    last_preview_time: float = 0.0,
    is_hdr: bool = False,
    preview_height: int = 0,
    preview_width: int = 0,
) -> tuple[int, float]:
    """Write body frames to the encoder, optionally reporting progress.

    Returns (frames_written, last_preview_time).
    """
    from immich_memories.processing.frame_preview import _maybe_emit_preview

    for emitted in range(max(count, 0)):
        frame = next(active_iter, None)
        if frame is None:
            if emitted < count:
                logger.warning(
                    f"Frame underrun: expected {count} frames, got {emitted} "
                    f"(missing {count - emitted} frames = {(count - emitted) / max(total_frames, 1) * 100:.1f}%)"
                )
            break
        encoder.write_frame(frame)
        frames_written += 1
        if progress_callback and frames_written % report_interval == 0:
            progress_callback(frames_written, total_frames)
        last_preview_time = _maybe_emit_preview(
            frame,
            last_preview_time,
            frame_preview_callback,
            is_hdr,
            preview_height,
            preview_width,
        )
    return frames_written, last_preview_time


def _match_blend_bufs(
    ref: np.ndarray, blend_buf: np.ndarray, temp_buf: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create black frame and ensure blend buffers match actual frame shape.

    WHY: HDR mode pre-allocates flat uint16 YUV buffers, but some clips
    (title screens, FFmpeg filter fallback on Linux) may decode as 3D RGB.
    """
    black = np.zeros_like(ref)
    # WHY: in YUV, all-zeros = GREEN (U=0, V=0 = green chroma).
    # For flat uint16 arrays (yuv420p10le), set chroma planes to 512.
    if ref.ndim == 1 and ref.dtype == np.uint16:
        # Y plane occupies first 2/3 of the flat array, U+V the last 1/3
        y_size = len(ref) * 2 // 3
        black[y_size:] = 512
    if blend_buf.shape != ref.shape or blend_buf.dtype != ref.dtype:
        blend_buf = np.zeros_like(ref)
        temp_buf = np.zeros_like(ref)
    return black, blend_buf, temp_buf


def _hold_or_fallback(
    frame: np.ndarray | None,
    last: np.ndarray | None,
    black: np.ndarray,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Return frame (or held last frame) and update last-seen cache."""
    if frame is not None:
        return frame, frame
    return (last if last is not None else black), last


def _emit_crossfade(
    active_iter: Iterator[np.ndarray],
    next_iter: Iterator[np.ndarray],
    fade_frames: int,
    encoder: StreamingEncoder,
    blend_buf: np.ndarray,
    temp_buf: np.ndarray,
    height: int,
    width: int,
    frame_preview_callback: Callable[[bytes], None] | None = None,
    last_preview_time: float = 0.0,
    is_hdr: bool = False,
) -> float:
    """Blend fade_frames from two iterators and write to encoder.

    Returns updated last_preview_time.
    """
    from immich_memories.processing.frame_preview import _maybe_emit_preview

    black: np.ndarray | None = None
    last_a: np.ndarray | None = None
    last_b: np.ndarray | None = None
    for fade_idx in range(fade_frames):
        frame_a = next(active_iter, None)
        frame_b = next(next_iter, None)

        if frame_a is None is frame_b:
            break

        if black is None:
            ref = frame_a if frame_a is not None else frame_b
            assert ref is not None  # noqa: S101
            black, blend_buf, temp_buf = _match_blend_bufs(ref, blend_buf, temp_buf)

        frame_a, last_a = _hold_or_fallback(frame_a, last_a, black)
        frame_b, last_b = _hold_or_fallback(frame_b, last_b, black)

        alpha = (fade_idx + 1) / fade_frames
        blend_crossfade(frame_a, frame_b, alpha, out=blend_buf, temp=temp_buf)
        encoder.write_frame(blend_buf)
        last_preview_time = _maybe_emit_preview(
            blend_buf,
            last_preview_time,
            frame_preview_callback,
            is_hdr,
            height,
            width,
        )
    return last_preview_time


def _make_decoder(
    clip: Any,
    clip_idx: int,
    width: int,
    height: int,
    fps: int,
    ctx: Any | None = None,
    privacy_mode: bool = False,
    scale_mode: str = "black",
    hdr_type: str | None = None,
    audio_work_dir: Path | None = None,
) -> FrameDecoder:
    """Create a FrameDecoder with per-clip normalization filters."""
    rotation = 0
    is_title = getattr(clip, "is_title_screen", False)

    rotation_override = getattr(clip, "rotation_override", None)
    if rotation_override is not None and rotation_override != 0:
        rotation = rotation_override

    # WHY: title screens are already encoded with the correct HDR settings
    # by the title generator. Skip per-clip HDR detection which can fail
    # when the context was rebuilt from the extended clip list (shifted indices).
    if is_title and hdr_type:
        hdr_conversion = ""
        colorspace_filter = ""
        output_pix_fmt = ""
        sdr_to_hdr_filter = ""
    else:
        hdr_conversion, colorspace_filter, output_pix_fmt, sdr_to_hdr_filter, _ = _resolve_clip_hdr(
            clip_idx, ctx, hdr_type
        )
    pix_fmt = "yuv420p10le" if hdr_type else "rgb24"
    logger.info(
        f"Decoder[{clip_idx}] pix={pix_fmt} title={is_title} hdr_type={hdr_type} "
        f"sdr2hdr={bool(sdr_to_hdr_filter)} {clip.path.name}"
    )

    audio_output = None
    if audio_work_dir:
        audio_output = audio_work_dir / f"clip_{clip_idx}_audio.wav"

    return FrameDecoder(
        clip_path=clip.path,
        width=width,
        height=height,
        fps=fps,
        pix_fmt=pix_fmt,
        rotation=rotation,
        privacy_blur=privacy_mode and not is_title,
        hdr_conversion=hdr_conversion,
        colorspace_filter=colorspace_filter,
        output_pix_fmt=output_pix_fmt,
        scale_mode=scale_mode,
        sdr_to_hdr_filter=sdr_to_hdr_filter,
        input_seek=getattr(clip, "input_seek", 0.0),
        audio_output=audio_output,
    )


def _estimate_total_frames(
    clips: list, transitions: list[str], fps: int, fade_duration: float
) -> int:
    """Estimate total output frames accounting for crossfade overlap."""
    fade_frames = int(fade_duration * fps)
    total = sum(int(c.duration * fps) for c in clips)
    fade_count = sum(1 for t in transitions if t == "fade")
    return max(1, total - fade_count * fade_frames)


def _alloc_blend_bufs(
    width: int, height: int, hdr_type: str | None
) -> tuple[np.ndarray, np.ndarray]:
    """Allocate blend and temp buffers for crossfade blending."""
    if hdr_type:
        # WHY: yuv420p10le is flat uint16 — W*H*3 bytes = W*H*3/2 uint16 samples
        n = width * height * 3 // 2
        return np.zeros(n, dtype=np.uint16), np.zeros(n, dtype=np.uint16)
    return np.zeros((height, width, 3), dtype=np.uint8), np.zeros(
        (height, width, 3), dtype=np.uint8
    )


def assemble_streaming(
    clips: list,
    transitions: list[str],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fade_duration: float = 0.5,
    encoder_args: list[str] | None = None,
    ctx: Any | None = None,
    privacy_mode: bool = False,
    hdr_type: str | None = None,
    scale_mode: str = "blur",
    progress_callback: Callable[[int, int], None] | None = None,
    frame_preview_callback: Callable[[bytes], None] | None = None,
    audio_work_dir: Path | None = None,
) -> list[Path]:
    """Assemble clips via streaming frame blending (constant memory).

    Returns list of per-clip audio WAV paths extracted during decoding.
    """
    if len(transitions) != len(clips) - 1:
        raise ValueError(f"Expected {len(clips) - 1} transitions, got {len(transitions)}")

    fade_frames = int(fade_duration * fps)
    total_frames = _estimate_total_frames(clips, transitions, fps, fade_duration)
    encoder = StreamingEncoder(
        output_path, width, height, fps, encoder_args=encoder_args, hdr_type=hdr_type
    )
    encoder.start()
    blend_buf, temp_buf = _alloc_blend_bufs(width, height, hdr_type)
    # WHY: Throttle callbacks to every ~0.5s worth of frames to avoid UI overhead
    report_interval = max(1, fps // 2)

    try:
        _encode_clip_sequence(
            clips,
            transitions,
            encoder,
            fade_frames,
            total_frames,
            report_interval,
            blend_buf,
            temp_buf,
            width,
            height,
            fps,
            ctx,
            privacy_mode,
            scale_mode,
            hdr_type,
            progress_callback,
            frame_preview_callback,
            audio_work_dir=audio_work_dir,
        )
        encoder.finish()
        if progress_callback:
            progress_callback(total_frames, total_frames)
        logger.info(f"Streaming assembly complete: {len(clips)} clips → {output_path.name}")
    except (
        Exception
    ):  # WHY: cleanup safety net — ensures encoder.finish() even on unexpected errors
        encoder.finish()
        raise

    # Collect audio WAV files extracted by FrameDecoder during the encode pass
    audio_paths: list[Path] = []
    if audio_work_dir:
        for clip_idx in range(len(clips)):
            wav = audio_work_dir / f"clip_{clip_idx}_audio.wav"
            if wav.exists():
                audio_paths.append(wav)
            else:
                audio_paths.append(Path())
    return audio_paths


def _encode_clip_sequence(
    clips: list,
    transitions: list[str],
    encoder: StreamingEncoder,
    fade_frames: int,
    total_frames: int,
    report_interval: int,
    blend_buf: np.ndarray,
    temp_buf: np.ndarray,
    width: int,
    height: int,
    fps: int,
    ctx: Any | None,
    privacy_mode: bool,
    scale_mode: str,
    hdr_type: str | None,
    progress_callback: Callable[[int, int], None] | None,
    frame_preview_callback: Callable[[bytes], None] | None = None,
    audio_work_dir: Path | None = None,
) -> int:
    """Encode all clips with transitions, tracking frame count for progress."""
    active_iter: Iterator[np.ndarray] | None = None
    skip_frames = 0
    frames_written = 0
    last_preview_time = 0.0
    is_hdr = hdr_type is not None

    for clip_idx, clip in enumerate(clips):
        if active_iter is None:
            decoder = _make_decoder(
                clip,
                clip_idx,
                width,
                height,
                fps,
                ctx,
                privacy_mode,
                scale_mode,
                hdr_type,
                audio_work_dir=audio_work_dir,
            )
            active_iter = iter(decoder)

        clip_frames = int(clip.duration * fps)
        has_fade_out = clip_idx < len(transitions) and transitions[clip_idx] == "fade"
        body_frames = clip_frames - skip_frames - (fade_frames if has_fade_out else 0)

        frames_written, last_preview_time = _emit_body_frames(
            active_iter,
            body_frames,
            encoder,
            progress_callback,
            frames_written,
            total_frames,
            report_interval,
            frame_preview_callback,
            last_preview_time,
            is_hdr,
            height,
            width,
        )

        if has_fade_out and clip_idx + 1 < len(clips):
            next_decoder = _make_decoder(
                clips[clip_idx + 1],
                clip_idx + 1,
                width,
                height,
                fps,
                ctx,
                privacy_mode,
                scale_mode,
                hdr_type,
                audio_work_dir=audio_work_dir,
            )
            next_iter = iter(next_decoder)
            last_preview_time = _emit_crossfade(
                active_iter,
                next_iter,
                fade_frames,
                encoder,
                blend_buf,
                temp_buf,
                height,
                width,
                frame_preview_callback,
                last_preview_time,
                is_hdr,
            )
            frames_written += fade_frames
            if progress_callback:
                progress_callback(frames_written, total_frames)
            active_iter = next_iter
            skip_frames = fade_frames
        else:
            active_iter = None
            skip_frames = 0

    # WHY: The last FrameDecoder's FFmpeg process inherits the encoder's
    # stdin pipe FD. If not closed before encoder.finish(), the pipe never
    # sees EOF and the encoder hangs waiting for input. Force-close the
    # last iterator to trigger FrameDecoder.__iter__'s finally block
    # (proc.terminate + wait), ensuring the FD is released.
    if active_iter is not None and hasattr(active_iter, "close"):
        active_iter.close()
    return frames_written


def streaming_assemble_full(
    clips: list,
    transitions: list[str],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fade_duration: float = 0.5,
    encoder_args: list[str] | None = None,
    ctx: Any | None = None,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
    hdr_type: str | None = None,
    scale_mode: str = "blur",
    progress_callback: Callable[[float, str], None] | None = None,
    frame_preview_callback: Callable[[bytes], None] | None = None,
) -> Path:
    """Full streaming assembly: video encode + audio mix + mux → final MP4."""
    work_dir = output_path.parent / ".streaming_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    video_only = work_dir / "video.mp4"
    audio_only = work_dir / "audio.m4a"
    audio_work_dir = work_dir / "audio_clips"
    audio_work_dir.mkdir(exist_ok=True)

    try:
        if progress_callback:
            progress_callback(0.07, "Streaming video assembly...")

        # WHY: Scale frame-level progress into [0.07, 0.85) range so the caller
        # sees continuous updates during the heavy encode phase.
        def _frame_progress(frames_done: int, frames_total: int) -> None:
            if progress_callback and frames_total > 0:
                frac = frames_done / frames_total
                scaled = 0.07 + frac * 0.80
                total_secs = frames_total / fps if fps > 0 else 0
                done_secs = frames_done / fps if fps > 0 else 0
                time_done = f"{int(done_secs // 60)}:{int(done_secs % 60):02d}"
                time_total = f"{int(total_secs // 60)}:{int(total_secs % 60):02d}"
                progress_callback(
                    scaled,
                    f"Encoding ({time_done} / {time_total}) — {frac * 100:.0f}%",
                )

        # WHY: Extract audio in the same FFmpeg pass as video decoding.
        # This eliminates the separate audio extraction pass and ensures
        # audio timing matches decoded video frames exactly.
        clip_audio_paths = assemble_streaming(
            clips=clips,
            transitions=transitions,
            output_path=video_only,
            width=width,
            height=height,
            fps=fps,
            fade_duration=fade_duration,
            encoder_args=encoder_args,
            ctx=ctx,
            privacy_mode=privacy_mode,
            hdr_type=hdr_type,
            scale_mode=scale_mode,
            progress_callback=_frame_progress,
            frame_preview_callback=frame_preview_callback,
            audio_work_dir=audio_work_dir,
        )

        if progress_callback:
            progress_callback(0.85, "Mixing audio...")

        # WHY: Probe actual video duration so the audio filter graph can
        # clamp its output to match. This avoids re-encoding audio in the
        # mux step (which would cause double-AAC priming delay).
        video_dur = _probe_duration(video_only)

        extract_and_mix_audio(
            clips=clips,
            transitions=transitions,
            output_path=audio_only,
            fade_duration=fade_duration,
            fps=fps,
            normalize_audio=normalize_audio,
            privacy_mode=privacy_mode,
            pre_extracted_audio=clip_audio_paths,
            video_duration=video_dur,
        )

        if progress_callback:
            progress_callback(0.95, "Muxing final output...")

        mux_video_audio(video_only, audio_only, output_path)

        logger.info(f"Full streaming assembly complete: {len(clips)} clips → {output_path.name}")
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
