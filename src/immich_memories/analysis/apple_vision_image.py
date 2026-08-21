"""Image conversion utilities for Apple Vision framework.

Converts numpy arrays (OpenCV BGR) to CoreGraphics image objects
required by the Vision framework.
"""

from __future__ import annotations

import numpy as np


def create_cg_image_from_numpy(image_array: np.ndarray) -> object:
    """Create a CGImage from a numpy array.

    Args:
        image_array: BGR image from OpenCV.

    Returns:
        CGImage object.
    """
    import Quartz

    # Convert BGR to RGBA
    if len(image_array.shape) == image_array.shape[2] == 3:
        # Add alpha channel
        import cv2

        image_rgba = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGBA)
    elif len(image_array.shape) == 3 and image_array.shape[2] == 4:
        image_rgba = image_array
    else:
        # Grayscale - convert to RGBA
        import cv2

        image_rgb = cv2.cvtColor(image_array, cv2.COLOR_GRAY2RGB)
        image_rgba = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2RGBA)

    height, width = image_rgba.shape[:2]
    bytes_per_row = width * 4

    # Create color space
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()

    # WHY: CGDataProviderCreateWithData does not copy — PyObjC would retain the
    # bytes buffer forever (no release callback fires), leaking a full RGBA
    # frame per call. CFData is refcounted by the provider and freed with it.
    data = Quartz.CFDataCreate(None, image_rgba.tobytes(), height * bytes_per_row)
    provider = Quartz.CGDataProviderCreateWithCFData(data)

    # Create CGImage
    cg_image = Quartz.CGImageCreate(
        width,
        height,
        8,  # bits per component
        32,  # bits per pixel (RGBA)
        bytes_per_row,
        color_space,
        Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big,
        provider,
        None,
        False,
        Quartz.kCGRenderingIntentDefault,
    )

    return cg_image
