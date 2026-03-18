"""Photo animator — converts still images to video clips via FFmpeg.

Uses filter expressions from filter_expressions.py to generate FFmpeg
commands that animate photos with Ken Burns, face zoom, blur background,
or collage effects. Each photo gets reproducible randomness via a seed
derived from its asset ID.

Supports HDR photos (HEIF/HEIC from iPhones): when hdr_type is provided,
outputs HEVC with 10-bit color and BT.2020 metadata.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.filter_expressions import (
    blur_bg_filter,
    face_zoom_filter,
    ken_burns_filter,
)
from immich_memories.photos.models import AnimationMode

# HDR transfer characteristic mapping
_HDR_COLOR_TRC = {
    "hlg": "arib-std-b67",
    "pq": "smpte2084",
}


class PhotoAnimator:
    """Converts photos to short .mp4 clips with animation effects."""

    def __init__(self, config: PhotoConfig, target_w: int, target_h: int) -> None:
        self._config = config
        self._target_w = target_w
        self._target_h = target_h

    def resolve_auto_mode(
        self,
        width: int,
        height: int,
        face_bbox: tuple[float, float, float, float] | None,
    ) -> AnimationMode:
        """Resolve AUTO mode to a concrete animation based on photo content."""
        if face_bbox is not None:
            return AnimationMode.FACE_ZOOM
        if height > width:
            return AnimationMode.BLUR_BG
        return AnimationMode.KEN_BURNS

    def build_ffmpeg_command(
        self,
        source_path: Path,
        output_path: Path,
        width: int,
        height: int,
        mode: AnimationMode,
        face_bbox: tuple[float, float, float, float] | None = None,
        asset_id: str = "",
        hdr_type: str | None = None,
    ) -> list[str]:
        """Build FFmpeg command to convert a photo to an animated .mp4 clip.

        Args:
            hdr_type: "hlg", "pq", or None for SDR. When set, outputs HEVC
                      with 10-bit color and HDR metadata.
        """
        if mode == AnimationMode.AUTO:
            mode = self.resolve_auto_mode(width, height, face_bbox)

        duration = self._config.duration
        fps = 30
        seed = self._seed_from_id(asset_id)
        encoder_args = self._encoder_args(hdr_type)

        if mode == AnimationMode.BLUR_BG:
            return self._build_filter_complex_command(
                source_path,
                output_path,
                duration,
                encoder_args,
                blur_bg_filter(
                    width, height, self._target_w, self._target_h, duration, fps, seed=seed
                ),
            )

        if mode == AnimationMode.FACE_ZOOM:
            vf = face_zoom_filter(
                width,
                height,
                self._target_w,
                self._target_h,
                duration,
                fps,
                face_bbox=face_bbox or (0.3, 0.2, 0.4, 0.5),
                seed=seed,
            )
        else:
            vf = ken_burns_filter(
                width,
                height,
                self._target_w,
                self._target_h,
                duration,
                fps,
                zoom_factor=self._config.zoom_factor,
                seed=seed,
            )

        return self._build_vf_command(source_path, output_path, duration, encoder_args, vf)

    def _encoder_args(self, hdr_type: str | None) -> list[str]:
        """Build encoder arguments — HEVC+10bit for HDR, H.264 for SDR."""
        if hdr_type and hdr_type in _HDR_COLOR_TRC:
            color_trc = _HDR_COLOR_TRC[hdr_type]
            return [
                "-c:v",
                "libx265",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p10le",
                "-colorspace",
                "bt2020nc",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                color_trc,
                "-x265-params",
                f"hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer={color_trc}:colormatrix=bt2020nc",
            ]
        return [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]

    def _build_vf_command(
        self,
        source_path: Path,
        output_path: Path,
        duration: float,
        encoder_args: list[str],
        vf: str,
    ) -> list[str]:
        """Build command using -vf (single-stream filter)."""
        return [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(source_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-vf",
            vf,
            *encoder_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            str(duration),
            "-shortest",
            str(output_path),
        ]

    def _build_filter_complex_command(
        self,
        source_path: Path,
        output_path: Path,
        duration: float,
        encoder_args: list[str],
        fc: str,
    ) -> list[str]:
        """Build command using -filter_complex (multi-stream filter like blur_bg)."""
        return [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(source_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            fc,
            *encoder_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            str(duration),
            "-shortest",
            str(output_path),
        ]

    def _seed_from_id(self, asset_id: str) -> int:
        """Derive a reproducible seed from an asset ID."""
        return int(hashlib.sha256(asset_id.encode()).hexdigest()[:8], 16)
