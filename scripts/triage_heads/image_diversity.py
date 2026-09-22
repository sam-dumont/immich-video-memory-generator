"""Label-blind pixel descriptors and diversity diagnostics for triage cohorts.

The certification sampler may inspect pixels to avoid selecting four hundred
versions of the same scene, but it must not inspect semantic labels or model
outputs.  This module deliberately stays on the raw-pixel side of that line.
"""

from __future__ import annotations

import hashlib
import io
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

DESCRIPTOR_VERSION = "triage-raw-pixel-v1"
DESCRIPTOR_DIM = 112
DEFAULT_VISUAL_CLUSTERS = 64


@dataclass(frozen=True)
class PixelDescriptor:
    """A compact, task-model-free description of one preview."""

    preview_sha256: str
    average_hash: str
    difference_hash: str
    aspect_ratio: float
    vector: np.ndarray


@dataclass(frozen=True)
class VisualClustering:
    """Deterministic cluster assignments plus label-free quality measurements."""

    labels: np.ndarray
    distances: np.ndarray
    normalized_entropy: float
    coverage: float
    largest_cluster_share: float
    medoid_indices: tuple[int, ...]


def _hex_hash(bits: np.ndarray) -> str:
    flattened = np.asarray(bits, dtype=bool).reshape(-1)
    value = 0
    for index, bit in enumerate(flattened):
        if bit:
            value |= 1 << index
    return format(value, "x").zfill((len(flattened) + 3) // 4)


def _normalized_histogram(
    values: np.ndarray, bins: int, value_range: tuple[float, float]
) -> np.ndarray:
    counts, _edges = np.histogram(values, bins=bins, range=value_range)
    total = max(1, int(counts.sum()))
    return counts.astype(np.float32) / total


def _block_means(array: np.ndarray, rows: int, columns: int) -> np.ndarray:
    height, width = array.shape[:2]
    row_edges = np.linspace(0, height, rows + 1, dtype=int)
    column_edges = np.linspace(0, width, columns + 1, dtype=int)
    blocks: list[np.ndarray] = []
    for row in range(rows):
        for column in range(columns):
            block = array[
                row_edges[row] : row_edges[row + 1],
                column_edges[column] : column_edges[column + 1],
            ]
            blocks.append(np.asarray(block.mean(axis=(0, 1)), dtype=np.float32).reshape(-1))
    return np.concatenate(blocks)


def describe_preview_bytes(payload: bytes) -> PixelDescriptor:
    """Compute a bounded descriptor from preview bytes without a semantic model."""
    preview_sha256 = hashlib.sha256(payload).hexdigest()
    with Image.open(io.BytesIO(payload)) as source:
        image = source.convert("RGB")
        width, height = image.size
        if width < 1 or height < 1:
            raise ValueError("preview has invalid dimensions")
        rgb_image = image.resize((96, 96), Image.Resampling.BILINEAR)
        gray_image = image.convert("L")
        hsv_image = image.convert("HSV").resize((96, 96), Image.Resampling.BILINEAR)

    rgb = np.asarray(rgb_image, dtype=np.float32) / 255.0
    hsv = np.asarray(hsv_image, dtype=np.float32) / 255.0
    luminance = (0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]).astype(
        np.float32
    )

    gradient_x = np.abs(np.diff(luminance, axis=1, append=luminance[:, -1:]))
    gradient_y = np.abs(np.diff(luminance, axis=0, append=luminance[-1:, :]))
    edges = np.hypot(gradient_x, gradient_y).astype(np.float32)

    average_pixels = np.asarray(
        gray_image.resize((8, 8), Image.Resampling.LANCZOS), dtype=np.float32
    )
    average_hash = _hex_hash(average_pixels > average_pixels.mean())
    difference_pixels = np.asarray(
        gray_image.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.float32
    )
    difference_hash = _hex_hash(difference_pixels[:, 1:] > difference_pixels[:, :-1])

    aspect_ratio = width / height
    log_aspect = float(np.clip(math.log(aspect_ratio), -2.0, 2.0) / 2.0)
    orientation = np.asarray(
        [aspect_ratio < 0.9, 0.9 <= aspect_ratio <= 1.1, aspect_ratio > 1.1],
        dtype=np.float32,
    )
    vector = np.concatenate(
        (
            _normalized_histogram(hsv[:, :, 0], 12, (0.0, 1.0)),
            _normalized_histogram(hsv[:, :, 1], 8, (0.0, 1.0)),
            _normalized_histogram(hsv[:, :, 2], 8, (0.0, 1.0)),
            _normalized_histogram(luminance, 16, (0.0, 1.0)),
            _block_means(rgb, 4, 4),
            _block_means(edges[:, :, None], 4, 4),
            np.asarray([log_aspect], dtype=np.float32),
            orientation,
        )
    ).astype(np.float32, copy=False)
    if vector.shape != (DESCRIPTOR_DIM,) or not np.isfinite(vector).all():
        raise AssertionError("raw-pixel descriptor contract changed")
    return PixelDescriptor(
        preview_sha256=preview_sha256,
        average_hash=average_hash,
        difference_hash=difference_hash,
        aspect_ratio=aspect_ratio,
        vector=vector,
    )


