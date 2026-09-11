"""Data models for video analysis cache."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CachedSegment:
    """Cached segment/scene data."""

    segment_index: int
    start_time: float
    end_time: float
    start_frame: int | None = None
    total_score: float | None = None
    face_positions: list[tuple[float, float]] | None = None
    motion_vectors: dict | None = None

    # LLM analysis results (persisted from unified analysis)
    llm_description: str | None = None
    llm_category: str | None = None
    llm_emotion: str | None = None
    llm_setting: str | None = None
    llm_subjects: list[str] | None = None
    llm_quality: float | None = None

    # Audio content categories (from PANNs analysis)
    audio_categories: list[str] | None = None

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass
class CachedVideoAnalysis:
    """Cached video analysis result."""

    asset_id: str
    checksum: str | None
    analysis_timestamp: datetime

    scoring_version: int = 1
    model_version: str | None = None
    thumbnail_hash: str | None = None

    # Video metadata
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    bitrate: int | None = None
    fps: float | None = None
    codec: str | None = None

    # HDR
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    bit_depth: int | None = None

    # Best scores
    best_face_score: float | None = None
    best_motion_score: float | None = None
    best_stability_score: float | None = None
    best_audio_score: float | None = None
    best_total_score: float | None = None

    # File creation date (for queries)
    file_created_at: datetime | None = None

    # Associated segments (loaded separately)
    segments: list[CachedSegment] = field(default_factory=list)

    def get_best_segment(self) -> CachedSegment | None:
        if not self.segments:
            return None

        def safe_score(s: CachedSegment) -> float:
            """Safely get score, handling corrupted cache data (bytes)."""
            score = s.total_score
            if score is None:
                return 0.0
            if isinstance(score, (int, float)):
                return float(score)
            try:
                return float(score)
            except (ValueError, TypeError):
                return 0.0

        return max(self.segments, key=safe_score)
