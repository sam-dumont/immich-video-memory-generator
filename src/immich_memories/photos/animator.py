"""Photo animator — converts still images to video clips via FFmpeg.

Uses filter expressions from filter_expressions.py to generate FFmpeg
commands that animate photos with Ken Burns, face zoom, blur background,
or collage effects. Each photo gets reproducible randomness via a seed
derived from its asset ID.
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
            # Portrait photo in landscape output → blur background
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
    ) -> list[str]:
        """Build FFmpeg command to convert a photo to an animated .mp4 clip."""
        if mode == AnimationMode.AUTO:
            mode = self.resolve_auto_mode(width, height, face_bbox)

        duration = self._config.duration
        fps = 30
        seed = self._seed_from_id(asset_id)

        # Build the video filter
        if mode == AnimationMode.BLUR_BG:
            return self._build_blur_bg_command(
                source_path, output_path, width, height, duration, fps, seed
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
            # KEN_BURNS (default)
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
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            str(duration),
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(output_path),
        ]

    def _build_blur_bg_command(
        self,
        source_path: Path,
        output_path: Path,
        width: int,
        height: int,
        duration: float,
        fps: int,
        seed: int,
    ) -> list[str]:
        """Build FFmpeg command for blur background mode.

        Uses filter_complex instead of -vf because blur_bg_filter
        has multiple streams (split, overlay).
        """
        fc = blur_bg_filter(
            width,
            height,
            self._target_w,
            self._target_h,
            duration,
            fps,
            seed=seed,
        )

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
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            str(duration),
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(output_path),
        ]

    def _seed_from_id(self, asset_id: str) -> int:
        """Derive a reproducible seed from an asset ID."""
        return int(hashlib.sha256(asset_id.encode()).hexdigest()[:8], 16)
