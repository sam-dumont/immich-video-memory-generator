"""Clip extraction and trimming."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from immich_memories.processing.clip_encoding import ClipEncodingMixin
from immich_memories.processing.clip_probing import (
    _parse_bit_depth,
    _parse_frame_rate,
    _parse_probe_streams,
    _parse_rotation,
    _validate_header,
    _validate_url,
    get_video_duration,
    get_video_info,
    probe_video_url,
)
from immich_memories.processing.clip_transitions import (
    BOUNDARY_TOLERANCE,
    TRANSITION_BUFFER,
    ClipTransitionInfo,
    TransitionPlan,
    plan_transitions,
)
from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility
__all__ = [
    "BOUNDARY_TOLERANCE",
    "TRANSITION_BUFFER",
    "ClipExtractor",
    "ClipSegment",
    "ClipTransitionInfo",
    "TransitionPlan",
    "_parse_bit_depth",
    "_parse_frame_rate",
    "_parse_probe_streams",
    "_parse_rotation",
    "_validate_header",
    "_validate_url",
    "extract_clip",
    "get_video_duration",
    "get_video_info",
    "plan_transitions",
    "probe_video_url",
]


@dataclass
class ClipSegment:
    """A segment of a video clip to use."""

    source_path: Path
    start_time: float
    end_time: float
    asset_id: str
    score: float = 0.0
    output_path: Path | None = None

    @property
    def duration(self) -> float:
        """Get segment duration."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "source_path": str(self.source_path),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "asset_id": self.asset_id,
            "score": self.score,
            "output_path": str(self.output_path) if self.output_path else None,
        }


