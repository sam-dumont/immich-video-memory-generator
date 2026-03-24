"""Smoke tests for transforms module imports.

Ensures the renamed transform modules load correctly and their
public API surface is importable.
"""

from __future__ import annotations

import pytest

try:
    import cv2  # noqa: F401
except ImportError:
    pytest.skip("cv2 not available", allow_module_level=True)


def test_transforms_module_imports():
    from immich_memories.processing.transforms import (
        AspectRatioTransformer,
        ScaleMode,
        apply_aspect_ratio_transform,
    )

    assert ScaleMode.FIT.value == "fit"
    assert callable(apply_aspect_ratio_transform)
    assert issubclass(AspectRatioTransformer, object)


def test_transforms_ffmpeg_imports():
    from immich_memories.processing.transforms_ffmpeg import (
        CropRegion,
        get_video_dimensions,
        transform_fill,
        transform_fit,
    )

    region = CropRegion(x=10, y=20, width=100, height=80)
    assert region.x == 10
    assert region.y == 20
    assert region.width == 100
    assert region.height == 80
    assert region.center == (60, 60)
    assert "crop=100:80:10:20" in region.to_ffmpeg_filter()
    assert callable(get_video_dimensions)
    assert callable(transform_fill)
    assert callable(transform_fit)


def test_transforms_smart_crop_imports():
    from immich_memories.processing.transforms_smart_crop import (
        calculate_smart_crop,
        detect_faces_in_video,
    )

    assert callable(calculate_smart_crop)
    assert callable(detect_faces_in_video)
