"""Frame preview encoding for live UI feedback during assembly."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image


def _encode_preview_jpeg(rgb: np.ndarray, quality: int = 75, max_height: int = 720) -> bytes:
    """Encode an RGB uint8 frame to JPEG bytes, downscaling if needed."""
    img = Image.fromarray(rgb)
    h, w = rgb.shape[:2]
    if h > max_height:
        scale = max_height / h
        img = img.resize((int(w * scale), max_height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
