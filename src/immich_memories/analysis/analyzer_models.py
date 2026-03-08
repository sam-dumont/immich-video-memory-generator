"""Data models for unified video segment analysis.

This module contains the core data classes used by UnifiedSegmentAnalyzer
and related components for representing cut points and scored segments.
"""

from __future__ import annotations

from dataclasses import dataclass

from immich_memories.analysis.scoring import MomentScore


@dataclass
class CutPoint:
    """A potential cut point in a video.

    Cut points can be visual (scene change), audio (silence gap boundary),
    or both. Points that are both visual and audio are ideal cut locations.
    """

    time: float
    is_visual: bool = False  # From PySceneDetect
    is_audio: bool = False  # From silence detection

    @property
    def priority(self) -> int:
        """Get cut point priority.

        Returns:
            2 = both visual+audio (ideal), 1 = one type, 0 = neither
        """
        return int(self.is_visual) + int(self.is_audio)

    def __lt__(self, other: CutPoint) -> bool:
        """Enable sorting by time."""
        return self.time < other.time


@dataclass
class ScoredSegment:
    """A scored video segment with timing and quality metrics."""

    start_time: float
    end_time: float
    visual_score: float = 0.0  # Combined face, motion, stability
    content_score: float = 0.5  # LLM analysis (default neutral, not 0)
    audio_score: float = 0.5  # Audio content score (laughter, speech, etc.)
    duration_score: float = 0.5  # Duration preference score (peaks at optimal duration)
    total_score: float = 0.0
    start_cut_priority: int = 0  # Priority of start cut point
    end_cut_priority: int = 0  # Priority of end cut point

    # Component scores for debugging/display
    face_score: float = 0.0
    motion_score: float = 0.0
    stability_score: float = 0.0

    # Audio analysis results
    has_laughter: bool = False
    has_speech: bool = False
    has_music: bool = False

    # LLM analysis results (populated by content analyzer)
    llm_description: str | None = None
    llm_emotion: str | None = None
    llm_setting: str | None = None
    llm_activities: list[str] | None = None
    llm_subjects: list[str] | None = None
    llm_interestingness: float | None = None
    llm_quality: float | None = None

    @property
    def duration(self) -> float:
        """Get segment duration in seconds."""
        return self.end_time - self.start_time

    @property
    def cut_quality(self) -> float:
        """Get overall cut quality (0-1 based on cut point priorities)."""
        return (self.start_cut_priority + self.end_cut_priority) / 4.0

    def to_moment_score(self) -> MomentScore:
        """Convert to MomentScore for compatibility with existing code."""
        # Ensure all values are Python floats (not numpy.float64) for SQLite compatibility
        return MomentScore(
            start_time=float(self.start_time),
            end_time=float(self.end_time),
            total_score=float(self.total_score),
            face_score=float(self.face_score),
            motion_score=float(self.motion_score),
            stability_score=float(self.stability_score),
            audio_score=float(self.audio_score),
            content_score=float(self.content_score),
        )
