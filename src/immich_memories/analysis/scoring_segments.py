"""Segment generation and subdivision for video scoring.

Provides both fixed-duration sliding window segmentation and
scene-aware segmentation using natural scene boundaries.
"""

from __future__ import annotations

import logging
from pathlib import Path

from immich_memories.analysis.scenes import Scene, get_video_info

logger = logging.getLogger(__name__)


def generate_segments(
    video_path: Path,
    segment_duration: float,
    overlap: float,
) -> list[Scene]:
    """Generate segment boundaries using sliding window.

    Args:
        video_path: Path to the video file.
        segment_duration: Duration of each segment in seconds.
        overlap: Overlap fraction between segments (0-1).

    Returns:
        List of Scene objects representing segments.
    """
    info = get_video_info(video_path)
    duration = info.get("duration", 0)
    fps = info.get("fps", 30) or 30

    if duration <= 0:
        return []

    # Handle video shorter than segment_duration
    if duration <= segment_duration:
        return [
            Scene(
                start_time=0,
                end_time=duration,
                start_frame=0,
                end_frame=int(duration * fps),
            )
        ]

    step = segment_duration * (1 - overlap)
    segments = []
    current_start = 0.0

    while current_start + segment_duration <= duration:
        segments.append(
            Scene(
                start_time=current_start,
                end_time=current_start + segment_duration,
                start_frame=int(current_start * fps),
                end_frame=int((current_start + segment_duration) * fps),
            )
        )
        current_start += step

    # Handle final partial segment if substantial
    if current_start < duration and (duration - current_start) >= segment_duration * 0.5:
        segments.append(
            Scene(
                start_time=current_start,
                end_time=duration,
                start_frame=int(current_start * fps),
                end_frame=int(duration * fps),
            )
        )

    return segments


def generate_scene_aware_segments(
    video_path: Path,
    max_segment_duration: float,
    min_segment_duration: float,
    scene_threshold: float,
    min_scene_duration: float,
) -> list[Scene]:
    """Generate segments using scene detection with subdivision for long scenes.

    Args:
        video_path: Path to the video file.
        max_segment_duration: Maximum segment duration (subdivide longer scenes).
        min_segment_duration: Minimum segment duration (filter out shorter).
        scene_threshold: Threshold for scene detection.
        min_scene_duration: Minimum scene duration for detection.

    Returns:
        List of Scene objects representing segments.
    """
    from immich_memories.analysis.scenes import SceneDetector

    # Detect natural scene boundaries
    detector = SceneDetector(
        threshold=scene_threshold,
        min_scene_duration=min_scene_duration,
        adaptive_threshold=True,
    )
    scenes = detector.detect(
        video_path,
        extract_keyframes=False,  # Skip for performance
    )

    # Get video info for fps
    info = get_video_info(video_path)
    fps = info.get("fps", 30) or 30

    # Process scenes into segments
    segments = []

    for scene in scenes:
        if scene.duration < min_segment_duration:
            # Skip very short scenes (likely flashes/glitches)
            continue

        if scene.duration <= max_segment_duration:
            # Short/medium scene: use entire scene as one segment
            segments.append(scene)
        else:
            # Long scene: subdivide with sliding window WITHIN scene boundaries
            sub_segments = subdivide_scene(
                scene=scene,
                target_duration=max_segment_duration / 2,  # Target smaller segments
                overlap=0.5,
                fps=fps,
            )
            segments.extend(sub_segments)

    return segments


def subdivide_scene(
    scene: Scene,
    target_duration: float,
    overlap: float,
    fps: float,
) -> list[Scene]:
    """Subdivide a long scene into overlapping segments.

    Args:
        scene: The scene to subdivide.
        target_duration: Target duration for each sub-segment.
        overlap: Overlap fraction between segments (0-1).
        fps: Video frame rate.

    Returns:
        List of Scene objects representing sub-segments.
    """
    sub_segments = []
    step = target_duration * (1 - overlap)
    current_start = scene.start_time

    while current_start + target_duration <= scene.end_time:
        sub_segments.append(
            Scene(
                start_time=current_start,
                end_time=current_start + target_duration,
                start_frame=int(current_start * fps),
                end_frame=int((current_start + target_duration) * fps),
            )
        )
        current_start += step

    # Handle final partial segment if substantial
    remaining = scene.end_time - current_start
    if remaining >= target_duration * 0.5:
        sub_segments.append(
            Scene(
                start_time=current_start,
                end_time=scene.end_time,
                start_frame=int(current_start * fps),
                end_frame=int(scene.end_time * fps),
            )
        )

    return sub_segments
