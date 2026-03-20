"""Streaming video assembler — constant-memory frame blending.

Decodes clips one at a time, blends crossfade transitions with numpy,
and pipes frames to a single FFmpeg encode process. Memory stays constant
regardless of clip count (~550 MB at 4K, ~300 MB at 1080p).

Extends the proven photo pipeline pattern (photos/renderer.py + photo_pipeline.py).
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

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

    Applies per-clip normalization: rotation, privacy blur, PTS reset,
    scale/pad, fps, timebase, and format conversion — matching the old
    filter graph pipeline (filter_builder.build_clip_video_filter).
    """

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
    ) -> None:
        self._clip_path = clip_path
        self._width = width
        self._height = height
        self._fps = fps
        self._pix_fmt = pix_fmt
        self._frame_size = width * height * 3  # rgb24 = 3 bytes/pixel
        self._rotation = rotation
        self._privacy_blur = privacy_blur
        self._hdr_conversion = hdr_conversion
        self._colorspace_filter = colorspace_filter
        self._output_pix_fmt = output_pix_fmt

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

        # Privacy blur
        if self._privacy_blur:
            parts.append("gblur=sigma=80")

        # PTS reset — critical for multi-clip concat
        parts.append("setpts=PTS-STARTPTS")

        # Scale + pad to target resolution
        parts.extend(
            (
                f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease:flags=lanczos",
                f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2:black",
            )
        )

        # FPS + timebase reset
        parts.append(f"fps={self._fps},settb=1/{self._fps}")

        # Pixel format conversion (yuv420p for SDR, p010le for HDR)
        if self._output_pix_fmt:
            parts.append(f"format={self._output_pix_fmt}")

        # HDR conversion (PQ→HLG, HLG→PQ etc.) + colorspace
        if self._hdr_conversion:
            parts.append(self._hdr_conversion.lstrip(","))
        if self._colorspace_filter:
            parts.append(self._colorspace_filter.lstrip(","))

        # Square pixels
        parts.append("setsar=1")

        return ",".join(parts)

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
            self._build_vf(),
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

    Accepts encoder_args from _get_gpu_encoder_args() for GPU acceleration
    and HDR support (VideoToolbox, NVENC, VAAPI, or CPU fallback).
    """

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        encoder_args: list[str] | None = None,
    ) -> None:
        self._output_path = output_path
        self._width = width
        self._height = height
        self._fps = fps
        # WHY: encoder_args comes from _get_gpu_encoder_args() which handles
        # GPU detection, HDR metadata, and platform-specific encoder selection.
        # Default to libx264 for tests that don't pass encoder_args.
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


def _make_decoder(
    clip: Any,
    clip_idx: int,
    width: int,
    height: int,
    fps: int,
    ctx: Any | None = None,
    privacy_mode: bool = False,
) -> FrameDecoder:
    """Create a FrameDecoder with per-clip normalization filters.

    Reads rotation, HDR type, and colorspace from clip + AssemblyContext
    to match the old filter_builder.build_clip_video_filter() behavior.
    """
    rotation = 0
    hdr_conversion = ""
    colorspace_filter = ""
    output_pix_fmt = ""
    is_title = getattr(clip, "is_title_screen", False)

    rotation_override = getattr(clip, "rotation_override", None)
    if rotation_override is not None and rotation_override != 0:
        rotation = rotation_override

    if ctx is not None:
        output_pix_fmt = getattr(ctx, "pix_fmt", "")
        colorspace_filter = getattr(ctx, "colorspace_filter", "")

        # Per-clip HDR conversion (PQ→HLG, HLG→PQ etc.)
        clip_hdr_types = getattr(ctx, "clip_hdr_types", [])
        clip_primaries = getattr(ctx, "clip_primaries", [])
        dominant_hdr = getattr(ctx, "hdr_type", "")

        if clip_idx < len(clip_hdr_types) and clip_hdr_types[clip_idx] != dominant_hdr:
            from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

            source_pri = clip_primaries[clip_idx] if clip_idx < len(clip_primaries) else None
            hdr_conversion = _get_hdr_conversion_filter(
                clip_hdr_types[clip_idx], dominant_hdr, source_primaries=source_pri
            )

    return FrameDecoder(
        clip_path=clip.path,
        width=width,
        height=height,
        fps=fps,
        rotation=rotation,
        privacy_blur=privacy_mode and not is_title,
        hdr_conversion=hdr_conversion,
        colorspace_filter=colorspace_filter,
        output_pix_fmt=output_pix_fmt,
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
    ctx: object | None = None,
    privacy_mode: bool = False,
) -> None:
    """Assemble clips via streaming frame blending. Constant memory.

    Decodes one clip at a time (two during crossfade zones), blends
    frames with numpy, and pipes to a single FFmpeg encode process.

    Per-clip normalization (rotation, HDR, privacy, PTS, scale) is applied
    in the FrameDecoder filter chain — matching the old filter graph pipeline.

    Does NOT mutate the input clips list.
    """
    if len(transitions) != len(clips) - 1:
        raise ValueError(f"Expected {len(clips) - 1} transitions, got {len(transitions)}")

    fade_frames = int(fade_duration * fps)
    encoder = StreamingEncoder(output_path, width, height, fps, encoder_args=encoder_args)
    encoder.start()

    blend_buf = np.zeros((height, width, 3), dtype=np.uint8)
    temp_buf = np.zeros((height, width, 3), dtype=np.uint8)

    active_iter: Iterator[np.ndarray] | None = None
    skip_frames = 0

    try:
        for clip_idx, clip in enumerate(clips):
            if active_iter is None:
                decoder = _make_decoder(clip, clip_idx, width, height, fps, ctx, privacy_mode)
                active_iter = iter(decoder)

            clip_frames = int(clip.duration * fps)
            has_fade_out = clip_idx < len(transitions) and transitions[clip_idx] == "fade"
            body_frames = clip_frames - skip_frames - (fade_frames if has_fade_out else 0)

            _emit_body_frames(active_iter, body_frames, encoder)

            if has_fade_out and clip_idx + 1 < len(clips):
                next_decoder = _make_decoder(
                    clips[clip_idx + 1], clip_idx + 1, width, height, fps, ctx, privacy_mode
                )
                next_iter = iter(next_decoder)
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
    normalize_audio: bool = True,
    privacy_mode: bool = False,
) -> str:
    """Build FFmpeg filter_complex string for audio crossfade/concat chain.

    Matches filter_builder.build_audio_prep_filters():
    - loudnorm per clip (EBU R128, I=-16, TP=-1.5, LRA=11)
    - Privacy muffle (lowpass=f=200 — makes speech unintelligible)
    - Title screens get null audio source
    - aresample=async=1 + apad for duration safety
    """
    audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if normalize_audio else ""
    privacy_muffle = ",lowpass=f=200" if privacy_mode else ""

    filter_parts: list[str] = []
    for i, clip in enumerate(clips):
        is_title = getattr(clip, "is_title_screen", False)
        clip_loudnorm = loudnorm if not is_title else ""

        if is_title:
            # Title screens have no audio — generate silence
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}]"
            )
        else:
            filter_parts.append(
                f"[{i}:a]{audio_format},aresample=async=1,"
                f"asetpts=PTS-STARTPTS{clip_loudnorm}{privacy_muffle},"
                f"apad=whole_dur={clip.duration},atrim=0:{clip.duration}[a{i}]"
            )

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
    normalize_audio: bool = True,
    privacy_mode: bool = False,
) -> None:
    """Extract audio from clips and mix with crossfade transitions.

    Runs a single FFmpeg command with audio-only inputs and acrossfade filters.
    Memory usage is negligible (audio is tiny compared to video frames).
    Output bitrate matches the highest source bitrate (no quality downgrade).
    Applies loudnorm and privacy muffle matching the old filter graph pipeline.
    """
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

    # WHY: Title screens have no audio file — we generate silence via lavfi.
    # Use -f lavfi for title screen inputs, -i for real clips.
    inputs: list[str] = []
    for clip in clips:
        is_title = getattr(clip, "is_title_screen", False)
        if is_title:
            inputs.extend(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={clip.duration}"])
        else:
            inputs.extend(["-i", str(clip.path)])

    filter_complex = _build_audio_filter_graph(
        clips, transitions, fade_duration, normalize_audio, privacy_mode
    )

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


def mux_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    """Mux video and audio streams into final output. No re-encoding."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603, S607
    if result.returncode != 0:
        raise RuntimeError(f"Muxing failed: {result.stderr[-500:]}")


