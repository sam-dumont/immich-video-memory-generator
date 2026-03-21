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
        scale_mode: str = "black",
        sdr_to_hdr_filter: str = "",
    ) -> None:
        self._clip_path = clip_path
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

        # Privacy blur
        if self._privacy_blur:
            parts.append("gblur=sigma=80")

        # PTS reset — critical for multi-clip concat
        parts.append("setpts=PTS-STARTPTS")

        # Scale + fill to target resolution
        if self._scale_mode == "blur":
            # WHY: Blur background fills the entire frame with a blurred, zoomed version
            # of the source, then overlays the sharp scaled version centered on top.
            # Uses split to avoid re-reading the source.
            parts.extend(
                (
                    "split[_bg][_fg]",
                    f"[_bg]scale={self._width}:{self._height}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={self._width}:{self._height},gblur=sigma=30[_blurred]",
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

        cmd = [
            "ffmpeg",
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
        self._proc.wait(timeout=3600)
        if self._proc.returncode != 0:
            stderr = self._proc.stderr.read().decode(errors="replace") if self._proc.stderr else ""
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
) -> int:
    """Write body frames to the encoder, optionally reporting progress.

    Returns the updated frames_written count.
    """
    for _ in range(max(count, 0)):
        frame = next(active_iter, None)
        if frame is None:
            break
        encoder.write_frame(frame)
        frames_written += 1
        if progress_callback and frames_written % report_interval == 0:
            progress_callback(frames_written, total_frames)
    return frames_written


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
    black = np.zeros((height, width, 3), dtype=blend_buf.dtype)
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


def _resolve_clip_hdr(
    clip_idx: int, ctx: Any | None, hdr_type: str | None
) -> tuple[str, str, str, str, bool]:
    """Resolve per-clip HDR settings from AssemblyContext.

    Returns (hdr_conversion, colorspace_filter, output_pix_fmt, sdr_to_hdr_filter, clip_is_hdr).
    """
    hdr_conversion = ""
    colorspace_filter = ""
    output_pix_fmt = ""
    clip_is_hdr = False

    if ctx is not None:
        output_pix_fmt = getattr(ctx, "pix_fmt", "")
        colorspace_filter = getattr(ctx, "colorspace_filter", "")
        clip_hdr_types = getattr(ctx, "clip_hdr_types", [])
        clip_primaries = getattr(ctx, "clip_primaries", [])
        dominant_hdr = getattr(ctx, "hdr_type", "")

        if clip_idx < len(clip_hdr_types):
            clip_is_hdr = clip_hdr_types[clip_idx] is not None
            if clip_hdr_types[clip_idx] != dominant_hdr:
                from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

                source_pri = clip_primaries[clip_idx] if clip_idx < len(clip_primaries) else None
                hdr_conversion = _get_hdr_conversion_filter(
                    clip_hdr_types[clip_idx], dominant_hdr, source_primaries=source_pri
                )

    # WHY: SDR clip in HDR output needs zscale sRGB→HLG/PQ conversion.
    # Without this, SDR full-range data tagged as TV-range HLG = red tint.
    sdr_to_hdr_filter = ""
    if hdr_type and not clip_is_hdr:
        trc = "arib-std-b67" if hdr_type == "hlg" else "smpte2084"
        sdr_to_hdr_filter = (
            f"zscale=t={trc}:tin=iec61966-2-1"
            ":p=bt2020:pin=bt709"
            ":m=bt2020nc:min=bt709"
            ":npl=203"
            ",format=yuv420p10le"
        )

    return hdr_conversion, colorspace_filter, output_pix_fmt, sdr_to_hdr_filter, clip_is_hdr


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
) -> FrameDecoder:
    """Create a FrameDecoder with per-clip normalization filters."""
    rotation = 0
    is_title = getattr(clip, "is_title_screen", False)

    rotation_override = getattr(clip, "rotation_override", None)
    if rotation_override is not None and rotation_override != 0:
        rotation = rotation_override

    hdr_conversion, colorspace_filter, output_pix_fmt, sdr_to_hdr_filter, _ = _resolve_clip_hdr(
        clip_idx, ctx, hdr_type
    )
    pix_fmt = "yuv420p10le" if hdr_type else "rgb24"

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
        )
        encoder.finish()
        if progress_callback:
            progress_callback(total_frames, total_frames)
        logger.info(f"Streaming assembly complete: {len(clips)} clips → {output_path.name}")
    except Exception:
        encoder.finish()
        raise


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
) -> int:
    """Encode all clips with transitions, tracking frame count for progress."""
    active_iter: Iterator[np.ndarray] | None = None
    skip_frames = 0
    frames_written = 0

    for clip_idx, clip in enumerate(clips):
        if active_iter is None:
            decoder = _make_decoder(
                clip, clip_idx, width, height, fps, ctx, privacy_mode, scale_mode, hdr_type
            )
            active_iter = iter(decoder)

        clip_frames = int(clip.duration * fps)
        has_fade_out = clip_idx < len(transitions) and transitions[clip_idx] == "fade"
        body_frames = clip_frames - skip_frames - (fade_frames if has_fade_out else 0)

        frames_written = _emit_body_frames(
            active_iter,
            body_frames,
            encoder,
            progress_callback,
            frames_written,
            total_frames,
            report_interval,
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
            frames_written += fade_frames
            if progress_callback:
                progress_callback(frames_written, total_frames)
            active_iter = next_iter
            skip_frames = fade_frames
        else:
            active_iter = None
            skip_frames = 0

    return frames_written


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


def _probe_duration(path: Path) -> float:
    """Get actual duration of a media file via ffprobe."""
    result = subprocess.run(  # noqa: S603, S607
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(result.stdout.strip()) if result.returncode == 0 else 0.0


def mux_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    """Mux video and audio streams into final output.

    Pads or trims audio to match video duration to prevent desync from
    frame count rounding differences between video and audio passes.
    """
    video_dur = _probe_duration(video_path)
    # WHY: Audio and video passes produce slightly different durations due to
    # frame count rounding. Use apad+atrim to force audio to exact video length.
    # apad extends if audio is shorter, atrim trims if longer.
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-af",
        f"apad=whole_dur={video_dur},atrim=0:{video_dur}",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # noqa: S603, S607
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
    ctx: Any | None = None,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
    hdr_type: str | None = None,
    scale_mode: str = "blur",
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    """Full streaming assembly: video + audio → final MP4.

    Three phases:
    1. Streaming video encode (frame-by-frame, constant memory)
    2. Audio extraction + mixing (separate FFmpeg pass, lightweight)
    3. Mux video + audio (trim audio to video duration for sync)

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

        # WHY: Scale frame-level progress into [0.05, 0.85) range so the caller
        # sees continuous updates during the heavy encode phase.
        def _frame_progress(frames_done: int, frames_total: int) -> None:
            if progress_callback and frames_total > 0:
                frac = frames_done / frames_total
                scaled = 0.05 + frac * 0.80
                total_secs = frames_total / fps if fps > 0 else 0
                done_secs = frames_done / fps if fps > 0 else 0
                time_done = f"{int(done_secs // 60)}:{int(done_secs % 60):02d}"
                time_total = f"{int(total_secs // 60)}:{int(total_secs % 60):02d}"
                progress_callback(
                    scaled,
                    f"Encoding ({time_done} / {time_total}) — {frac * 100:.0f}%",
                )

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
            hdr_type=hdr_type,
            scale_mode=scale_mode,
            progress_callback=_frame_progress,
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
