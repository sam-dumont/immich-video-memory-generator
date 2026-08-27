"""Photo source preparation — decodes stills into something FFmpeg can read.

Handles HEIC/HEIF decode via pillow-heif, downscaling to the render cap, and
HDR detection: Apple gain maps (headroom from EXIF MakerNote tag 0x0021),
Android Ultra HDR, and tagged HLG/PQ transfer characteristics.

The animation itself lives in renderer.py — frames are rendered in numpy and
piped to FFmpeg by photo_pipeline.py.
"""

from __future__ import annotations

import contextlib
import json
import logging
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image as PILImage

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


_DEFAULT_HEADROOM = 2.3
_APPLE_MAKERNOTE_HEADER = b"Apple iOS"
_HDR_HEADROOM_TAG = 0x0021
_HDR_GAIN_TAG = 0x0030
_SRATIONAL_TYPE = 10


def _extract_apple_headroom(makernote: bytes | None, source_path: Path) -> float:
    """The headroom a photo's gain map may reach, as a LINEAR RATIO.

    Apple derives it from two MakerNote tags together -- 0x0021 HDRHeadroom and
    0x0030 HDRGain -- through a piecewise function, not from either alone. This
    code previously read 0x0021 by itself and used it as stops, which on a real
    photograph gave 2.01x where the true answer was 5.955x.

    Validated against 11 photographs: the value below reproduces the file's own
    `XMP:HDRGainMapHeadroom` to four decimal places on every one, across
    headrooms from 3.50 to 6.91.

    Constants from Apple's "Applying Apple HDR effect to your photos".
    """
    if makernote and makernote.startswith(_APPLE_MAKERNOTE_HEADER):
        result = _parse_makernote_headroom(makernote)
        if result is not None:
            return result

    return _exiftool_headroom(source_path)


def _eotf_srgb(v: np.ndarray) -> np.ndarray:
    """sRGB electro-optical transfer -- Display P3 shares it."""
    import numpy

    return numpy.where(v <= 0.04045, v / 12.92, numpy.power((v + 0.055) / 1.055, 2.4))


def _headroom_from_stops(maker33: float, maker48: float) -> float:
    """Apple's piecewise map from the two MakerNote values to a linear ratio."""
    if maker33 < 1.0:
        stops = -20.0 * maker48 + 1.8 if maker48 <= 0.01 else -0.101 * maker48 + 1.601
    else:
        stops = -70.0 * maker48 + 3.0 if maker48 <= 0.01 else -0.303 * maker48 + 2.303
    return float(2.0 ** max(stops, 0.0))


def _parse_makernote_headroom(mn: bytes) -> float | None:
    """Read tags 0x0021 and 0x0030 from the Apple MakerNote TIFF IFD.

    Apple MakerNote layout:
      - 14-byte header: 'Apple iOS\\x00\\x00\\x01MM'
      - Standard big-endian TIFF IFD (entry count + 12-byte entries)
      - Value offsets are relative to byte 0 of the MakerNote blob
    """
    if len(mn) < 16:
        return None

    try:
        entry_count = struct.unpack(">H", mn[14:16])[0]
    except struct.error:
        return None

    maker33 = _read_srational(mn, entry_count, _HDR_HEADROOM_TAG)
    maker48 = _read_srational(mn, entry_count, _HDR_GAIN_TAG)
    if maker33 is None or maker48 is None:
        return None
    return _headroom_from_stops(maker33, maker48)


def _read_srational(mn: bytes, entry_count: int, tag: int) -> float | None:
    """One SRATIONAL value from the MakerNote IFD, or None if absent."""
    pos = _find_ifd_tag(mn, entry_count, tag, _SRATIONAL_TYPE)
    if pos is None:
        return None
    offset = struct.unpack(">I", mn[pos + 8 : pos + 12])[0]
    if offset + 8 > len(mn):
        return None
    num, den = struct.unpack(">ii", mn[offset : offset + 8])
    if den == 0:
        return None
    return num / den


def _find_ifd_tag(mn: bytes, entry_count: int, tag_id: int, expected_type: int) -> int | None:
    """Find an IFD entry by tag ID, returning its byte position or None."""
    ifd_start = 16
    for i in range(entry_count):
        pos = ifd_start + i * 12
        if pos + 12 > len(mn):
            return None
        tag, dtype, count = struct.unpack(">HHI", mn[pos : pos + 8])
        if tag == tag_id and dtype == expected_type and count == 1:
            return pos
    return None