class ClipExtractor(ClipEncodingMixin):
    """Extract and process video clips."""

    def __init__(self, output_dir: Path | None = None):
        """Initialize the clip extractor.

        Args:
            output_dir: Directory for extracted clips.
        """
        if output_dir is None:
            output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "clips"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def cleanup_old_clips(self, max_age_hours: int = 24) -> int:
        """Remove clip files older than max_age_hours.

        Args:
            max_age_hours: Maximum age in hours for cached clips.

        Returns:
            Number of files removed.
        """
        if not self.output_dir.exists():
            return 0

        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        for path in self.output_dir.iterdir():
            if path.is_file() and path.suffix == ".mp4":
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            logger.info(f"Cleaned up {removed} old clip(s) from {self.output_dir}")
        return removed

    def extract(
        self,
        segment: ClipSegment,
        reencode: bool = False,
        progress_callback: Callable[[float], None] | None = None,
        with_buffer: bool = False,
        buffer_seconds: float = TRANSITION_BUFFER,
        buffer_start: bool | None = None,
        buffer_end: bool | None = None,
    ) -> Path:
        """Extract a clip segment from a video.

        Args:
            segment: Clip segment to extract.
            reencode: If True, re-encode the video (slower but more compatible).
            progress_callback: Optional callback for progress updates.
            with_buffer: If True, add buffer footage before/after for transitions.
            buffer_seconds: Amount of buffer to add (default 0.5s each side).
            buffer_start: If True, add buffer at start. Overrides with_buffer.
            buffer_end: If True, add buffer at end. Overrides with_buffer.

        Returns:
            Path to the extracted clip.
        """
        if not segment.source_path.exists():
            raise FileNotFoundError(f"Source video not found: {segment.source_path}")

        add_start_buffer = buffer_start if buffer_start is not None else with_buffer
        add_end_buffer = buffer_end if buffer_end is not None else with_buffer

        if add_start_buffer or add_end_buffer:
            extraction_segment, output_filename = self._make_buffered_segment(
                segment, add_start_buffer, add_end_buffer, buffer_seconds
            )
        else:
            output_filename = (
                f"{segment.asset_id}_{segment.start_time:.1f}_{segment.end_time:.1f}.mp4"
            )
            extraction_segment = segment

        output_path = self.output_dir / output_filename

        if output_path.exists():
            logger.debug(f"Clip already exists: {output_path}")
            segment.output_path = output_path
            return output_path

        if reencode:
            self._extract_with_reencode(extraction_segment, output_path, progress_callback)
        else:
            self._extract_copy(extraction_segment, output_path)

        segment.output_path = output_path
        return output_path

    def _make_buffered_segment(
        self,
        segment: ClipSegment,
        add_start_buffer: bool,
        add_end_buffer: bool,
        buffer_seconds: float,
    ) -> tuple[ClipSegment, str]:
        """Create a buffered segment and filename for transition extraction.

        Args:
            segment: Original clip segment.
            add_start_buffer: Whether to add buffer at start.
            add_end_buffer: Whether to add buffer at end.
            buffer_seconds: Amount of buffer in seconds.

        Returns:
            Tuple of (buffered_segment, output_filename).
        """
        video_duration = get_video_duration(segment.source_path)

        buffered_start = (
            max(0, segment.start_time - buffer_seconds) if add_start_buffer else segment.start_time
        )

        if add_end_buffer:
            buffered_end = (
                min(video_duration, segment.end_time + buffer_seconds)
                if video_duration > 0
                else segment.end_time + buffer_seconds
            )
        else:
            buffered_end = segment.end_time

        buffered_segment = ClipSegment(
            source_path=segment.source_path,
            start_time=buffered_start,
            end_time=buffered_end,
            asset_id=segment.asset_id,
            score=segment.score,
        )
        buffer_suffix = f"_b{int(add_start_buffer)}{int(add_end_buffer)}"
        output_filename = (
            f"{segment.asset_id}_{buffered_start:.1f}_{buffered_end:.1f}{buffer_suffix}.mp4"
        )
        return buffered_segment, output_filename

    def _extract_copy(self, segment: ClipSegment, output_path: Path) -> None:
        """Extract clip using stream copy (fast, no quality loss)."""
        validate_video_path(segment.source_path, must_exist=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(segment.start_time),
            "-i",
            str(segment.source_path),
            "-t",
            str(segment.duration),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            raise RuntimeError(f"Failed to extract clip: {result.stderr}")

    def batch_extract(
        self,
        segments: list[ClipSegment],
        reencode: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
        with_buffer: bool = False,
        buffer_seconds: float = TRANSITION_BUFFER,
        transition_plan: TransitionPlan | None = None,
    ) -> list[Path]:
        """Extract multiple clip segments.

        Args:
            segments: List of segments to extract.
            reencode: Whether to re-encode clips.
            progress_callback: Callback with (current, total) counts.
            with_buffer: If True, add buffer footage for transitions (legacy).
            buffer_seconds: Amount of buffer to add (default 0.5s each side).
            transition_plan: Pre-decided transition plan with per-clip buffer flags.

        Returns:
            List of paths to extracted clips.
        """
        self.cleanup_old_clips()

        results = []
        failures: list[tuple[str, str]] = []

        for i, segment in enumerate(segments):
            try:
                if transition_plan is not None:
                    path = self.extract(
                        segment,
                        reencode=reencode,
                        buffer_seconds=buffer_seconds,
                        buffer_start=transition_plan.buffer_start[i],
                        buffer_end=transition_plan.buffer_end[i],
                    )
                else:
                    path = self.extract(
                        segment,
                        reencode=reencode,
                        with_buffer=with_buffer,
                        buffer_seconds=buffer_seconds,
                    )
                results.append(path)
            except Exception as e:
                logger.error(f"Failed to extract segment {segment.asset_id}: {e}")
                failures.append((segment.asset_id, str(e)))
                continue

            if progress_callback:
                progress_callback(i + 1, len(segments))

        if failures:
            logger.warning(
                f"{len(failures)}/{len(segments)} clips failed extraction: "
                f"{', '.join(asset_id for asset_id, _ in failures[:5])}"
            )

        return results


def extract_clip(
    source_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path | None = None,
    reencode: bool = False,
    buffer_start: bool = False,
    buffer_end: bool = False,
    buffer_seconds: float = TRANSITION_BUFFER,
) -> Path:
    """Convenience function to extract a single clip.

    Args:
        source_path: Path to source video.
        start_time: Start time in seconds.
        end_time: End time in seconds.
        output_path: Optional output path.
        reencode: Whether to re-encode.
        buffer_start: If True, add buffer at start for incoming crossfade.
        buffer_end: If True, add buffer at end for outgoing crossfade.
        buffer_seconds: Amount of buffer to add (default 0.5s).

    Returns:
        Path to extracted clip.
    """
    actual_start, actual_end = _resolve_buffer_times(
        source_path, start_time, end_time, buffer_start, buffer_end, buffer_seconds
    )

    if output_path is None:
        output_path = _build_clip_output_path(
            source_path, actual_start, actual_end, buffer_start, buffer_end, reencode
        )

    segment = ClipSegment(
        source_path=Path(source_path),
        start_time=actual_start,
        end_time=actual_end,
        asset_id="temp",
    )

    extractor = ClipExtractor(output_path.parent)
    segment.output_path = output_path

    if output_path.exists():
        logger.debug(f"Clip already exists: {output_path}")
        return output_path

    if reencode:
        extractor._extract_with_reencode(segment, output_path, None)
    else:
        extractor._extract_copy(segment, output_path)

    return output_path


def _resolve_buffer_times(
    source_path: Path,
    start_time: float,
    end_time: float,
    buffer_start: bool,
    buffer_end: bool,
    buffer_seconds: float,
) -> tuple[float, float]:
    """Calculate actual start/end times with buffer applied."""
    actual_start = start_time
    actual_end = end_time

    if buffer_start or buffer_end:
        video_duration = get_video_duration(source_path)
        if buffer_start:
            actual_start = max(0, start_time - buffer_seconds)
        if buffer_end:
            actual_end = (
                min(video_duration, end_time + buffer_seconds)
                if video_duration > 0
                else end_time + buffer_seconds
            )

    return actual_start, actual_end


def _build_clip_output_path(
    source_path: Path,
    actual_start: float,
    actual_end: float,
    buffer_start: bool,
    buffer_end: bool,
    reencode: bool,
) -> Path:
    """Build the output path for a standalone clip extraction."""
    output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.md5(str(source_path).encode(), usedforsecurity=False).hexdigest()[:8]  # noqa: S324
    buffer_suffix = (
        f"_b{int(buffer_start)}{int(buffer_end)}" if (buffer_start or buffer_end) else ""
    )
    enc_suffix = "_enc" if reencode else ""
    return (
        output_dir
        / f"clip_{source_hash}_{actual_start:.1f}_{actual_end:.1f}{buffer_suffix}{enc_suffix}.mp4"
    )