def hash_hamming(left: str, right: str) -> int:
    """Return a conservative maximum distance for malformed hash inputs."""
    if len(left) != len(right) or not left:
        return 64
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 64


def descriptor_cosine(left: PixelDescriptor, right: PixelDescriptor) -> float:
    left_vector = np.asarray(left.vector, dtype=np.float32)
    right_vector = np.asarray(right.vector, dtype=np.float32)
    denominator = float(np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
    return float(np.dot(left_vector, right_vector) / denominator) if denominator else 0.0


def confirmed_near_duplicate(left: PixelDescriptor, right: PixelDescriptor) -> bool:
    """Confirm only very close raw-pixel matches; false negatives are safer here."""
    if left.preview_sha256 == right.preview_sha256:
        return True
    aspect_delta = abs(math.log(left.aspect_ratio / right.aspect_ratio))
    return bool(
        aspect_delta <= 0.08
        and hash_hamming(left.average_hash, right.average_hash) <= 4
        and hash_hamming(left.difference_hash, right.difference_hash) <= 4
        and descriptor_cosine(left, right) >= 0.985
    )


def cluster_descriptors(
    descriptors: Sequence[PixelDescriptor],
    *,
    cluster_count: int = DEFAULT_VISUAL_CLUSTERS,
    seed: int = 42,
) -> VisualClustering:
    """Cluster raw-pixel vectors deterministically and report diversity gates."""
    if not descriptors:
        raise ValueError("cannot cluster an empty descriptor set")
    if cluster_count < 2 or cluster_count > len(descriptors):
        raise ValueError("cluster_count must be between two and the descriptor count")

    from sklearn.cluster import MiniBatchKMeans
    from sklearn.preprocessing import StandardScaler

    vectors = np.stack([descriptor.vector for descriptor in descriptors]).astype(np.float32)
    scaled = StandardScaler().fit_transform(vectors).astype(np.float32, copy=False)
    model = MiniBatchKMeans(
        n_clusters=cluster_count,
        batch_size=min(1024, max(cluster_count * 4, 64)),
        n_init=10,
        max_iter=200,
        random_state=seed,
        reassignment_ratio=0.0,
    )
    labels = model.fit_predict(scaled).astype(np.int32, copy=False)
    all_distances = model.transform(scaled).astype(np.float32, copy=False)
    distances = all_distances[np.arange(len(labels)), labels]
    counts = Counter(int(label) for label in labels)
    probabilities = np.asarray(list(counts.values()), dtype=np.float64) / len(labels)
    entropy = float(-(probabilities * np.log(probabilities)).sum() / math.log(cluster_count))
    coverage = len(counts) / cluster_count
    medoids: list[int] = []
    for label in sorted(counts):
        members = np.flatnonzero(labels == label)
        member = int(members[np.argmin(distances[members])])
        medoids.append(member)
    return VisualClustering(
        labels=labels,
        distances=distances,
        normalized_entropy=entropy,
        coverage=coverage,
        largest_cluster_share=max(counts.values()) / len(labels),
        medoid_indices=tuple(medoids),
    )


def selected_cluster_metrics(
    labels: Sequence[int], *, universe_cluster_count: int
) -> dict[str, Any]:
    """Summarize a selected cohort against a frozen visual-cluster universe."""
    if universe_cluster_count < 2:
        raise ValueError("universe_cluster_count must be at least two")
    values = [int(label) for label in labels]
    if not values:
        raise ValueError("selected labels cannot be empty")
    if min(values) < 0 or max(values) >= universe_cluster_count:
        raise ValueError("selected cluster label is outside the frozen universe")
    counts = Counter(values)
    probabilities = np.asarray(list(counts.values()), dtype=np.float64) / len(values)
    return {
        "selected_count": len(values),
        "represented_clusters": len(counts),
        "cluster_coverage": len(counts) / universe_cluster_count,
        "normalized_cluster_entropy": float(
            -(probabilities * np.log(probabilities)).sum() / math.log(universe_cluster_count)
        ),
        "largest_cluster_share": max(counts.values()) / len(values),
        "cluster_counts": {str(key): counts[key] for key in sorted(counts)},
    }


def estimated_descriptor_memory_bytes(row_count: int) -> int:
    """Upper-bound the dense vectors plus clustering distance matrix at 64 clusters."""
    if row_count < 0:
        raise ValueError("row_count cannot be negative")
    vectors = row_count * DESCRIPTOR_DIM * np.dtype(np.float32).itemsize
    distances = row_count * DEFAULT_VISUAL_CLUSTERS * np.dtype(np.float32).itemsize
    return int(3 * vectors + distances)
