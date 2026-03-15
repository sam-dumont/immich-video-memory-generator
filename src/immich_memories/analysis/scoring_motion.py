"""Motion and duration scoring for video segments."""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def compute_motion_metrics(
    prev_frame: np.ndarray,
    curr_frame: np.ndarray,
) -> tuple[float, float]:
    """Compute motion amount and stability.

    Args:
        prev_frame: Previous grayscale frame.
        curr_frame: Current grayscale frame.

    Returns:
        Tuple of (motion_score, stability_score).
    """
    # Compute optical flow
    flow = cv2.calcOpticalFlowFarneback(
        prev_frame,
        curr_frame,
        None,
        pyr_scale=0.5,
        levels=3,
        winsize=15,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )

    # Magnitude of flow
    magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
    mean_motion = np.mean(magnitude)
    motion_std = np.std(magnitude)

    # Motion score: some motion is good, too much is bad
    # Optimal range is roughly 2-10 pixels of movement
    if mean_motion < 1:
        motion_score = 0.3  # Too static
    elif mean_motion < 5:
        motion_score = 0.5 + (mean_motion - 1) * 0.125  # Good range
    elif mean_motion < 15:
        motion_score = 1.0 - (mean_motion - 5) * 0.05  # Getting shaky
    else:
        motion_score = 0.3  # Too much motion (shake or blur)

    # Stability score: lower variance = more stable
    # High std indicates camera shake or erratic motion
    stability_score = max(0, 1 - (motion_std / 20))

    return motion_score, float(stability_score)


def compute_duration_score(
    duration: float,
    source_duration: float | None,
    optimal_duration: float,
    max_optimal_duration: float,
    target_extraction_ratio: float,
    min_duration: float,
) -> float:
    """Compute duration preference score using a Gaussian curve.

    The score peaks at the optimal duration, which scales with source
    duration for longer videos. For a 15s source, 5s is optimal. For a
    70s source, ~10s is optimal (to avoid extracting too little).

    Args:
        duration: Clip duration in seconds.
        source_duration: Full source video duration (for ratio-based scaling).
        optimal_duration: Base sweet spot duration in seconds.
        max_optimal_duration: Max optimal duration for long sources.
        target_extraction_ratio: Target ratio of clip to source.
        min_duration: Minimum acceptable duration in seconds.

    Returns:
        Score between 0.0 and 1.0, with 1.0 at optimal duration.
    """
    # Clips below minimum duration get heavy penalty
    if duration < min_duration:
        # Linear penalty: 0.0 at 0s, 0.3 at min_duration
        return max(0.0, 0.3 * (duration / min_duration))

    # Dynamic optimal duration based on source length
    # For short sources (< 20s): optimal stays at base (5s)
    # For longer sources: optimal scales up to max_optimal
    if source_duration and source_duration > 20.0:
        dynamic_optimal = min(
            max_optimal_duration,
            max(optimal_duration, source_duration * target_extraction_ratio),
        )
        logger.debug(
            f"Duration scoring: source={source_duration:.1f}s, "
            f"clip={duration:.1f}s, optimal={dynamic_optimal:.1f}s "
            f"(target {target_extraction_ratio * 100:.0f}% of source)"
        )
    else:
        dynamic_optimal = optimal_duration
        if source_duration:
            logger.debug(
                f"Duration scoring: source={source_duration:.1f}s (short), "
                f"clip={duration:.1f}s, optimal={dynamic_optimal:.1f}s (base)"
            )

    # Gaussian curve centered at dynamic optimal duration
    # sigma scales with optimal to keep curve proportional
    sigma = max(3.0, dynamic_optimal * 0.6)
    diff = duration - dynamic_optimal
    score = np.exp(-(diff * diff) / (2 * sigma * sigma))

    # For very long clips (>15s), add extra penalty
    if duration > 15.0:
        long_penalty = (duration - 15.0) * 0.05
        score = max(0.2, score - long_penalty)

    return float(score)
