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
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PILImage

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.filter_expressions import (
    blur_bg_filter,
    face_zoom_filter,
    ken_burns_filter,
)
from immich_memories.photos.models import AnimationMode

logger = logging.getLogger(__name__)

# HDR transfer characteristic mapping
_HDR_COLOR_TRC = {
    "hlg": "arib-std-b67",
    "pq": "smpte2084",
}

# Extensions that FFmpeg can read directly as images
_FFMPEG_NATIVE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

# Extensions that need pillow-heif conversion
_HEIF_EXTENSIONS = {".heic", ".heif", ".avif"}


@dataclass
class PreparedPhoto:
    """Result of preparing a photo for FFmpeg animation."""

    path: Path
    width: int
    height: int
    has_gain_map: bool = False


def prepare_photo_source(source_path: Path, work_dir: Path) -> PreparedPhoto:
    """Convert any image format to an FFmpeg-compatible source.

    HEIC/HEIF/AVIF: decoded via pillow-heif → saved as high-quality JPEG.
    JPEG/PNG/WebP: used directly (FFmpeg handles these natively).

    Returns PreparedPhoto with the path to the FFmpeg-compatible file,
    plus dimensions extracted from the image.
    """
    ext = source_path.suffix.lower()

    if ext in _HEIF_EXTENSIONS:
        return _convert_heif(source_path, work_dir)

    if ext in _FFMPEG_NATIVE_EXTENSIONS:
        w, h = _get_image_dimensions(source_path)
        return PreparedPhoto(path=source_path, width=w, height=h)

    # Unknown format — try Pillow as fallback
    return _convert_via_pillow(source_path, work_dir)


def _convert_heif(source_path: Path, work_dir: Path) -> PreparedPhoto:
    """Convert HEIC/HEIF/AVIF via pillow-heif.

    If an Apple HDR gain map is present, applies it to produce a 16-bit
    PNG with full HDR data (for PQ encoding via FFmpeg zscale). Otherwise
    saves as high-quality JPEG.
    """
    try:
        import pillow_heif  # type: ignore[import-untyped]

        pillow_heif.register_heif_opener()
    except ImportError:
        logger.warning(
            "pillow-heif not installed — HEIC support unavailable. pip install pillow-heif"
        )
        raise

    from PIL import Image

    heif_file = pillow_heif.open_heif(str(source_path))
    img = Image.open(source_path)
    w, h = img.size

    # Check for Apple HDR gain map (present on iPhone 12+ photos)
    primary = heif_file[0] if len(heif_file) > 0 else heif_file
    aux_data = primary.info.get("aux", {})
    has_gain_map = any("hdrgainmap" in k for k in aux_data)

    # TODO: apply gain map for true HDR output once we can parse the
    # ISO 21496-1 metadata (min/max boost, offsets). Without those params,
    # the gain math produces overbright/wrong-color results. For now, the
    # SDR base with Display P3 ICC profile looks great on all displays.

    icc_profile = img.info.get("icc_profile")
    out_path = work_dir / f"{source_path.stem}_converted.jpg"
    save_kwargs: dict = {"quality": 95}
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    img.save(out_path, "JPEG", **save_kwargs)

    if has_gain_map:
        logger.info(
            f"Converted {source_path.name} ({w}x{h}) → JPEG with Display P3 (gain map present, SDR base used)"
        )
    else:
        logger.info(f"Converted {source_path.name} ({w}x{h}) → JPEG")

    return PreparedPhoto(path=out_path, width=w, height=h, has_gain_map=has_gain_map)


