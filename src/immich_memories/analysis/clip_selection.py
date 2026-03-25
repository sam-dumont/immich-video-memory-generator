"""Clip selection utilities for the smart pipeline.

Standalone functions for selecting, filtering, and ranking video clips
by quality, date distribution, and favorites status.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.config_models import AnalysisConfig, ContentAnalysisConfig

logger = logging.getLogger(__name__)

__all__ = [
    "analyze_clip_for_highlight",
    "smart_select_clips",
    "_clip_quality_key",
    "_allocate_day_slots",
    "_select_from_day",
    "_enforce_non_favorite_ratio",
    "_get_fast_encoder_args",
]


def _get_fast_encoder_args() -> list[str]:
    """Get fast encoder arguments with GPU acceleration when available.

    Returns encoder optimized for speed (preview temp files).
    """
    import subprocess
    import sys

    # macOS: Use VideoToolbox hardware encoder
    if sys.platform == "darwin":
        return [
            "-c:v",
            "h264_videotoolbox",
            "-q:v",
            "65",  # Lower quality OK for previews (faster)
        ]

    # Other platforms: Check for available encoders
    with contextlib.suppress(Exception):
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        encoders = result.stdout

        # Try NVIDIA NVENC (GPU accelerated)
        if "h264_nvenc" in encoders:
            return [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p1",  # Fastest preset
                "-rc",
                "constqp",
                "-qp",
                "23",
            ]

        # Try VAAPI (Linux GPU)
        if "h264_vaapi" in encoders:
            return [
                "-c:v",
                "h264_vaapi",
                "-qp",
                "23",
            ]

        # Try Intel QSV
        if "h264_qsv" in encoders:
            return [
                "-c:v",
                "h264_qsv",
                "-preset",
                "veryfast",
            ]

    # Fallback to CPU libx264
    return [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
    ]


def _clip_quality_key(c: VideoClipInfo) -> tuple:
    """Sort key for clip quality (resolution, bitrate, duration)."""
    res = c.width * c.height if c.width and c.height else 0
    return (res, c.bitrate, c.duration_seconds or 0)


def _allocate_day_slots(
    clips_by_day: dict[str, list[VideoClipInfo]],
    clips_needed: int,
) -> dict[str, int]:
    """Allocate clip slots per day, with extra for special days.

    Args:
        clips_by_day: Clips grouped by day key.
        clips_needed: Total clips needed.

    Returns:
        Dict of day -> number of slots allocated.
    """
    num_days = len(clips_by_day)
    base_per_day = max(1, clips_needed // num_days) if num_days > 0 else clips_needed

    avg_clips_per_day = sum(len(v) for v in clips_by_day.values()) / num_days if num_days > 0 else 0
    special_days = {
        day for day, day_clips in clips_by_day.items() if len(day_clips) > avg_clips_per_day * 1.5
    }

    slots_per_day: dict[str, int] = {}
    remaining_slots = clips_needed

    for day in clips_by_day:
        max_slots = base_per_day * 2 if day in special_days else base_per_day
        slots = min(max_slots, len(clips_by_day[day]), remaining_slots)
        slots_per_day[day] = slots
        remaining_slots -= slots

    # Distribute remaining slots to days with most clips
    if remaining_slots > 0:
        sorted_days = sorted(clips_by_day.keys(), key=lambda d: len(clips_by_day[d]), reverse=True)
        for day in sorted_days:
            if remaining_slots <= 0:
                break
            available = len(clips_by_day[day]) - slots_per_day[day]
            if available > 0:
                add = min(available, remaining_slots)
                slots_per_day[day] += add
                remaining_slots -= add

    return slots_per_day


def _select_from_day(
    day_clips: list[VideoClipInfo],
    slots: int,
    prioritize_favorites: bool,
) -> list[VideoClipInfo]:
    """Select clips from a single day.

    Args:
        day_clips: All clips for this day.
        slots: Number of clips to select.
        prioritize_favorites: Whether to pick favorites first.

    Returns:
        Selected clips for this day.
    """
    if not prioritize_favorites:
        return sorted(day_clips, key=_clip_quality_key, reverse=True)[:slots]

    favorites = sorted(
        [c for c in day_clips if c.asset.is_favorite], key=_clip_quality_key, reverse=True
    )
    others = sorted(
        [c for c in day_clips if not c.asset.is_favorite], key=_clip_quality_key, reverse=True
    )

    day_selected = favorites[:slots]
    remaining = slots - len(day_selected)
    if remaining > 0:
        day_selected.extend(others[:remaining])
    return day_selected


def _enforce_non_favorite_ratio(
    selected: list[VideoClipInfo],
    max_non_favorite_ratio: float,
) -> list[VideoClipInfo]:
    """Trim non-favorites to enforce ratio limit.

    Args:
        selected: Current selection.
        max_non_favorite_ratio: Maximum ratio of non-favorites.

    Returns:
        Selection with ratio enforced.
    """
    favorites_selected = [c for c in selected if c.asset.is_favorite]
    non_favorites_selected = [c for c in selected if not c.asset.is_favorite]

    max_non_favorites = int(len(selected) * max_non_favorite_ratio)

    if len(non_favorites_selected) <= max_non_favorites:
        return selected

    non_favorites_selected.sort(key=_clip_quality_key, reverse=True)
    non_favorites_to_keep = non_favorites_selected[:max_non_favorites]

    removed_count = len(non_favorites_selected) - len(non_favorites_to_keep)
    logger.info(
        f"Non-favorite ratio limit: keeping {len(non_favorites_to_keep)}/{len(non_favorites_selected)} "
        f"non-favorites ({max_non_favorite_ratio:.0%} max), removed {removed_count}"
    )

    return favorites_selected + non_favorites_to_keep


def smart_select_clips(
    clips: list[VideoClipInfo],
    clips_needed: int,
    hdr_only: bool = False,
    prioritize_favorites: bool = True,
    max_non_favorite_ratio: float = 1.0,
) -> list[VideoClipInfo]:
    """Smart clip selection algorithm.

    Distributes clips across days, prioritizing favorites and special days.

    Args:
        clips: Available clips.
        clips_needed: Target number of clips.
        hdr_only: Only select HDR clips.
        prioritize_favorites: Prioritize favorite clips.
        max_non_favorite_ratio: Maximum ratio of non-favorites.

    Returns:
        Selected clips.
    """
    from collections import defaultdict

    if hdr_only:
        clips = [c for c in clips if c.is_hdr]

    if not clips:
        return []

    # Group by day
    clips_by_day: dict[str, list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        clips_by_day[clip.asset.file_created_at.strftime("%Y-%m-%d")].append(clip)

    # Allocate slots per day
    slots_per_day = _allocate_day_slots(clips_by_day, clips_needed)

    # Select from each day
    selected: list[VideoClipInfo] = []
    for day, slots in slots_per_day.items():
        selected.extend(_select_from_day(clips_by_day[day], slots, prioritize_favorites))

    # Enforce non-favorite ratio
    if max_non_favorite_ratio < 1.0 and prioritize_favorites:
        selected = _enforce_non_favorite_ratio(selected, max_non_favorite_ratio)

    selected.sort(key=lambda c: c.asset.file_created_at or datetime.min)
    return selected


def analyze_clip_for_highlight(
    video_path: Path,
    min_duration: float = 3.0,
    max_duration: float = 15.0,
    target_duration: float = 5.0,
    *,
    content_analysis_config: ContentAnalysisConfig,
    analysis_config: AnalysisConfig,
) -> tuple[float, float, float]:
    """Analyze a single clip to find the best highlight segment.

    Uses UnifiedSegmentAnalyzer for silence-aware boundaries and
    audio scoring (when enabled).
    """
    from immich_memories.analysis.scoring import SceneScorer
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
    from immich_memories.config_models import AudioContentConfig

    scorer = SceneScorer(
        content_analysis_config=content_analysis_config,
        analysis_config=analysis_config,
    )
    analyzer = UnifiedSegmentAnalyzer(
        scorer=scorer,
        min_segment_duration=min_duration,
        max_segment_duration=max_duration,
        audio_content_config=AudioContentConfig(),
        analysis_config=analysis_config,
    )
    segments = analyzer.analyze(video_path)

    if not segments:
        return 0.0, target_duration, 0.0

    best = segments[0]  # Already sorted by score (best first)

    duration = min(max(best.end_time - best.start_time, min_duration), max_duration)
    if duration > target_duration:
        duration = target_duration

    start = best.start_time
    end = start + duration

    return start, end, best.total_score
