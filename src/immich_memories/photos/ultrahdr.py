"""Ultra HDR JPEG gain map extraction and HDR reconstruction.

Handles Android/Pixel Ultra HDR format (ISO 21496-1 / Google hdrgm):
MPF container with SDR base + gain map secondary image + XMP metadata.

Formula: HDR = (SDR + offset_sdr) * 2^(log_boost * weight) - offset_hdr
Where log_boost is derived from the gain map pixel values and metadata.
"""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class GainMapMetadata:
    """XMP hdrgm namespace metadata for gain map reconstruction."""

    gain_map_min: list[float] = field(default_factory=lambda: [0.0])
    gain_map_max: list[float] = field(default_factory=lambda: [1.0])
    gamma: list[float] = field(default_factory=lambda: [1.0])
    offset_sdr: list[float] = field(default_factory=lambda: [1 / 64])
    offset_hdr: list[float] = field(default_factory=lambda: [1 / 64])
    hdr_capacity_min: float = 0.0
    hdr_capacity_max: float = 1.0
    base_rendition_is_hdr: bool = False


def extract_gain_map(jpeg_path: str | Path) -> tuple[Image.Image, Image.Image]:
    """Extract primary SDR image and gain map from Ultra HDR JPEG."""
    data = Path(jpeg_path).read_bytes()
    images = _find_mpf_images(data)

    if len(images) < 2:
        msg = f"No secondary image (gain map) found in {jpeg_path}"
        raise ValueError(msg)

    offset0, size0 = images[0]
    if size0 == 0:
        size0 = images[1][0]
    primary = Image.open(io.BytesIO(data[offset0 : offset0 + size0]))

    offset1, size1 = images[1]
    if size1 == 0:
        size1 = len(data) - offset1
    gain_map = Image.open(io.BytesIO(data[offset1 : offset1 + size1]))

    return primary, gain_map


def parse_hdrgm_metadata(jpeg_path: str | Path) -> GainMapMetadata:
    """Parse hdrgm XMP metadata from Ultra HDR JPEG."""
    data = Path(jpeg_path).read_bytes()
    meta = GainMapMetadata()

    xmp_str = _parse_xmp_from_jpeg(data)
    if xmp_str is None:
        return meta

    patterns = {
        "gain_map_min": r'hdrgm:GainMapMin[=">]+([^"<]+)',
        "gain_map_max": r'hdrgm:GainMapMax[=">]+([^"<]+)',
        "gamma": r'hdrgm:Gamma[=">]+([^"<]+)',
        "offset_sdr": r'hdrgm:OffsetSDR[=">]+([^"<]+)',
        "offset_hdr": r'hdrgm:OffsetHDR[=">]+([^"<]+)',
        "hdr_capacity_min": r'hdrgm:HDRCapacityMin[=">]+([^"<]+)',
        "hdr_capacity_max": r'hdrgm:HDRCapacityMax[=">]+([^"<]+)',
        "base_rendition_is_hdr": r'hdrgm:BaseRenditionIsHDR[=">]+([^"<]+)',
    }

    for attr, pattern in patterns.items():
        match = re.search(pattern, xmp_str)
        if match:
            value = match.group(1).strip()
            if attr == "base_rendition_is_hdr":
                meta.base_rendition_is_hdr = value.lower() in ("true", "1")
            elif attr in ("hdr_capacity_min", "hdr_capacity_max"):
                setattr(meta, attr, float(value))
            else:
                setattr(meta, attr, _parse_float_or_list(value))

    return meta


def apply_gain_map(
    sdr: np.ndarray,
    gain_map: np.ndarray,
    metadata: GainMapMetadata,
    display_boost: float = 0.0,
) -> np.ndarray:
    """Apply gain map to SDR image to reconstruct HDR.

    Formula (ISO 21496-1):
    HDR = (SDR + offset_sdr) * 2^(log_boost * weight) - offset_hdr

    Where:
    - log_boost = gm_min + pow(gain_map, 1/gamma) * (gm_max - gm_min)
    - weight adapts to display capability (1.0 = full HDR)
    """
    h, w = sdr.shape[:2]

    # Upsample gain map if needed
    if gain_map.shape[0] != h or gain_map.shape[1] != w:
        gm_pil = Image.fromarray((gain_map * 255).astype(np.uint8))
        gm_pil = gm_pil.resize((w, h), Image.Resampling.BILINEAR)
        gain_map = np.array(gm_pil).astype(np.float32) / 255.0

    # Ensure 3-channel
    if len(gain_map.shape) == 2:
        gain_map = np.stack([gain_map] * 3, axis=-1)

    def _expand(values: list[float]) -> np.ndarray:
        if len(values) == 1:
            return np.full(3, values[0], dtype=np.float32)
        return np.array(values[:3], dtype=np.float32)

    gm_min = _expand(metadata.gain_map_min)
    gm_max = _expand(metadata.gain_map_max)
    gamma = _expand(metadata.gamma)
    off_sdr = _expand(metadata.offset_sdr)
    off_hdr = _expand(metadata.offset_hdr)

    # Weight factor (display adaptation)
    if display_boost <= 0:
        display_boost = 2.0**metadata.hdr_capacity_max
    log2_boost = np.log2(max(display_boost, 1.001))

    if metadata.hdr_capacity_max > metadata.hdr_capacity_min:
        weight = np.clip(
            (log2_boost - metadata.hdr_capacity_min)
            / (metadata.hdr_capacity_max - metadata.hdr_capacity_min),
            0.0,
            1.0,
        )
    else:
        weight = 1.0

    if metadata.base_rendition_is_hdr:
        weight = 1.0 - weight

    # Inverse gamma on gain map
    log_recovery = np.zeros_like(gain_map)
    for c in range(3):
        if gamma[c] != 1.0:
            log_recovery[:, :, c] = np.power(np.clip(gain_map[:, :, c], 0.0, 1.0), 1.0 / gamma[c])
        else:
            log_recovery[:, :, c] = gain_map[:, :, c]

    # Log boost interpolation
    log_boost = gm_min + log_recovery * (gm_max - gm_min)

    # Apply: HDR = (SDR + offset_sdr) * 2^(log_boost * weight) - offset_hdr
    hdr = (sdr + off_sdr) * np.power(2.0, log_boost * weight) - off_hdr

    return np.clip(hdr, 0.0, None)