def _exiftool_headroom(source_path: Path) -> float:
    """Fallback: extract HDRHeadroom via exiftool subprocess."""
    try:
        result = subprocess.run(
            ["exiftool", "-Apple:HDRHeadroom", "-n", str(source_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and "HDR Headroom" in result.stdout:
            value_str = result.stdout.split(":")[-1].strip()
            headroom = float(value_str)
            logger.info(f"exiftool headroom for {source_path.name}: {headroom:.2f}")
            return headroom
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        pass

    return _DEFAULT_HEADROOM


@dataclass
class PreparedPhoto:
    """Result of preparing a photo for FFmpeg animation."""

    path: Path
    width: int
    height: int
    has_gain_map: bool = False
    peak_nits: int = 203  # SDR default; gain-mapped HDR sets actual peak
    # WHY: the video path reads the source's own primaries and passes them to
    # zscale; photos hardcoded bt709. pillow-heif hands back RAW Display P3 for
    # an iPhone HEIC, so that hardcoding described P3 values as the narrower
    # gamut and desaturated every saturated colour. "smpte432" is P3-D65.
    primaries: str = "bt709"


def prepare_photo_source(
    source_path: Path, work_dir: Path, *, max_size: tuple[int, int] | None = None
) -> PreparedPhoto:
    """Convert any image format to an FFmpeg-compatible source.

    ``max_size`` caps the longest edge before any array work. The renderer holds
    three float32 copies of the decoded image, so a 24 MP HEIC peaks near 0.9 GB
    and a 48 MP one exceeds a 4 GB container. Ken Burns never samples more than
    about twice the output resolution, so anything beyond that is memory spent
    on detail the encoder discards. Photos already inside the cap are returned
    untouched rather than re-encoded.

    Extracts HDR gain maps when present:
    - Apple HEIC: gain map via pillow-heif auxiliary image
    - UltraHDR JPEG (Android/Pixel/Samsung): gain map via MPF container
    Both produce 16-bit linear HDR PNG for the streaming renderer.

    Returns PreparedPhoto with the path to the FFmpeg-compatible file,
    plus dimensions and has_gain_map flag.
    """
    ext = source_path.suffix.lower()

    if ext in _HEIF_EXTENSIONS:
        try:
            return _convert_heif(source_path, work_dir, max_size=max_size)
        except ValueError:
            # WHY: extensions lie. A real library asset carries a .heic name over
            # JPEG bytes, and pillow-heif rejects it with "No 'ftyp' box". Losing
            # a photograph over its name is worse than decoding it the long way.
            logger.warning(
                "%s is named HEIF but is not; decoding by content instead", source_path.name
            )
            return _convert_via_pillow(source_path, work_dir, max_size=max_size)

    # Check for UltraHDR JPEG gain map (Android/Pixel/Samsung)
    if ext in (".jpg", ".jpeg"):
        result = _try_ultrahdr_extraction(source_path, work_dir, max_size=max_size)
        if result is not None:
            return result

    if ext in _FFMPEG_NATIVE_EXTENSIONS:
        return _prepare_native(source_path, work_dir, max_size)

    # Unknown format — try Pillow as fallback
    return _convert_via_pillow(source_path, work_dir, max_size=max_size)


def _prepare_native(
    source_path: Path, work_dir: Path, max_size: tuple[int, int] | None
) -> PreparedPhoto:
    """FFmpeg reads these directly; only re-encode when the cap actually bites."""
    w, h = _get_image_dimensions(source_path)
    if max_size is None or (w <= max_size[0] and h <= max_size[1]):
        return PreparedPhoto(path=source_path, width=w, height=h)

    from PIL import Image

    with Image.open(source_path) as opened:
        opened.draft("RGB", max_size)
        capped = opened.convert("RGB")
    capped.thumbnail(max_size, Image.Resampling.LANCZOS)
    out_path = work_dir / f"{source_path.stem}_capped.jpg"
    capped.save(out_path, "JPEG", quality=95)
    return PreparedPhoto(path=out_path, width=capped.width, height=capped.height)


def _downscale_in_place(img, max_size: tuple[int, int] | None):
    """Cap a decoded image, cheaply where the decoder can help.

    ``draft`` lets the JPEG decoder skip DCT levels, so the full-size bitmap is
    never materialised; it is a no-op for other formats and for images already
    within the cap.
    """
    if max_size is None:
        return img
    from PIL import Image

    with contextlib.suppress(AttributeError, ValueError):
        img.draft("RGB", max_size)
    if img.width > max_size[0] or img.height > max_size[1]:
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
    return img


def _try_ultrahdr_extraction(
    source_path: Path, work_dir: Path, *, max_size: tuple[int, int] | None = None
) -> PreparedPhoto | None:
    """Try to extract UltraHDR gain map from JPEG. Returns None if not UltraHDR."""
    try:
        import cv2
        import numpy as np

        from immich_memories.photos.ultrahdr import (
            apply_gain_map,
            extract_gain_map,
            is_ultra_hdr_jpeg,
            parse_hdrgm_metadata,
        )

        if not is_ultra_hdr_jpeg(source_path):
            return None

        primary_pil, gain_map_pil = extract_gain_map(source_path)
        metadata = parse_hdrgm_metadata(source_path)

        # WHY: capped before the float32 arrays below. The gain map is then
        # resampled onto the primary's new size -- apply_gain_map works
        # per-pixel and needs the two to agree, not to be full resolution.
        primary_pil = _downscale_in_place(primary_pil, max_size)
        w, h = primary_pil.size
        if gain_map_pil.size != (w, h):
            from PIL import Image as _PILImage

            gain_map_pil = gain_map_pil.resize((w, h), _PILImage.Resampling.LANCZOS)

        sdr = np.array(primary_pil, dtype=np.float32) / 255.0
        gm = np.array(gain_map_pil, dtype=np.float32) / 255.0

        # apply_gain_map returns gamma-encoded HDR per ISO 21496-1
        hdr_gamma = apply_gain_map(sdr, gm, metadata)

        # Convert to linear light (inverse sRGB gamma) so both HEIC and
        # UltraHDR paths output linear 16-bit PNGs for zscale tin=linear
        hdr_linear = np.where(
            hdr_gamma <= 0.04045,
            hdr_gamma / 12.92,
            np.power((hdr_gamma + 0.055) / 1.055, 2.4),
        )

        # Normalize for uint16: peak maps to 1.0
        peak_linear = 2.0**metadata.hdr_capacity_max
        hdr_norm = np.clip(hdr_linear / max(peak_linear, 1.001), 0, 1)
        hdr_16 = (hdr_norm * 65535).astype(np.uint16)

        out_path = work_dir / f"{source_path.stem}_hdr.png"
        cv2.imwrite(str(out_path), cv2.cvtColor(hdr_16, cv2.COLOR_RGB2BGR))

        peak_nits = int(peak_linear * 203)
        logger.info(
            f"Extracted UltraHDR gain map from {source_path.name} ({w}x{h}), "
            f"peak={peak_linear:.1f}x ({peak_nits} nits)"
        )
        return PreparedPhoto(
            path=out_path, width=w, height=h, has_gain_map=True, peak_nits=peak_nits
        )

    except (ValueError, OSError) as e:
        logger.debug(f"UltraHDR extraction failed for {source_path.name}: {e}")
        return None


def _convert_heif(
    source_path: Path, work_dir: Path, *, max_size: tuple[int, int] | None = None
) -> PreparedPhoto:
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
    # WHY: capped here, before the gain-map maths and the float32 copies below.
    # The gain map is resampled to the primary's size, and the maths is
    # per-pixel, so it is unaffected by the primary being smaller.
    img = _downscale_in_place(img, max_size)
    w, h = img.size

    # Check for Apple HDR gain map (present on iPhone 12+ photos)
    primary = heif_file[0] if len(heif_file) > 0 else heif_file
    aux_data = primary.info.get("aux", {})
    gain_map_key = next((k for k in aux_data if "hdrgainmap" in k), None)

    if gain_map_key:
        # aux_data values are lists of item IDs — pass the first item ID
        gain_map_item_id = aux_data[gain_map_key][0]

        # Extract per-photo headroom from Apple MakerNote
        makernote = img.getexif().get_ifd(0x8769).get(0x927C)
        headroom = _extract_apple_headroom(makernote, source_path)
        # WHY: no P3->sRGB here on purpose. The 16-bit PNG keeps the source
        # gamut and the renderer is told what it is, so BT.2020 -- which
        # contains P3 -- receives the wide colours instead of clipped ones.
        icc = img.info.get("icc_profile")
        primaries = "smpte432" if icc and _is_display_p3(icc) else "bt709"
        try:
            return _apply_hdr_gain_map(
                img, primary, gain_map_item_id, w, h, work_dir, source_path, headroom, primaries
            )
        except (ValueError, OSError) as e:
            logger.warning(f"Gain map extraction failed, falling back to SDR: {e}")

    out_path = work_dir / f"{source_path.stem}_converted.jpg"

    # WHY: cv2.imread ignores ICC profiles — it reads raw pixel values as sRGB.
    # If the HEIC is Display P3, P3 reds will appear oversaturated when treated
    # as sRGB. Convert P3→sRGB here so downstream cv2 reads correct colors.
    icc_profile = img.info.get("icc_profile")
    if icc_profile and _is_display_p3(icc_profile):
        img = _convert_p3_to_srgb(img, icc_profile)  # type: ignore[assignment]

    img.save(out_path, "JPEG", quality=95)
    logger.info(f"Converted {source_path.name} ({w}x{h}) → JPEG")

    return PreparedPhoto(path=out_path, width=w, height=h, has_gain_map=False)


def _apply_hdr_gain_map(
    sdr_img: PILImage.Image,
    heif_file: object,
    gain_map_index: int,
    w: int,
    h: int,
    work_dir: Path,
    source_path: Path,
    headroom: float = _DEFAULT_HEADROOM,
    primaries: str = "bt709",
) -> PreparedPhoto:
    """Apply Apple HDR gain map to SDR base → 16-bit linear HDR PNG.

    Apple stores iPhone photos as 8-bit SDR (gamma-encoded) + logarithmic
    gain map. The gain must be applied in LINEAR light, not gamma space.
    Headroom is extracted per-photo from the EXIF MakerNote (tag 0x0021).
    """
    import numpy as np
    from PIL import Image

    gain_pil = heif_file.get_aux_image(gain_map_index).to_pillow()  # type: ignore[attr-defined]
    gain_resized = gain_pil.resize((w, h), Image.Resampling.LANCZOS)

    sdr_arr = np.array(sdr_img, dtype=np.float32) / 255.0
    gain_arr = np.array(gain_resized, dtype=np.float32) / 255.0
    if gain_arr.ndim == 3:
        gain_arr = gain_arr[:, :, 0]

    # WHY: BOTH layers are sRGB-encoded and both must be linearised. Skipping it
    # on the gain map was worth a factor of two on this library -- raw values
    # average 0.51 where their linear counterparts average 0.23 -- so mid-tones
    # were lifted about twice as far as Apple lifts them, which reads as a flat,
    # over-bright picture rather than as a bright highlight.
    # Display P3 and sRGB share an EOTF, so one function serves both.
    sdr_linear = _eotf_srgb(sdr_arr)
    gain_linear = _eotf_srgb(gain_arr)

    # WHY: Apple interpolates linearly between 1.0 and the headroom -- it never
    # darkens. The exponential 2**(gain * headroom) used before is the ISO
    # 21496-1 / Ultra HDR shape, which belongs to a different file format.
    # Validated against CoreImage's own kCIImageExpandToHDR output on 11
    # photographs: median error 1-2% on most, 10% worst of those that converged.
    scale = 1.0 + (headroom - 1.0) * gain_linear
    hdr_linear = sdr_linear * scale[:, :, np.newaxis]

    # Normalize for uint16 storage: map HDR range into 0-1
    # WHY: headroom IS the peak multiple of SDR white; npl = peak * 203 nits
    peak_linear = headroom
    hdr_arr = np.clip(hdr_linear / peak_linear, 0, 1)

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

    peak_nits = int(peak_linear * 203)
    logger.info(
        f"Applied HDR gain map to {source_path.name} ({w}x{h}), "
        f"headroom={headroom:.2f}, peak={peak_nits} nits"
    )

    return PreparedPhoto(
        path=out_path,
        width=w,
        height=h,
        has_gain_map=True,
        peak_nits=peak_nits,
        primaries=primaries,
    )


def _convert_via_pillow(
    source_path: Path, work_dir: Path, *, max_size: tuple[int, int] | None = None
) -> PreparedPhoto:
    """Fallback: convert any Pillow-supported format to JPEG."""
    from PIL import Image

    img: PILImage.Image = Image.open(source_path)
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


def _is_display_p3(icc_profile: bytes) -> bool:
    """Check if an ICC profile is Display P3 (not sRGB)."""
    from io import BytesIO

    from PIL import ImageCms

    try:
        profile = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
        desc = ImageCms.getProfileDescription(profile).strip().lower()
        return "p3" in desc
    except (OSError, ValueError):
        return False


def _convert_p3_to_srgb(img: PILImage.Image, icc_profile: bytes) -> PILImage.Image:
    """Convert a Display P3 image to sRGB using ICC profile transform."""
    from io import BytesIO

    from PIL import ImageCms

    try:
        p3_profile = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
        srgb_profile = ImageCms.createProfile("sRGB")
        result = ImageCms.profileToProfile(img, p3_profile, srgb_profile, outputMode="RGB")
        return result  # type: ignore[return-value]
    except (OSError, ValueError) as e:
        logger.debug(f"P3→sRGB conversion failed, using original: {e}")
        return img


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
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug(f"HDR detection failed for {photo_path}: {e}")
    return None
