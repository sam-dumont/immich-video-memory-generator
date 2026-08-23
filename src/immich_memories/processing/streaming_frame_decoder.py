"""Decoding one clip into normalized frames for the streaming assembler.

Kept apart from the assembler because this is the source half of the pipe: it
owns the per-clip FFmpeg filter chain (rotation, privacy blur, fit/blur fill,
HDR transfer, captions) and hands back raw frames plus the clip's audio. The
assembler only consumes the frames.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from immich_memories.processing.clip_caption import ClipCaption, caption_filters
from immich_memories.processing.hdr_utilities import (
    _detect_color_primaries,
    _detect_hdr_type,
    _get_colorspace_filter,
    _resolve_clip_hdr,
)

logger = logging.getLogger(__name__)


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
        caption: ClipCaption | None = None,
        caption_font: str | None = None,
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
        self._caption = caption
        self._caption_font = caption_font

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
            # "fit": scale down inside the frame and pad the rest black. There is
            # no face-aware crop on the video path, so anything that is not
            # "blur" lands here — which is why no such mode is offered.
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

        # Apply the shared per-source transfer conversion and tag the decoded
        # frames before they enter the metadata-free rawvideo pipe.
        for color_filter in (
            self._hdr_conversion,
            self._colorspace_filter,
            self._output_pix_fmt,
        ):
            if color_filter:
                parts.append(color_filter.removeprefix(","))

        # Drawn last so the text is never scaled, padded or blurred with the
        # source, and lands in target-frame coordinates.
        if self._caption:
            parts.extend(
                caption_filters(
                    self._caption,
                    self._width,
                    self._height,
                    is_hdr=self._pix_fmt != "rgb24",
                    font_path=self._caption_font,
                )
            )

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


def make_decoder(
    clip: Any,
    clip_idx: int,
    width: int,
    height: int,
    fps: int,
    ctx: Any | None = None,
    privacy_mode: bool = False,
    caption: ClipCaption | None = None,
    caption_font: str | None = None,
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

    if ctx is None and Path(clip.path).exists():
        target_type = hdr_type or "sdr"
        source_types: list[str | None] = [None] * (clip_idx + 1)
        source_primaries: list[str | None] = [None] * (clip_idx + 1)
        source_types[clip_idx] = _detect_hdr_type(clip.path)
        source_primaries[clip_idx] = _detect_color_primaries(clip.path)
        ctx = SimpleNamespace(
            hdr_type=target_type,
            pix_fmt="yuv420p10le" if hdr_type else "yuv420p",
            clip_hdr_types=source_types,
            clip_primaries=source_primaries,
            colorspace_filter=_get_colorspace_filter(target_type),
        )

    # Title videos may be pre-encoded, but only an exact transfer match may
    # bypass conversion. The shared resolver makes the same decision for all clips.
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
        caption=caption if not is_title else None,
        caption_font=caption_font,
    )
