"""Run tracker for active pipeline runs."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.tracking.models import PhaseStats, RunMetadata
from immich_memories.tracking.run_database import RunDatabase
from immich_memories.tracking.run_id import generate_run_id
from immich_memories.tracking.system_info import capture_system_info

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)


class RunTracker:
    """Tracks a single pipeline run's progress and statistics."""

    def __init__(
        self,
        run_id: str | None = None,
        *,
        db_path: Path,
        capture_system: bool = True,
    ):
        """Initialize a run tracker.

        Args:
            run_id: Optional run ID. Generated if not provided.
            db_path: Database path for run storage.
            capture_system: Whether to capture system info on start.
        """
        self.run_id = run_id or generate_run_id()
        self.db = RunDatabase(db_path)
        self._capture_system = capture_system

        # Current state
        self._run: RunMetadata | None = None
        self._current_phase: str | None = None
        self._phase_start: datetime | None = None
        self._phase_start_time: float | None = None
        self._phase_items_total: int = 0

    def start_run(
        self,
        person_name: str | None = None,
        person_id: str | None = None,
        date_range: DateRange | None = None,
        target_duration_seconds: int = 600,
    ) -> str:
        """Start tracking a new run.

        Args:
            person_name: Name of person (if filtering by person).
            person_id: ID of person (if filtering by person).
            date_range: Date range for the run.
            target_duration_seconds: Target video duration in seconds.

        Returns:
            The run ID.
        """
        system_info = None
        if self._capture_system:
            try:
                system_info = capture_system_info()
            except Exception as e:
                logger.warning(f"Failed to capture system info: {e}")

        self._run = RunMetadata(
            run_id=self.run_id,
            created_at=datetime.now(),
            status="running",
            person_name=person_name,
            person_id=person_id,
            date_range_start=date_range.start.date() if date_range else None,
            date_range_end=date_range.end.date() if date_range else None,
            target_duration_seconds=target_duration_seconds,
            system_info=system_info,
        )

        self.db.save_run(self._run)
        logger.info(f"Started run {self.run_id}")

        return self.run_id

    def start_phase(
        self,
        phase_name: str,
        total_items: int = 0,
    ) -> None:
        """Start tracking a phase.

        Args:
            phase_name: Name of the phase.
            total_items: Expected number of items to process.
        """
        # Complete previous phase if any
        if self._current_phase:
            self.complete_phase()

        self._current_phase = phase_name
        self._phase_start = datetime.now()
        self._phase_start_time = time.time()
        self._phase_items_total = total_items

        logger.debug(f"Started phase: {phase_name} (total items: {total_items})")

    def complete_phase(
        self,
        items_processed: int | None = None,
        errors: list[dict[str, Any]] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> None:
        """Complete the current phase.

        Args:
            items_processed: Number of items processed (defaults to total_items).
            errors: List of error dictionaries.
            extra_metrics: Additional phase-specific metrics.
        """
        if not self._current_phase or not self._phase_start:
            return

        now = datetime.now()
        duration = time.time() - (self._phase_start_time or time.time())

        if items_processed is None:
            items_processed = self._phase_items_total

        stats = PhaseStats(
            phase_name=self._current_phase,
            started_at=self._phase_start,
            completed_at=now,
            duration_seconds=duration,
            items_processed=items_processed,
            items_total=self._phase_items_total,
            errors=errors or [],
            extra_metrics=extra_metrics or {},
        )

        self.db.save_phase_stats(self.run_id, stats)

        logger.debug(
            f"Completed phase: {self._current_phase} "
            f"({items_processed}/{self._phase_items_total} items, {duration:.1f}s)"
        )

        # Reset phase state
        self._current_phase = None
        self._phase_start = None
        self._phase_start_time = None
        self._phase_items_total = 0

    def update_phase_progress(self, items_processed: int) -> None:
        """Update progress within current phase.

        Args:
            items_processed: Number of items processed so far.
        """
        # This is for logging/debugging - actual stats saved on complete
        if self._current_phase:
            logger.debug(
                f"Phase {self._current_phase}: {items_processed}/{self._phase_items_total}"
            )

    def complete_run(
        self,
        output_path: Path | str | None = None,
        clips_analyzed: int = 0,
        clips_selected: int = 0,
        errors_count: int = 0,
    ) -> RunMetadata:
        """Finalize run with output info.

        Args:
            output_path: Path to output video.
            clips_analyzed: Total clips analyzed.
            clips_selected: Clips selected for output.
            errors_count: Total errors encountered.

        Returns:
            The completed RunMetadata.
        """
        # Complete any pending phase
        if self._current_phase:
            self.complete_phase()

        now = datetime.now()

        # Get output file info
        output_size_bytes = 0
        output_duration_seconds = 0.0

        if output_path:
            output_path = Path(output_path)
            if output_path.exists():
                output_size_bytes = output_path.stat().st_size
                output_duration_seconds = self._get_video_duration(output_path)

        self.db.update_run_status(
            run_id=self.run_id,
            status="completed",
            completed_at=now,
            output_path=str(output_path) if output_path else None,
            output_size_bytes=output_size_bytes,
            output_duration_seconds=output_duration_seconds,
            clips_analyzed=clips_analyzed,
            clips_selected=clips_selected,
            errors_count=errors_count,
        )

        # Reload to get full data with phases
        run = self.db.get_run(self.run_id)
        if run:
            self._run = run

        logger.info(f"Completed run {self.run_id} (status: completed)")

        # Save metadata JSON alongside video
        if output_path and run:
            self._save_metadata_json(Path(output_path).parent, run)

        return self._run or run  # type: ignore

    def fail_run(self, error: str, errors_count: int = 1) -> None:
        """Mark run as failed.

        Args:
            error: Error message.
            errors_count: Total errors encountered.
        """
        # Complete any pending phase
        if self._current_phase:
            self.complete_phase(errors=[{"error": error}])

        now = datetime.now()

        self.db.update_run_status(
            run_id=self.run_id,
            status="failed",
            completed_at=now,
            errors_count=errors_count,
        )

        logger.error(f"Run {self.run_id} failed: {error}")

    def cancel_run(self) -> None:
        """Mark run as cancelled."""
        if self._current_phase:
            self.complete_phase()

        now = datetime.now()

        self.db.update_run_status(
            run_id=self.run_id,
            status="cancelled",
            completed_at=now,
        )

        logger.info(f"Run {self.run_id} cancelled")

    def _get_video_duration(self, path: Path) -> float:
        """Get video duration using ffprobe.

        Args:
            path: Path to video file.

        Returns:
            Duration in seconds.
        """
        try:
            import subprocess

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception as e:
            logger.debug(f"Failed to get video duration: {e}")

        return 0.0

    def _save_metadata_json(self, output_dir: Path, run: RunMetadata) -> None:
        """Save run_metadata.json alongside output video.

        Args:
            output_dir: Directory to save to.
            run: The run metadata.
        """
        try:
            metadata_path = output_dir / "run_metadata.json"
            metadata_path.write_text(run.to_json())
            logger.debug(f"Saved run metadata to {metadata_path}")
        except Exception as e:
            logger.warning(f"Failed to save run metadata: {e}")

    @property
    def current_run(self) -> RunMetadata | None:
        """Get the current run metadata."""
        return self._run


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string like "12m 34s" or "1h 23m".
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs:02d}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes:02d}m"
