"""Frame preview encoding for live UI feedback during assembly."""

from __future__ import annotations

import io
import time
from collections.abc import Callable

import numpy as np
from PIL import Image

PREVIEW_INTERVAL_SECONDS = 2.0


def _to_rgb8(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    """Convert a flat YUV420p10le buffer to RGB8.

    Steps: unpack Y/U/V planes -> upsample chroma (nearest neighbor) ->
    BT.2020 YUV->RGB matrix -> simple tonemap (10-bit -> 8-bit) -> clip to uint8.
    """
    y_size = width * height
    uv_w, uv_h = width // 2, height // 2
    uv_size = uv_w * uv_h

    y = frame[:y_size].reshape(height, width).astype(np.float32)
    u = frame[y_size : y_size + uv_size].reshape(uv_h, uv_w).astype(np.float32)
    v = frame[y_size + uv_size :].reshape(uv_h, uv_w).astype(np.float32)

    # Upsample chroma (nearest neighbor — fast, good enough for preview)
    u = u.repeat(2, axis=0).repeat(2, axis=1)[:height, :width]
    v = v.repeat(2, axis=0).repeat(2, axis=1)[:height, :width]

    # BT.2020 limited range (64-940 for Y, 64-960 for UV) -> normalized
    y_norm = (y - 64.0) / (940.0 - 64.0)
    u_norm = (u - 512.0) / (960.0 - 64.0)
    v_norm = (v - 512.0) / (960.0 - 64.0)

    # BT.2020 NCL YUV->RGB matrix
    r = y_norm + 1.4746 * v_norm
    g = y_norm - 0.1646 * u_norm - 0.5714 * v_norm
    b = y_norm + 1.8814 * u_norm

    # WHY: Simple clip is fine for preview — SDR values stay accurate,
    # HDR highlights clip to white. Full tonemap (Reinhard) would darken
    # the entire image making reference white look gray.
    rgb = np.stack([r, g, b], axis=-1)
    return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)


def _maybe_emit_preview(
    frame: np.ndarray,
    last_preview_time: float,
    callback: Callable[[bytes], None] | None,
    is_hdr: bool,
    height: int,
    width: int,
) -> float:
    """Emit a JPEG preview if enough time has elapsed since last emission.

    Returns the updated last_preview_time.
    """
    if callback is None:
        return last_preview_time

    now = time.monotonic()
    if now - last_preview_time < PREVIEW_INTERVAL_SECONDS:
        return last_preview_time

    rgb = _to_rgb8(frame, height, width) if is_hdr else frame
    jpeg = _encode_preview_jpeg(rgb)
    callback(jpeg)
    return now


def _encode_preview_jpeg(rgb: np.ndarray, quality: int = 75, max_height: int = 720) -> bytes:
    """Encode an RGB uint8 frame to JPEG bytes, downscaling if needed."""
    img = Image.fromarray(rgb)
    h, w = rgb.shape[:2]
    if h > max_height:
        scale = max_height / h
        img = img.resize((int(w * scale), max_height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
