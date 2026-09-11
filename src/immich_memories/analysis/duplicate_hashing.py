"""Perceptual hashing utilities for duplicate detection.

Provides frame-level, video-level, image-level, and thumbnail hashing
using average hash (aHash) algorithm, plus Hamming distance calculation.
"""

from __future__ import annotations

import cv2
import numpy as np


def _compute_frame_hash(frame: np.ndarray, hash_size: int = 8) -> str:
    """Compute average hash for a single frame.

    Args:
        frame: BGR image as numpy array.
        hash_size: Size of the hash.

    Returns:
        Hexadecimal hash string.
    """
    # Convert to grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Resize to hash_size x hash_size
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)

    # Compute mean
    mean = resized.mean()

    # Create binary hash (convert numpy bools to Python ints)
    bits = (resized > mean).flatten()

    # Convert to hex string - ensure we use Python int to avoid numpy issues
    hash_int = 0
    for i, bit in enumerate(bits):
        if bit:
            hash_int |= 1 << i

    # Use format() instead of hex() to avoid '0x' prefix issues
    return format(hash_int, "x").zfill(hash_size * hash_size // 4)


def hamming_distance(hash1: str, hash2: str) -> int:
    """Calculate Hamming distance between two hashes.

    Args:
        hash1: First hash (hex string).
        hash2: Second hash (hex string).

    Returns:
        Hamming distance (number of different bits).
    """
    if len(hash1) != len(hash2):
        return 64  # Maximum distance

    # Convert hex to integers
    try:
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
    except ValueError:
        return 64

    # XOR and count bits
    return (int1 ^ int2).bit_count()


def compute_thumbnail_hash(thumbnail_bytes: bytes, hash_size: int = 8) -> str:
    """Compute perceptual hash from thumbnail bytes.

    This is much faster than video hashing since thumbnails are already
    downloaded and cached.

    Args:
        thumbnail_bytes: JPEG image bytes from thumbnail cache.
        hash_size: Size of the hash.

    Returns:
        Hexadecimal hash string.
    """
    # Decode JPEG bytes to numpy array
    img_array = np.frombuffer(thumbnail_bytes, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if img is None:
        return ""

    return _compute_frame_hash(img, hash_size)