def streaming_assemble_full(
    clips: list,
    transitions: list[str],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fade_duration: float = 0.5,
    encoder_args: list[str] | None = None,
    ctx: object | None = None,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    """Full streaming assembly: video + audio → final MP4.

    Three phases:
    1. Streaming video encode (frame-by-frame, constant memory)
    2. Audio extraction + mixing (separate FFmpeg pass, lightweight)
    3. Mux video + audio (copy streams, no re-encode)

    Supports GPU encoding, HDR preservation, rotation, loudnorm, and
    privacy mode — full parity with the old filter graph pipeline.
    """
    work_dir = output_path.parent / ".streaming_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    video_only = work_dir / "video.mp4"
    audio_only = work_dir / "audio.m4a"

    try:
        if progress_callback:
            progress_callback(0.05, "Streaming video assembly...")

        assemble_streaming(
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
        )

        if progress_callback:
            progress_callback(0.85, "Mixing audio...")

        extract_and_mix_audio(
            clips=clips,
            transitions=transitions,
            output_path=audio_only,
            fade_duration=fade_duration,
            normalize_audio=normalize_audio,
            privacy_mode=privacy_mode,
        )

        if progress_callback:
            progress_callback(0.95, "Muxing final output...")

        mux_video_audio(video_only, audio_only, output_path)

        logger.info(f"Full streaming assembly complete: {len(clips)} clips → {output_path.name}")
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
