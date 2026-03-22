"""AutoRunner — detect, score, and generate memory candidates."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import MemoryCandidate
from immich_memories.config_loader import Config
from immich_memories.tracking.run_database import RunDatabase

logger = logging.getLogger(__name__)


def _time_buckets_to_month_counts(
    buckets: list,
) -> dict[str, int]:
    """Convert Immich TimeBucket list to {YYYY-MM: count} dict."""
    result: dict[str, int] = {}
    for bucket in buckets:
        try:
            dt = datetime.fromisoformat(bucket.time_bucket)
            key = f"{dt.year}-{dt.month:02d}"
            result[key] = bucket.count
        except (ValueError, AttributeError):
            continue
    return result


def _build_last_runs_by_type(db: RunDatabase) -> dict[str, date]:
    """Query DB for the most recent completed run date per memory type."""
    result: dict[str, date] = {}
    for mem_type in ("monthly_highlights", "year_in_review", "person_spotlight", "trip"):
        run = db.get_last_run_of_type(mem_type)
        if run and run.created_at:
            result[mem_type] = run.created_at.date()
    return result


def _build_generate_command(candidate: MemoryCandidate, upload: bool) -> list[str]:
    """Build CLI subprocess command from a candidate."""
    cmd = ["immich-memories", "generate"]

    mem_type = candidate.memory_type
    if mem_type == "monthly_highlights":
        cmd.extend(["--memory-type", "monthly_highlights"])
        cmd.extend(["--year", str(candidate.date_range_start.year)])
        cmd.extend(["--month", str(candidate.date_range_start.month)])
    elif mem_type == "year_in_review":
        cmd.extend(["--memory-type", "year_in_review"])
        cmd.extend(["--year", str(candidate.date_range_start.year)])
    elif mem_type == "person_spotlight":
        cmd.extend(["--memory-type", "person_spotlight"])
        cmd.extend(["--year", str(candidate.date_range_start.year)])
        for name in candidate.person_names:
            cmd.extend(["--person", name])
    elif mem_type == "trip":
        cmd.extend(["--memory-type", "trip"])
        cmd.extend(["--year", str(candidate.date_range_start.year)])
        # Trip detection will re-detect; pass start date as hint
        cmd.extend(["--start", candidate.date_range_start.isoformat()])
        cmd.extend(["--end", candidate.date_range_end.isoformat()])

    if upload:
        cmd.append("--upload-to-immich")

    return cmd


def _is_within_cooldown(db: RunDatabase, cooldown_hours: int) -> bool:
    """Check if the most recent completed run is within the cooldown window."""
    runs = db.list_runs(limit=1, status="completed")
    if not runs:
        return False
    last_completed = runs[0].created_at
    now = datetime.now(tz=UTC)
    if last_completed.tzinfo is None:
        # DB stores naive datetimes — treat as UTC
        last_completed = last_completed.replace(tzinfo=UTC)
    hours_since = (now - last_completed).total_seconds() / 3600
    if hours_since < cooldown_hours:
        logger.info(
            "Cooldown active: %.1fh since last run (need %dh)",
            hours_since,
            cooldown_hours,
        )
        return True
    return False


def _execute_generate(cmd: list[str]) -> bool:
    """Run the generate subprocess, return True on success."""
    try:
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=7200
        )
    except subprocess.TimeoutExpired:
        logger.error("Generation timed out after 2 hours")
        return False

    if result.returncode != 0:
        logger.error(
            "Generation failed (exit %d): %s",
            result.returncode,
            result.stderr[-500:] if result.stderr else "no stderr",
        )
        return False

    return True


def _send_notification(
    config: Config,
    memory_type: str,
    success: bool,
    duration_seconds: float = 0.0,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    """Fire an Apprise notification if configured."""
    notif = config.notifications
    if not notif.enabled or not notif.urls:
        return
    status = "completed" if success else "failed"
    if (success and not notif.on_success) or (not success and not notif.on_failure):
        return

    from immich_memories.automation.notifications import notify_job_complete

    notify_job_complete(
        memory_type=memory_type,
        status=status,
        duration_seconds=duration_seconds,
        output_path=output_path,
        error=error,
        urls=notif.urls,
    )


class AutoRunner:
    """Orchestrates candidate detection and one-shot generation."""

    def __init__(self, config: Config):
        self.config = config
        self.db = RunDatabase(db_path=config.cache.database_path)

    def suggest(self, limit: int = 10) -> list[MemoryCandidate]:
        """Detect, score, and rank memory candidates from the Immich library."""
        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.automation.calendar_detectors import (
            MonthlyDetector,
            PersonSpotlightDetector,
            YearlyDetector,
        )
        from immich_memories.automation.event_detectors import (
            ActivityBurstDetector,
            TripDetector,
        )
        from immich_memories.preflight import CheckStatus, check_immich

        immich_result = check_immich(self.config)
        if immich_result.status == CheckStatus.ERROR:
            logger.error("Immich preflight failed: %s", immich_result.message)
            return []

        auto_cfg = self.config.automation
        generated_keys = self.db.get_generated_memory_keys()
        last_runs = _build_last_runs_by_type(self.db)
        today = date.today()

        with SyncImmichClient(
            base_url=self.config.immich.url,
            api_key=self.config.immich.api_key,
        ) as client:
            buckets = client.get_time_buckets()
            assets_by_month = _time_buckets_to_month_counts(buckets)
            people = client.get_all_people() if auto_cfg.detect_person_spotlight else []

        all_candidates: list[MemoryCandidate] = []

        if auto_cfg.detect_monthly:
            all_candidates.extend(
                MonthlyDetector().detect(
                    assets_by_month, people, generated_keys, self.config, today
                )
            )

        if auto_cfg.detect_yearly:
            all_candidates.extend(
                YearlyDetector().detect(assets_by_month, people, generated_keys, self.config, today)
            )

        if auto_cfg.detect_person_spotlight:
            all_candidates.extend(
                PersonSpotlightDetector().detect(
                    assets_by_month, people, generated_keys, self.config, today
                )
            )

        if auto_cfg.detect_activity_burst:
            all_candidates.extend(
                ActivityBurstDetector().detect(
                    assets_by_month,
                    people,
                    generated_keys,
                    self.config,
                    today,
                    burst_threshold=auto_cfg.burst_threshold,
                )
            )

        # TripDetector needs full asset list — skip if trips disabled or no homebase
        if auto_cfg.detect_trips:
            trips_cfg = self.config.trips
            if not (trips_cfg.homebase_latitude == trips_cfg.homebase_longitude == 0.0):
                all_candidates.extend(
                    TripDetector().detect(
                        assets_by_month, people, generated_keys, self.config, today
                    )
                )

        ranked = score_and_rank(all_candidates, generated_keys, today, last_runs)
        return ranked[:limit]

    def run_one(
        self,
        *,
        force: bool = False,
        cooldown_hours: int | None = None,
        upload: bool = False,
        dry_run: bool = False,
    ) -> Path | None:
        """Generate the top-scoring candidate memory.

        Returns the output path on success, None if skipped or no candidates.
        """
        effective_cooldown = cooldown_hours or self.config.automation.cooldown_hours

        if not force and _is_within_cooldown(self.db, effective_cooldown):
            return None

        candidates = self.suggest(limit=1)
        if not candidates:
            logger.info("No candidates found")
            return None

        candidate = candidates[0]
        effective_upload = upload or self.config.automation.upload_to_immich
        cmd = _build_generate_command(candidate, effective_upload)

        if dry_run:
            logger.info("Dry run — would execute: %s", " ".join(cmd))
            return None

        logger.info("Generating: %s (score=%.3f)", candidate.reason, candidate.score)
        logger.info("Running: %s", " ".join(cmd))

        import time as _time

        start = _time.monotonic()
        success = _execute_generate(cmd)
        elapsed = _time.monotonic() - start

        _send_notification(
            config=self.config,
            memory_type=candidate.memory_type,
            success=success,
            duration_seconds=elapsed,
            error=None if success else "Generation subprocess failed",
        )

        if not success:
            return None

        logger.info("Generation completed successfully")
        recent = self.db.list_runs(limit=1, status="completed")
        if recent and recent[0].output_path:
            return Path(recent[0].output_path)

        return None
