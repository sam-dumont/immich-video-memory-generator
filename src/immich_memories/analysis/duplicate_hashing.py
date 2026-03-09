"""Perceptual hashing utilities for duplicate detection.

Provides frame-level, video-level, image-level, and thumbnail hashing
using average hash (aHash) algorithm, plus Hamming distance calculation.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def compute_video_hash(
    video_path: str | Path,
    num_frames: int = 8,
    hash_size: int = 8,
) -> str:
    """Compute a perceptual hash for a video.

    Uses average hash (aHash) on multiple frames sampled throughout the video.

    Args:
        video_path: Path to the video file.
        num_frames: Number of frames to sample.
        hash_size: Size of the hash (hash_size x hash_size bits).

    Returns:
        Hexadecimal hash string.
    """
    video_path = Path(video_path)
    cap = cv2.VideoCapture(str(video_path))
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if frame_count <= 0:
            return ""

        # Sample frames evenly throughout the video
        frame_indices = np.linspace(0, frame_count - 1, num_frames, dtype=int)

        frame_hashes = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue

            # Compute average hash for this frame
            frame_hash = _compute_frame_hash(frame, hash_size)
            frame_hashes.append(frame_hash)
    finally:
        cap.release()

    if not frame_hashes:
        return ""

    # Combine frame hashes
    # Convert each hash to binary, then combine
    combined_bits = []
    for h in frame_hashes:
        # Convert hex to binary
        bin_str = bin(int(h, 16))[2:].zfill(hash_size * hash_size)
        combined_bits.extend([int(b) for b in bin_str])

    # Create final hash by taking majority vote across frames
    final_bits = []
    chunk_size = len(combined_bits) // len(frame_hashes)
    for i in range(chunk_size):
        votes = [combined_bits[j * chunk_size + i] for j in range(len(frame_hashes))]
        final_bits.append(1 if sum(votes) > len(votes) / 2 else 0)

    # Convert to hex
    final_hash = hex(int("".join(map(str, final_bits)), 2))[2:].zfill(hash_size * hash_size // 4)
    return final_hash


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


def compute_image_hash(image_path: str | Path, hash_size: int = 8) -> str:
    """Compute perceptual hash for an image.

    Args:
        image_path: Path to the image file.
        hash_size: Size of the hash.

    Returns:
        Hexadecimal hash string.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return ""
    return _compute_frame_hash(img, hash_size)


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
    xor = int1 ^ int2
    return bin(xor).count("1")


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
