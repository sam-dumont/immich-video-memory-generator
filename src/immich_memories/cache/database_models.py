"""Data models for video analysis cache."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.scenes import Scene
    from immich_memories.analysis.scoring import MomentScore


@dataclass
class CachedSegment:
    """Cached segment/scene data."""

    segment_index: int
    start_time: float
    end_time: float
    start_frame: int | None = None
    end_frame: int | None = None
    face_score: float | None = None
    motion_score: float | None = None
    stability_score: float | None = None
    audio_score: float | None = None
    total_score: float | None = None
    face_positions: list[tuple[float, float]] | None = None
    motion_vectors: dict | None = None
    keyframe_path: str | None = None

    # LLM analysis results (persisted from unified analysis)
    llm_description: str | None = None
    llm_emotion: str | None = None
    llm_setting: str | None = None
    llm_activities: list[str] | None = None
    llm_subjects: list[str] | None = None
    llm_interestingness: float | None = None
    llm_quality: float | None = None

    # Audio content categories (from PANNs analysis)
    audio_categories: list[str] | None = None

    @property
    def duration(self) -> float:
        """Get segment duration."""
        return self.end_time - self.start_time

    def to_moment_score(self) -> MomentScore:
        """Convert to MomentScore for compatibility."""
        from immich_memories.analysis.scoring import MomentScore

        return MomentScore(
            start_time=self.start_time,
            end_time=self.end_time,
            total_score=self.total_score or 0.0,
            face_score=self.face_score or 0.0,
            motion_score=self.motion_score or 0.0,
            audio_score=self.audio_score or 0.0,
            stability_score=self.stability_score or 0.0,
            face_positions=self.face_positions,
        )

    def to_scene(self) -> Scene:
        """Convert to Scene for compatibility."""
        from immich_memories.analysis.scenes import Scene

        return Scene(
            start_time=self.start_time,
            end_time=self.end_time,
            start_frame=self.start_frame or 0,
            end_frame=self.end_frame or 0,
            keyframe_path=self.keyframe_path,
        )


@dataclass
class CachedVideoAnalysis:
    """Cached video analysis result."""

    asset_id: str
    checksum: str | None
    file_modified_at: datetime | None
    analysis_timestamp: datetime

    # Hashes
    perceptual_hash: str | None = None
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

    # JSON fields (parsed)
    motion_summary: dict | None = None
    audio_levels: dict | None = None

    # File creation date (for queries)
    file_created_at: datetime | None = None

    # Associated segments (loaded separately)
    segments: list[CachedSegment] = field(default_factory=list)

    def get_best_segment(self) -> CachedSegment | None:
        """Get the highest-scoring segment."""
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


@dataclass
class SimilarVideo:
    """A video similar to a query video."""

    asset_id: str
    hash_value: str
    hamming_distance: int


def _hamming_distance(hash1: str, hash2: str) -> int:
    """Compute Hamming distance between two hex hash strings."""
    try:
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
        xor = int1 ^ int2
        return bin(xor).count("1")
    except (ValueError, TypeError):
        return 64  # Maximum distance if hashes are invalid
