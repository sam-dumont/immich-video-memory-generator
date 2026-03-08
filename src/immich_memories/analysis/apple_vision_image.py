"""Image conversion utilities for Apple Vision framework.

Converts numpy arrays (OpenCV BGR) to CoreGraphics image objects
required by the Vision framework.
"""

from __future__ import annotations

import numpy as np


def create_ci_image_from_numpy(image_array: np.ndarray) -> object:
    """Create a CIImage from a numpy array (BGR format from OpenCV).

    Args:
        image_array: BGR image from OpenCV.

    Returns:
        CIImage object.
    """
    import CoreImage
    import Quartz

    # Convert BGR to RGB
    if len(image_array.shape) == 3 and image_array.shape[2] == 3:
        image_rgb = image_array[:, :, ::-1].copy()
    else:
        image_rgb = image_array

    height, width = image_array.shape[:2]
    channels = image_array.shape[2] if len(image_array.shape) == 3 else 1

    # Create CGImage
    bytes_per_row = width * channels
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()

    # Create bitmap context
    provider = Quartz.CGDataProviderCreateWithData(
        None,
        image_rgb.tobytes(),
        height * bytes_per_row,
        None,
    )

    cg_image = Quartz.CGImageCreate(
        width,
        height,
        8,  # bits per component
        8 * channels,  # bits per pixel
        bytes_per_row,
        color_space,
        Quartz.kCGImageAlphaNoneSkipLast if channels == 4 else Quartz.kCGBitmapByteOrderDefault,
        provider,
        None,
        False,
        Quartz.kCGRenderingIntentDefault,
    )

    # Create CIImage from CGImage
    ci_image = CoreImage.CIImage.imageWithCGImage_(cg_image)
    return ci_image


def create_cg_image_from_numpy(image_array: np.ndarray) -> object:
    """Create a CGImage from a numpy array.

    Args:
        image_array: BGR image from OpenCV.

    Returns:
        CGImage object.
    """
    import Quartz

    # Convert BGR to RGBA
    if len(image_array.shape) == 3 and image_array.shape[2] == 3:
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

    # Create data provider
    provider = Quartz.CGDataProviderCreateWithData(
        None,
        image_rgba.tobytes(),
        height * bytes_per_row,
        None,
    )

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
