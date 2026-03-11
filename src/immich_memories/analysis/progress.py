"""Progress tracking with dynamic ETA calculation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

# Memory optimization: limit accumulated history to prevent unbounded growth
MAX_COMPLETED_HISTORY = 100  # Keep last 100 for rolling average calculations
MAX_ERROR_HISTORY = 50  # Keep last 50 errors for display


class PipelinePhase(Enum):
    """Phases of the smart pipeline."""

    NOT_STARTED = 0
    CLUSTERING = 1
    FILTERING = 2
    ANALYZING = 3
    REFINING = 4
    COMPLETE = 5

    @property
    def label(self) -> str:
        """Human-readable label for the phase."""
        labels = {
            PipelinePhase.NOT_STARTED: "Not Started",
            PipelinePhase.CLUSTERING: "Clustering Similar Videos",
            PipelinePhase.FILTERING: "Filtering & Pre-Selecting",
            PipelinePhase.ANALYZING: "Analyzing Selected Clips",
            PipelinePhase.REFINING: "Refining Final Selection",
            PipelinePhase.COMPLETE: "Complete",
        }
        return labels[self]


@dataclass
class CompletedItem:
    """Record of a completed processing item."""

    item_id: str
    duration_seconds: float
    success: bool = True
    error: str | None = None


@dataclass
class PipelineProgress:
    """Current state of the pipeline."""

    phase: PipelinePhase = PipelinePhase.NOT_STARTED
    current_item: str | None = None
    current_asset_id: str | None = None  # Asset ID for thumbnail lookup
    current_index: int = 0
    total_items: int = 0
    completed: list[CompletedItem] = field(default_factory=list)
    errors: list[CompletedItem] = field(default_factory=list)
    start_time: float | None = None
    phase_start_time: float | None = None

    # Last completed item details (for preview)
    last_completed_asset_id: str | None = None
    last_completed_segment: tuple[float, float] | None = None  # (start, end)
    last_completed_score: float | None = None
    last_completed_video_path: str | None = None  # Path to preview segment

    # LLM analysis results for last completed item
    last_completed_llm_description: str | None = None
    last_completed_llm_emotion: str | None = None
    last_completed_llm_interestingness: float | None = None
    last_completed_llm_quality: float | None = None

    # Audio categories for last completed item
    last_completed_audio_categories: list[str] | None = None

    # Speed ratio tracking
    total_video_duration: float = 0.0  # Total video seconds processed
    total_processing_time: float = 0.0  # Total real seconds spent

    @property
    def progress_fraction(self) -> float:
        """Progress as a fraction from 0.0 to 1.0."""
        if self.total_items == 0:
            return 0.0
        return min(1.0, self.current_index / self.total_items)

    @property
    def elapsed_seconds(self) -> float:
        """Total elapsed time in seconds."""
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    @property
    def phase_elapsed_seconds(self) -> float:
        """Elapsed time in current phase."""
        if self.phase_start_time is None:
            return 0.0
        return time.time() - self.phase_start_time


class ProgressTracker:
    """Tracks progress and calculates dynamic ETA.

    Uses a rolling average of recent processing times for accurate estimates.
    """

    def __init__(
        self,
        total_phases: int = 4,
        rolling_window: int = 5,
        initial_estimate_seconds: float = 30.0,
    ):
        """Initialize the progress tracker.

        Args:
            total_phases: Total number of pipeline phases.
            rolling_window: Number of recent items to use for ETA calculation.
            initial_estimate_seconds: Initial time estimate per item before data.
        """
        self.total_phases = total_phases
        self.rolling_window = rolling_window
        self.initial_estimate = initial_estimate_seconds
        self.progress = PipelineProgress()
        self._item_start_time: float | None = None
        self._callbacks: list[Callable[[PipelineProgress], None]] = []

    def add_callback(self, callback: Callable[[PipelineProgress], None]) -> None:
        """Add a callback to be called on progress updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[PipelineProgress], None]) -> None:
        """Remove a progress callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_callbacks(self) -> None:
        """Notify all registered callbacks of progress update."""
        for callback in self._callbacks:
            try:
                callback(self.progress)
            except Exception:
                pass  # Don't let callback errors break the pipeline

    def start(self) -> None:
        """Start the pipeline."""
        self.progress = PipelineProgress()
        self.progress.start_time = time.time()
        self._notify_callbacks()

    def start_phase(
        self,
        phase: PipelinePhase,
        total_items: int,
    ) -> None:
        """Start a new pipeline phase.

        Args:
            phase: The phase to start.
            total_items: Total items to process in this phase.
        """
        self.progress.phase = phase
        self.progress.total_items = total_items
        self.progress.current_index = 0
        self.progress.current_item = None
        self.progress.phase_start_time = time.time()
        self._notify_callbacks()

    def start_item(self, item_name: str, asset_id: str | None = None) -> None:
        """Start processing an item.

        Args:
            item_name: Display name for the item being processed.
            asset_id: Optional asset ID for thumbnail lookup.
        """
        self.progress.current_item = item_name
        self.progress.current_asset_id = asset_id
        self._item_start_time = time.time()

        # Note: We intentionally do NOT clear last_completed_* fields here.
        # The "last completed" preview and analysis should remain visible
        # while the new item is being processed. They will be updated
        # when the new item completes via complete_item().

        self._notify_callbacks()

    def complete_item(
        self,
        item_id: str,
        success: bool = True,
        error: str | None = None,
        video_duration: float | None = None,
        segment: tuple[float, float] | None = None,
        score: float | None = None,
        preview_path: str | None = None,
        llm_description: str | None = None,
        llm_emotion: str | None = None,
        llm_interestingness: float | None = None,
        llm_quality: float | None = None,
        audio_categories: list[str] | None = None,
    ) -> None:
        """Mark an item as complete.

        Args:
            item_id: Identifier for the completed item.
            success: Whether processing succeeded.
            error: Error message if failed.
            video_duration: Duration of the video in seconds (for speed ratio).
            segment: Selected highlight segment (start, end) times.
            score: Quality score for the selected segment.
            preview_path: Path to extracted preview video file.
            llm_description: LLM-generated description of the video content.
            llm_emotion: LLM-detected emotional tone.
            llm_interestingness: LLM-scored interestingness (0-1).
            llm_quality: LLM-scored visual quality (0-1).
        """
        duration = 0.0
        if self._item_start_time is not None:
            duration = time.time() - self._item_start_time

        completed = CompletedItem(
            item_id=item_id,
            duration_seconds=duration,
            success=success,
            error=error,
        )

        if success:
            self.progress.completed.append(completed)

            # Memory optimization: trim completed history if it exceeds limit
            if len(self.progress.completed) > MAX_COMPLETED_HISTORY:
                self.progress.completed = self.progress.completed[-MAX_COMPLETED_HISTORY:]

            # Only update "Last Analyzed" display fields when we have
            # displayable data. Cached clips with no preview/LLM should NOT
            # overwrite the previous fresh clip's display.
            has_display_data = (
                preview_path is not None
                or llm_description is not None
                or audio_categories is not None
            )
            if has_display_data:
                self.progress.last_completed_asset_id = item_id
                self.progress.last_completed_segment = segment
                self.progress.last_completed_score = score
                if preview_path is not None:
                    self.progress.last_completed_video_path = preview_path
                if llm_description is not None:
                    self.progress.last_completed_llm_description = llm_description
                if llm_emotion is not None:
                    self.progress.last_completed_llm_emotion = llm_emotion
                if llm_interestingness is not None:
                    self.progress.last_completed_llm_interestingness = llm_interestingness
                if llm_quality is not None:
                    self.progress.last_completed_llm_quality = llm_quality
                if audio_categories is not None:
                    self.progress.last_completed_audio_categories = audio_categories

            # Track video duration for speed ratio
            if video_duration is not None and video_duration > 0:
                self.progress.total_video_duration += video_duration
                self.progress.total_processing_time += duration
        else:
            self.progress.errors.append(completed)

            # Memory optimization: trim error history if it exceeds limit
            if len(self.progress.errors) > MAX_ERROR_HISTORY:
                self.progress.errors = self.progress.errors[-MAX_ERROR_HISTORY:]

        self.progress.current_index += 1
        self.progress.current_item = None
        self.progress.current_asset_id = None
        self._item_start_time = None
        self._notify_callbacks()

    def complete_phase(self) -> None:
        """Mark the current phase as complete."""
        self.progress.current_item = None
        self._item_start_time = None
        self._notify_callbacks()

    def finish(self) -> None:
        """Mark the pipeline as complete."""
        self.progress.phase = PipelinePhase.COMPLETE
        self.progress.current_item = None
        self._notify_callbacks()

    def get_eta_seconds(self, remaining_count: int | None = None) -> float:
        """Get estimated time remaining in seconds.

        Uses rolling average of recent processing times for accuracy.

        Args:
            remaining_count: Override for remaining items count.

        Returns:
            Estimated seconds remaining.
        """
        if remaining_count is None:
            remaining_count = self.progress.total_items - self.progress.current_index

        if remaining_count <= 0:
            return 0.0

        # Get recent successful completions
        recent = [c for c in self.progress.completed[-self.rolling_window :] if c.success]

        if len(recent) < 2:
            # Not enough data, use initial estimate
            return remaining_count * self.initial_estimate

        # Calculate rolling average
        avg_duration = sum(c.duration_seconds for c in recent) / len(recent)
        return avg_duration * remaining_count

    def get_average_duration(self) -> float:
        """Get average processing duration per item."""
        recent = [c for c in self.progress.completed[-self.rolling_window :] if c.success]
        if not recent:
            return self.initial_estimate
        return sum(c.duration_seconds for c in recent) / len(recent)

    def get_speed_ratio(self) -> float:
        """Get video_duration / processing_time ratio.

        Returns:
            Ratio of video content processed per second of real time.
            E.g., 2.5 means "2.5 seconds of video processed per real second".
        """
        if self.progress.total_processing_time <= 0:
            return 0.0
        return self.progress.total_video_duration / self.progress.total_processing_time

    def format_eta(self, seconds: float) -> str:
        """Format ETA as human-readable string.

        Args:
            seconds: Seconds remaining.

        Returns:
            Formatted string like "5m 30s" or "2h 15m".
        """
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            if secs > 0:
                return f"{minutes}m {secs}s"
            return f"{minutes}m"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            if minutes > 0:
                return f"{hours}h {minutes}m"
            return f"{hours}h"

    def format_elapsed(self) -> str:
        """Format elapsed time as human-readable string."""
        return self.format_eta(self.progress.elapsed_seconds)

    def get_status_summary(self) -> dict:
        """Get a summary of current status for UI display.

        Returns:
            Dictionary with status information.
        """
        remaining = self.progress.total_items - self.progress.current_index
        eta = self.get_eta_seconds(remaining)

        return {
            "phase": self.progress.phase,
            "phase_label": self.progress.phase.label,
            "phase_number": self.progress.phase.value,
            "total_phases": self.total_phases,
            "current_item": self.progress.current_item,
            "current_asset_id": self.progress.current_asset_id,
            "current_index": self.progress.current_index,
            "total_items": self.progress.total_items,
            "progress_fraction": self.progress.progress_fraction,
            "elapsed": self.format_elapsed(),
            "elapsed_seconds": self.progress.elapsed_seconds,
            "eta": self.format_eta(eta),
            "eta_seconds": eta,
            "avg_duration": self.get_average_duration(),
            "speed_ratio": self.get_speed_ratio(),
            "completed_count": len(self.progress.completed),
            "error_count": len(self.progress.errors),
            "errors": [{"id": e.item_id, "error": e.error} for e in self.progress.errors],
            # Last completed item details for preview
            "last_completed_asset_id": self.progress.last_completed_asset_id,
            "last_completed_segment": self.progress.last_completed_segment,
            "last_completed_score": self.progress.last_completed_score,
            "last_completed_video_path": self.progress.last_completed_video_path,
            # LLM analysis results for last completed item
            "last_completed_llm_description": self.progress.last_completed_llm_description,
            "last_completed_llm_emotion": self.progress.last_completed_llm_emotion,
            "last_completed_llm_interestingness": self.progress.last_completed_llm_interestingness,
            "last_completed_llm_quality": self.progress.last_completed_llm_quality,
            # Audio categories
            "last_completed_audio_categories": self.progress.last_completed_audio_categories,
        }