def is_ultra_hdr_jpeg(jpeg_path: str | Path) -> bool:
    """Check if a JPEG file is an Ultra HDR image with gain map."""
    data = Path(jpeg_path).read_bytes()
    return b"hdrgm:Version" in data or b"ns.adobe.com/hdr-gain-map" in data


# ── Private helpers ───────────────────────────────────────────────


def _parse_float_or_list(value: str) -> list[float]:
    parts = [v.strip() for v in value.split(",")]
    return [float(p) for p in parts if p]


def _parse_xmp_from_jpeg(data: bytes) -> str | None:
    xmp_marker = b"http://ns.adobe.com/xap/1.0/\x00"
    pos = data.find(xmp_marker)
    if pos == -1:
        return None
    search_start = max(0, pos - 100)
    app1_pos = data.rfind(b"\xff\xe1", search_start, pos)
    if app1_pos == -1:
        return None
    length = struct.unpack(">H", data[app1_pos + 2 : app1_pos + 4])[0]
    xmp_start = pos + len(xmp_marker)
    xmp_end = app1_pos + 2 + length
    xmp_bytes = data[xmp_start:xmp_end]
    xml_start = xmp_bytes.find(b"<x:xmpmeta") if b"<x:xmpmeta" in xmp_bytes else 0
    return xmp_bytes[max(0, xml_start) :].decode("utf-8", errors="replace")


def _find_mpf_offset(data: bytes) -> int:
    """Find the MPF APP2 marker offset. Returns -1 if not found."""
    mpf_sig = b"\xff\xe2"
    pos = 0
    while pos < len(data) - 4:
        idx = data.find(mpf_sig, pos)
        if idx == -1:
            break
        if data[idx + 4 : idx + 8] == b"MPF\x00":
            return idx + 4
        pos = idx + 2
    return -1


def _parse_mp_entries(
    data: bytes, tiff_start: int, mpf_offset: int, endian: str, ifd_pos: int, num_entries: int
) -> list[tuple[int, int]]:
    """Parse MP Entry tag (0xB002) from MPF IFD to get image offsets."""
    images: list[tuple[int, int]] = []
    for i in range(num_entries):
        entry_pos = ifd_pos + 2 + i * 12
        tag = struct.unpack(endian + "H", data[entry_pos : entry_pos + 2])[0]
        if tag != 0xB002:
            continue
        count = struct.unpack(endian + "I", data[entry_pos + 4 : entry_pos + 8])[0]
        value_offset = struct.unpack(endian + "I", data[entry_pos + 8 : entry_pos + 12])[0]
        mp_entry_pos = tiff_start + value_offset
        for j in range(count // 16):
            ep = mp_entry_pos + j * 16
            img_size = struct.unpack(endian + "I", data[ep + 4 : ep + 8])[0]
            img_offset = struct.unpack(endian + "I", data[ep + 8 : ep + 12])[0]
            if j == 0:
                images.append((0, img_size))
            else:
                images.append(
                    (_resolve_mpf_offset(data, mpf_offset, tiff_start, img_offset), img_size)
                )
        break
    return images


def _resolve_mpf_offset(data: bytes, mpf_offset: int, tiff_start: int, raw_offset: int) -> int:
    """Resolve secondary image offset (different encoders use different bases)."""
    for candidate in (mpf_offset - 4 + raw_offset, tiff_start + raw_offset, raw_offset):
        if 0 <= candidate < len(data) and data[candidate : candidate + 2] == b"\xff\xd8":
            return candidate
    return raw_offset


def _find_mpf_images(data: bytes) -> list[tuple[int, int]]:
    """Find image offsets in MPF container."""
    mpf_offset = _find_mpf_offset(data)
    if mpf_offset == -1:
        return _find_jpeg_soi_markers(data)

    mpf_data_start = mpf_offset + 4
    endian_mark = data[mpf_data_start : mpf_data_start + 2]
    if endian_mark not in (b"MM", b"II"):
        return _find_jpeg_soi_markers(data)
    endian = ">" if endian_mark == b"MM" else "<"

    tiff_start = mpf_data_start
    first_ifd_offset = struct.unpack(endian + "I", data[tiff_start + 4 : tiff_start + 8])[0]
    ifd_pos = tiff_start + first_ifd_offset
    num_entries = struct.unpack(endian + "H", data[ifd_pos : ifd_pos + 2])[0]

    images = _parse_mp_entries(data, tiff_start, mpf_offset, endian, ifd_pos, num_entries)
    return images if len(images) >= 2 else _find_jpeg_soi_markers(data)


def _find_jpeg_soi_markers(data: bytes) -> list[tuple[int, int]]:
    """Fallback: find JPEG images by scanning for SOI markers."""
    soi = b"\xff\xd8"
    eoi = b"\xff\xd9"
    images: list[tuple[int, int]] = []
    pos = 0
    while pos < len(data):
        start = data.find(soi, pos)
        if start == -1:
            break
        end = data.find(eoi, start + 2)
        if end == -1:
            images.append((start, len(data) - start))
            break
        images.append((start, end + 2 - start))
        pos = end + 2
    return images