def _apply_hdr_gain_map(
    sdr_img: PILImage.Image,
    heif_file: object,
    gain_map_index: int,
    w: int,
    h: int,
    work_dir: Path,
    source_path: Path,
) -> PreparedPhoto:
    """Apply Apple HDR gain map to SDR base → 16-bit linear HDR PNG.

    Apple stores iPhone photos as 8-bit SDR (gamma-encoded) + logarithmic
    gain map. The gain must be applied in LINEAR light, not gamma space.

    Steps:
    1. Inverse sRGB gamma → linear SDR
    2. Apply gain: HDR_linear = SDR_linear * 2^(gain * headroom)
    3. Save as 16-bit PNG (linear values)
    4. FFmpeg zscale transferin=linear → PQ is then correct

    Headroom ≈ 2.3 (log2(1000/203) for ~1000 nit peak).
    """
    import numpy as np
    from PIL import Image

    gain_pil = heif_file.get_aux_image(gain_map_index).to_pillow()  # type: ignore[attr-defined]
    gain_resized = gain_pil.resize((w, h), Image.Resampling.LANCZOS)

    sdr_arr = np.array(sdr_img, dtype=np.float32) / 255.0
    gain_arr = np.array(gain_resized, dtype=np.float32) / 255.0

    # Step 1: inverse sRGB gamma → linear light
    # WHY: SDR pixels are gamma-encoded. Gain must be applied in linear space,
    # otherwise highlights get crushed and the PQ transfer looks washed out.
    sdr_linear = np.where(
        sdr_arr <= 0.04045,
        sdr_arr / 12.92,
        np.power((sdr_arr + 0.055) / 1.055, 2.4),
    )

    # Step 2: apply gain in linear space
    # WHY: headroom=2.3 maps SDR white (203 nits) to ~1000 nit peak
    headroom = 2.3
    hdr_gain = np.power(2.0, gain_arr * headroom)
    hdr_linear = sdr_linear * hdr_gain[:, :, np.newaxis]

    # Normalize: SDR white (1.0 linear) maps to 203/10000 in PQ absolute scale
    # Scale so that 1.0 = 10000 nits (PQ reference)
    hdr_pq_scale = hdr_linear * (203.0 / 10000.0)
    hdr_arr = np.clip(hdr_pq_scale, 0, 1)

    # Save as 16-bit PNG (cv2 handles 16-bit natively)
    hdr_16 = (hdr_arr * 65535).astype(np.uint16)

    try:
        import cv2

        hdr_bgr = cv2.cvtColor(hdr_16, cv2.COLOR_RGB2BGR)
        out_path = work_dir / f"{source_path.stem}_hdr.png"
        cv2.imwrite(str(out_path), hdr_bgr)
    except ImportError:
        # Fallback: save SDR JPEG if cv2 not available
        logger.warning("cv2 not available — falling back to SDR JPEG (gain map not applied)")
        out_path = work_dir / f"{source_path.stem}_converted.jpg"
        sdr_img.save(out_path, "JPEG", quality=95)
        return PreparedPhoto(path=out_path, width=w, height=h, has_gain_map=True)

    logger.info(
        f"Applied HDR gain map to {source_path.name} ({w}x{h}), "
        f"gain range {hdr_gain.min():.1f}×–{hdr_gain.max():.1f}×"
    )

    return PreparedPhoto(path=out_path, width=w, height=h, has_gain_map=True)


def _convert_via_pillow(source_path: Path, work_dir: Path) -> PreparedPhoto:
    """Fallback: convert any Pillow-supported format to JPEG."""
    from PIL import Image

    img: PILImage.Image = Image.open(source_path)  # type: ignore[assignment]
    if img.mode == "RGBA":
        img = img.convert("RGB")
    w, h = img.size

    out_path = work_dir / f"{source_path.stem}_converted.jpg"
    icc_profile = img.info.get("icc_profile")
    save_kwargs = {"quality": 95}
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    img.save(out_path, "JPEG", **save_kwargs)

    return PreparedPhoto(path=out_path, width=w, height=h)


def _get_image_dimensions(path: Path) -> tuple[int, int]:
    """Get image dimensions via Pillow (fast — only reads header)."""
    from PIL import Image

    with Image.open(path) as img:
        return img.size


def detect_photo_hdr_type(photo_path: Path) -> str | None:
    """Detect HDR type of a photo file via ffprobe.

    Same logic as hdr_utilities._detect_hdr_type() but accepts image
    file extensions (jpg, heic, heif, png, webp) without video-only
    path validation.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer",
                "-of",
                "json",
                str(photo_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                color_trc = streams[0].get("color_transfer", "")
                if color_trc == "arib-std-b67":
                    return "hlg"
                if color_trc in ("smpte2084", "bt2020-10", "bt2020-12"):
                    return "pq"
    except Exception as e:
        logger.debug(f"HDR detection failed for {photo_path}: {e}")
    return None


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
        gain_map_hdr: bool = False,
    ) -> list[str]:
        """Build FFmpeg command to convert a photo to an animated .mp4 clip.

        Args:
            hdr_type: "hlg", "pq", or None for SDR. When set, outputs HEVC
                      with 10-bit color and HDR metadata.
            gain_map_hdr: True when the source is a 16-bit gain-mapped PNG
                          (linear light) that needs zscale PQ transfer.
        """
        if mode == AnimationMode.AUTO:
            mode = self.resolve_auto_mode(width, height, face_bbox)

        duration = self._config.duration
        fps = 30
        seed = self._seed_from_id(asset_id)

        # Gain-mapped HDR sources are always PQ output
        effective_hdr = hdr_type or ("pq" if gain_map_hdr else None)
        encoder_args = self._encoder_args(effective_hdr)

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

        # Gain-mapped source is linear light — add zscale PQ conversion
        if gain_map_hdr:
            vf += (
                ",zscale=transfer=smpte2084:transferin=linear"
                ":primaries=bt2020:primariesin=bt709"
                ":matrix=bt2020nc:matrixin=bt709"
                ",format=yuv420p10le"
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
