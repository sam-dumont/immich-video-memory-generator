"""The DINOv2 image transform: resize short side, centre crop, ImageNet normalise.

Any change here is a new PREPROCESS_VERSION, which re-keys every cached feature.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

PREPROCESS_VERSION = "dinov2-s14-224-crop-v1"
RESIZE_SHORT_SIDE = 256
CROP_SIZE = 224
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def preprocess_image_bytes(payload: bytes) -> np.ndarray:
    """Decode one preview into a float32 ``[3, 224, 224]`` CHW tensor."""
    with Image.open(io.BytesIO(payload)) as handle:
        image = handle.convert("RGB")
    width, height = image.size
    if width <= height:
        resized = (RESIZE_SHORT_SIDE, round(height * RESIZE_SHORT_SIDE / width))
    else:
        resized = (round(width * RESIZE_SHORT_SIDE / height), RESIZE_SHORT_SIDE)
    image = image.resize(resized, Image.Resampling.BICUBIC)
    left = (resized[0] - CROP_SIZE) // 2
    top = (resized[1] - CROP_SIZE) // 2
    image = image.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    normalized = (pixels - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1), dtype=np.float32)
