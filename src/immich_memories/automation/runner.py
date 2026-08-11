"""AutoRunner — detect, score, and generate memory candidates."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import MemoryCandidate
from immich_memories.automation.models import (
    AutomationAttempt,
    AutoOutcome,
    AutoRejection,
    AutoRunResult,
    ProcessResult,
)
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.automation.variety import VarietyDecision, apply_variety_rules
from immich_memories.config_loader import Config
from immich_memories.config_models import AutomationConfig
from immich_memories.security import configured_secret_values, sanitize_error_message
from immich_memories.timeperiod import DateRange, birthday_year
from immich_memories.tracking.models import RunMetadata
from immich_memories.tracking.run_database import RunDatabase

logger = logging.getLogger(__name__)

_GENERATION_TIMEOUT_SECONDS = 7200
_GENERATION_TIMEOUT_REASON = "generation timed out after 2 hours"
_OUTPUT_TAIL_LENGTH = 2000
_MAX_REPORTED_REJECTIONS = 20


class ImmichDiscoveryError(RuntimeError):
    """A live Immich library snapshot could not be collected."""


class AutomationAlreadyRunningError(RuntimeError):
    """Another process owns the configured automation lease."""


class AutomationLease:
    """Nonblocking OS lease for one config-scoped automation decision."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: Any = None

    def __enter__(self) -> AutomationLease:
        import fcntl

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = self._lock_path.open("w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fd.close()
            self._fd = None
            raise AutomationAlreadyRunningError("automation already running") from None
        return self

    def __exit__(self, *exc: object) -> None:
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None


class SuggestOutcome(StrEnum):
    """Status of the most recent candidate discovery call."""

    READY = "ready"
    PREFLIGHT_FAILED = "preflight_failed"
    DISCOVERY_FAILED = "discovery_failed"


@dataclass(frozen=True)
class SuggestStatus:
    """Typed result for the most recent live candidate-discovery snapshot."""

    outcome: SuggestOutcome = SuggestOutcome.READY
    error: str | None = None


@dataclass(frozen=True)
class _BoundedProcessDetails:
    """Sanitized subprocess output with independent per-stream tail bounds."""

    text: str


@dataclass(frozen=True)
class CooldownStatus:
    """Current cooldown derived from the latest completed automation run."""

    hours: int
    active: bool
    until: datetime | None


@dataclass(frozen=True)
class AutomationStatus:
    """Read-only durable automation facts used by CLI and UI status surfaces."""

    last_attempt: AutomationAttempt | None
    last_completed_auto_run: RunMetadata | None
    cooldown: CooldownStatus
    recent_categories: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    suggestion: SuggestStatus

    def to_dict(self) -> dict[str, Any]:
        """Serialize the stable machine-facing automation status contract."""
        attempt = self.last_attempt
        run = self.last_completed_auto_run
        return {
            "last_attempt": (
                {
                    "id": attempt.id,
                    "started_at": attempt.started_at.isoformat(),
                    "finished_at": (
                        attempt.finished_at.isoformat() if attempt.finished_at else None
                    ),
                    "outcome": attempt.outcome.value,
                    "reason": attempt.reason,
                    "candidate_category": attempt.candidate_category,
                    "memory_type": attempt.memory_type,
                    "memory_key": attempt.memory_key,
                    "run_id": attempt.run_id,
                    "error": attempt.error,
                }
                if attempt
                else None
            ),
            "last_completed_auto_run": (
                {
                    "run_id": run.run_id,
                    "created_at": run.created_at.isoformat(),
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "memory_type": run.memory_type,
                    "memory_key": run.memory_key,
                    "category": run.memory_category,
                    "output_path": run.output_path,
                }
                if run
                else None
            ),
            "cooldown": {
                "hours": self.cooldown.hours,
                "active": self.cooldown.active,
                "until": self.cooldown.until.isoformat() if self.cooldown.until else None,
            },
            "recent_categories": list(self.recent_categories),
            "rejection_reasons": list(self.rejection_reasons),
            "suggestion": {
                "outcome": self.suggestion.outcome.value,
                "error": self.suggestion.error,
            },
        }


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


def _trailing_year_range(today: date) -> DateRange:
    """Return one inclusive calendar-year lookback ending on ``today``."""
    try:
        start_day = today.replace(year=today.year - 1)
    except ValueError:
        # February 29 has no same-day counterpart in a non-leap year.
        start_day = today.replace(year=today.year - 1, day=28)

    return DateRange(
        start=datetime.combine(start_day, datetime.min.time()),
        end=datetime.combine(today, datetime.max.time()),
    )


def _build_last_runs_by_type(db: RunDatabase) -> dict[str, date]:
    """Query DB for the most recent completed run date per memory type."""
    result: dict[str, date] = {}
    for mem_type in (
        "monthly_highlights",
        "year_in_review",
        "person_spotlight",
        "trip",
        "multi_person",
    ):
        run = db.get_last_run_of_type(mem_type, source="auto")
        if run and run.created_at:
            result[mem_type] = (run.completed_at or run.created_at).date()
    return result


def _build_generate_command(
    candidate: MemoryCandidate,
    upload: bool,
    automation_attempt_id: str | None = None,
    config_path: Path | None = None,
) -> list[str]:
    """Build CLI subprocess command from an exhaustively validated candidate."""
    from immich_memories.automation.generation_request import GenerationRequest

    return GenerationRequest.from_candidate(
        candidate,
        upload,
        automation_attempt_id=automation_attempt_id,
        config_path=config_path,
    ).to_argv()


def _cooldown_status(
    last_run: RunMetadata | None,
    cooldown_hours: int,
    now: datetime | None = None,
) -> CooldownStatus:
    """Derive cooldown from completion time, falling back for legacy rows."""
    if last_run is None:
        return CooldownStatus(hours=cooldown_hours, active=False, until=None)
    completed_at = last_run.completed_at or last_run.created_at
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    until = completed_at + timedelta(hours=cooldown_hours)
    return CooldownStatus(hours=cooldown_hours, active=current < until, until=until)


def _is_within_cooldown(db: RunDatabase, cooldown_hours: int) -> bool:
    """Check if the most recent completed auto run is within the cooldown window."""
    runs = db.list_runs(
        limit=1,
        status="completed",
        source="auto",
        order_by_completion=True,
    )
    status = _cooldown_status(runs[0] if runs else None, cooldown_hours)
    if status.active:
        assert status.until is not None
        hours_since = cooldown_hours - (
            (status.until - datetime.now(tz=UTC)).total_seconds() / 3600
        )
        logger.info(
            "Cooldown active: %.1fh since last run (need %dh)",
            hours_since,
            cooldown_hours,
        )
    return status.active


def _coerce_process_output(value: Any) -> str:
    """Normalize subprocess output from normal and timeout results."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _safe_tail(value: Any, secrets: tuple[str, ...] = ()) -> str:
    """Sanitize output before retaining only its bounded tail."""
    safe = sanitize_error_message(_coerce_process_output(value))
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "***")
    return safe[-_OUTPUT_TAIL_LENGTH:]


def _execute_generate(cmd: list[str]) -> ProcessResult:
    """Run one generation subprocess and capture output for config-aware redaction."""
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=_GENERATION_TIMEOUT_SECONDS,
    )
    return ProcessResult(
        returncode=result.returncode,
        stdout=_coerce_process_output(result.stdout),
        stderr=_coerce_process_output(result.stderr),
    )


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


def _compute_upcoming_birthday_ids(people: list, today: date, lookahead_days: int = 7) -> set[str]:
    """Return person IDs whose birthday falls within the next N days."""
    ids: set[str] = set()
    for person in people:
        if not getattr(person, "birth_date", None):
            continue
        bday = person.birth_date
        next_bday = birthday_year(bday, today.year).start.date()
        if next_bday < today:
            next_bday = birthday_year(bday, today.year + 1).start.date()
        days_until = (next_bday - today).days
        if 0 <= days_until <= lookahead_days:
            ids.add(person.id)
    return ids


def _run_all_detectors(
    auto_cfg: AutomationConfig,
    assets_by_month: dict[str, int],
    people: list,
    generated_keys: set[str],
    config: Config,
    today: date,
    person_asset_counts: dict[str, int],
    gps_assets: list | None,
) -> list[MemoryCandidate]:
    """Run all enabled detectors and collect candidates."""
    from immich_memories.automation.calendar_detectors import (
        BirthdayDetector,
        MonthlyDetector,
        OnThisDayDetector,
        PersonSpotlightDetector,
        YearlyDetector,
    )
    from immich_memories.automation.event_detectors import (
        ActivityBurstDetector,
        MultiPersonDetector,
        TripDetector,
    )

    all_candidates: list[MemoryCandidate] = []

    if auto_cfg.detect_monthly:
        all_candidates.extend(
            MonthlyDetector().detect(assets_by_month, people, generated_keys, config, today)
        )
    if auto_cfg.detect_yearly:
        all_candidates.extend(
            YearlyDetector().detect(assets_by_month, people, generated_keys, config, today)
        )
    if auto_cfg.detect_person_spotlight:
        # WHY: suppress spotlights for people whose birthday is within 7 days
        # so BirthdayDetector fires at the right time instead
        upcoming_birthday_ids = _compute_upcoming_birthday_ids(people, today)
        all_candidates.extend(
            PersonSpotlightDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                person_asset_counts=person_asset_counts,
                upcoming_birthday_ids=upcoming_birthday_ids,
            )
        )
        all_candidates.extend(
            MultiPersonDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                person_asset_counts=person_asset_counts,
            )
        )
    if auto_cfg.detect_activity_burst:
        all_candidates.extend(
            ActivityBurstDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                burst_threshold=auto_cfg.burst_threshold,
            )
        )

    all_candidates.extend(
        OnThisDayDetector().detect(assets_by_month, people, generated_keys, config, today)
    )

    # Birthday detector — always on, high priority near birthdays
    if people:
        all_candidates.extend(
            BirthdayDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                person_asset_counts=person_asset_counts,
            )
        )

    if auto_cfg.detect_trips and gps_assets is not None:
        all_candidates.extend(
            TripDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                assets=gps_assets,
            )
        )

    return all_candidates


class AutoRunner:
    """Orchestrates candidate detection and one-shot generation."""

    def __init__(
        self,
        config: Config,
        execute: Callable[[list[str]], ProcessResult] | None = None,
        config_path: Path | None = None,
    ):
        self.config = config
        self.db = RunDatabase(db_path=config.cache.database_path)
        self.state = AutomationStateStore(config.cache.database_path)
        self.execute = execute or _execute_generate
        self.config_path = config_path
        self.last_variety_decision = VarietyDecision(eligible=[], rejected=[])
        self.last_recent_categories: tuple[str, ...] = ()
        self.last_suggest_status = SuggestStatus()

    def _secrets(self) -> tuple[str, ...]:
        """Return configured credential values that must never enter attempt history."""
        return configured_secret_values(self.config)

    def status(
        self,
        cooldown_hours: int | None = None,
        *,
        refresh_suggestion: bool = False,
    ) -> AutomationStatus:
        """Return durable automation state without candidate or scheduler side effects."""
        if refresh_suggestion:
            try:
                self.suggest(limit=1)
            except ImmichDiscoveryError as exc:
                # Status is a diagnostic surface: a live Immich discovery failure must
                # not hide the durable attempt/run history that explains automation.
                error = _safe_tail(exc, self._secrets()) or "candidate discovery failed"
                self.last_variety_decision = VarietyDecision(eligible=[], rejected=[])
                self.last_suggest_status = SuggestStatus(
                    outcome=SuggestOutcome.DISCOVERY_FAILED,
                    error=error,
                )
                logger.error("Candidate discovery failed while refreshing status: %s", error)
        effective_cooldown = (
            cooldown_hours if cooldown_hours is not None else self.config.automation.cooldown_hours
        )
        recent_runs = self.db.list_runs(
            limit=6,
            status="completed",
            source="auto",
            order_by_completion=True,
        )
        rejection_reasons = tuple(
            dict.fromkeys(item.rule for item in self.last_variety_decision.rejected)
        )
        return AutomationStatus(
            last_attempt=self.state.get_last_attempt(),
            last_completed_auto_run=recent_runs[0] if recent_runs else None,
            cooldown=_cooldown_status(
                recent_runs[0] if recent_runs else None,
                effective_cooldown,
            ),
            recent_categories=tuple(
                run.memory_category for run in recent_runs if run.memory_category is not None
            ),
            rejection_reasons=rejection_reasons,
            suggestion=self.last_suggest_status,
        )

    def _process_details(self, stdout: Any, stderr: Any) -> _BoundedProcessDetails:
        """Format independently bounded stdout and stderr tails for persistence."""
        secrets = self._secrets()
        stdout_tail = _safe_tail(stdout, secrets)
        stderr_tail = _safe_tail(stderr, secrets)
        details: list[str] = []
        if stdout_tail:
            details.append(f"stdout:\n{stdout_tail}")
        if stderr_tail:
            details.append(f"stderr:\n{stderr_tail}")
        return _BoundedProcessDetails("\n".join(details) or "no subprocess output")

    def _finish(
        self,
        attempt: AutomationAttempt,
        outcome: AutoOutcome,
        reason: str,
        *,
        candidate: MemoryCandidate | None = None,
        run_id: str | None = None,
        output_path: Path | None = None,
        error: str | None = None,
    ) -> AutoRunResult:
        """Persist one terminal transition and return the same public result."""
        candidate_category = None
        if candidate is not None:
            candidate_category = getattr(candidate.category, "value", str(candidate.category))
        self.state.finish_attempt(
            attempt.id,
            outcome,
            reason,
            candidate_category=candidate_category,
            memory_type=candidate.memory_type if candidate else None,
            memory_key=candidate.memory_key if candidate else None,
            run_id=run_id,
            error=error,
        )
        return AutoRunResult(
            outcome=outcome,
            reason=reason,
            candidate=candidate,
            run_id=run_id,
            output_path=output_path,
            error=error,
            recent_categories=self.last_recent_categories,
            rejections=tuple(
                AutoRejection(
                    category=item.candidate.category.value,
                    memory_key=item.candidate.memory_key,
                    rule=item.rule,
                )
                for item in self.last_variety_decision.rejected[:_MAX_REPORTED_REJECTIONS]
            ),
        )

    def _notify_generation_failure(self, candidate: MemoryCandidate, error: str) -> None:
        """Keep the parent responsible only when the child did not complete."""
        _send_notification(
            config=self.config,
            memory_type=candidate.memory_type,
            success=False,
            error=error,
        )

    def _fail_candidate(
        self,
        attempt: AutomationAttempt,
        reason: str,
        *,
        candidate: MemoryCandidate | None,
        error: object | _BoundedProcessDetails | None = None,
    ) -> AutoRunResult:
        """Persist one safe parent failure, then notify exactly once best-effort."""
        if isinstance(error, _BoundedProcessDetails):
            safe_error = error.text
        else:
            safe_error = _safe_tail(error if error is not None else reason, self._secrets())
        safe_error = safe_error or reason
        result = self._finish(
            attempt,
            AutoOutcome.FAILED,
            reason,
            candidate=candidate,
            error=safe_error,
        )
        if candidate is not None:
            try:
                self._notify_generation_failure(candidate, safe_error)
            except Exception as exc:
                logger.warning(
                    "Failure notification could not be delivered: %s",
                    _safe_tail(exc, self._secrets()) or exc.__class__.__name__,
                )
        return result

    def _suggest_one_for_attempt(
        self, attempt: AutomationAttempt
    ) -> tuple[MemoryCandidate | None, AutoRunResult | None]:
        """Return one candidate or the terminal result of candidate discovery."""
        candidates = self.suggest(limit=1)
        if self.last_suggest_status.outcome is SuggestOutcome.PREFLIGHT_FAILED:
            reason = "Immich preflight failed"
            result = self._finish(
                attempt,
                AutoOutcome.FAILED,
                reason,
                error=self.last_suggest_status.error or reason,
            )
            return None, result
        if not candidates:
            logger.info("No eligible candidates found")
            result = self._finish(attempt, AutoOutcome.SKIPPED, "no eligible candidates")
            return None, result
        return candidates[0], None

    def suggest(self, limit: int = 10) -> list[MemoryCandidate]:
        """Detect, score, and rank memory candidates from the Immich library."""
        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.preflight import CheckStatus, check_immich

        self.last_variety_decision = VarietyDecision(eligible=[], rejected=[])
        self.last_recent_categories = ()
        self.last_suggest_status = SuggestStatus()
        immich_result = check_immich(self.config)
        if immich_result.status == CheckStatus.ERROR:
            error = f"Immich preflight failed: {immich_result.message}"
            if immich_result.details:
                error = f"{error}: {immich_result.details}"
            error = _safe_tail(error, self._secrets())
            self.last_suggest_status = SuggestStatus(
                outcome=SuggestOutcome.PREFLIGHT_FAILED,
                error=error,
            )
            logger.error("%s", error)
            return []

        auto_cfg = self.config.automation
        generated_keys = self.db.get_generated_memory_keys()
        last_runs = _build_last_runs_by_type(self.db)
        recent_auto_runs = self.db.list_runs(
            limit=6,
            status="completed",
            source="auto",
            order_by_completion=True,
        )
        self.last_recent_categories = tuple(
            run.memory_category for run in recent_auto_runs if run.memory_category is not None
        )
        today = date.today()

        try:
            with SyncImmichClient(
                base_url=self.config.immich.url,
                api_key=self.config.immich.api_key,
            ) as client:
                buckets = client.get_time_buckets()
                people = client.get_all_people() if auto_cfg.detect_person_spotlight else []

                # Fetch per-person asset counts (top 10 named people only)
                person_asset_counts: dict[str, int] = {}
                if auto_cfg.detect_person_spotlight and people:
                    named = [p for p in people if p.name and p.thumbnail_path][:10]
                    for p in named:
                        person_asset_counts[p.id] = client.get_person_asset_count(p.id)

                # Fetch GPS assets for trip detection (past year only)
                gps_assets = None
                if auto_cfg.detect_trips:
                    trips_cfg = self.config.trips
                    if not (trips_cfg.homebase_latitude == trips_cfg.homebase_longitude == 0.0):
                        from immich_memories.api.all_assets_service import AllAssetsService

                        dr = _trailing_year_range(today)
                        asset_service = AllAssetsService(client._async_client.search)
                        gps_assets = client._run(asset_service.get_assets_for_date_range(dr))
                        logger.info("Fetched %d assets for trip detection", len(gps_assets))
        except Exception as exc:
            raise ImmichDiscoveryError(str(exc)) from exc

        assets_by_month = _time_buckets_to_month_counts(buckets)

        all_candidates = _run_all_detectors(
            auto_cfg,
            assets_by_month,
            people,
            generated_keys,
            self.config,
            today,
            person_asset_counts,
            gps_assets,
        )

        self.last_variety_decision = apply_variety_rules(
            all_candidates,
            recent_auto_runs,
            today,
        )
        ranked = score_and_rank(
            self.last_variety_decision.eligible,
            generated_keys,
            today,
            last_runs,
        )
        return ranked[:limit]

    def run_one(
        self,
        *,
        force: bool = False,
        cooldown_hours: int | None = None,
        upload: bool = False,
        dry_run: bool = False,
    ) -> AutoRunResult:
        """Run one durable automation decision and return its exact outcome."""
        lease_path = self.config.cache.database_path.parent / ".auto.lock"
        try:
            with AutomationLease(lease_path):
                attempt = self.state.start_attempt(reason="daily wake")
                return self._run_one_under_lease(
                    attempt,
                    force=force,
                    cooldown_hours=cooldown_hours,
                    upload=upload,
                    dry_run=dry_run,
                )
        except AutomationAlreadyRunningError:
            return AutoRunResult(
                outcome=AutoOutcome.SKIPPED,
                reason="automation already running",
            )

    def _run_one_under_lease(
        self,
        attempt: AutomationAttempt,
        *,
        force: bool,
        cooldown_hours: int | None,
        upload: bool,
        dry_run: bool,
    ) -> AutoRunResult:
        """Execute and persist one automation decision while its lease is held."""
        candidate: MemoryCandidate | None = None

        try:
            effective_cooldown = (
                cooldown_hours
                if cooldown_hours is not None
                else self.config.automation.cooldown_hours
            )
            if not force and _is_within_cooldown(self.db, effective_cooldown):
                return self._finish(attempt, AutoOutcome.SKIPPED, "cooldown active")

            candidate, candidate_result = self._suggest_one_for_attempt(attempt)
            if candidate_result is not None:
                return candidate_result
            assert candidate is not None
            effective_upload = upload or self.config.automation.upload_to_immich
            cmd = _build_generate_command(
                candidate,
                effective_upload,
                automation_attempt_id=attempt.id,
                config_path=self.config_path,
            )

            if dry_run:
                logger.info("Dry run — would execute: %s", " ".join(cmd))
                return self._finish(
                    attempt,
                    AutoOutcome.DRY_RUN,
                    "dry run",
                    candidate=candidate,
                )

            logger.info("Generating: %s (score=%.3f)", candidate.reason, candidate.score)
            logger.info("Running: %s", " ".join(cmd))
            try:
                process = self.execute(cmd)
            except subprocess.TimeoutExpired as exc:
                details = self._process_details(exc.stdout, exc.stderr)
                timeout_error = _BoundedProcessDetails(
                    f"{_GENERATION_TIMEOUT_REASON}\n{details.text}"
                )
                logger.error(_GENERATION_TIMEOUT_REASON)
                return self._fail_candidate(
                    attempt,
                    _GENERATION_TIMEOUT_REASON,
                    candidate=candidate,
                    error=timeout_error,
                )
            except Exception as exc:
                launch_error = _safe_tail(exc, self._secrets()) or "generation process failed"
                reason = "generation process could not be executed"
                logger.error("%s: %s", reason, launch_error)
                return self._fail_candidate(
                    attempt,
                    reason,
                    candidate=candidate,
                    error=launch_error,
                )

            if process.returncode != 0:
                reason = f"generation subprocess exited with code {process.returncode}"
                process_error = self._process_details(process.stdout, process.stderr)
                logger.error("%s: %s", reason, process_error.text)
                return self._fail_candidate(
                    attempt,
                    reason,
                    candidate=candidate,
                    error=process_error,
                )

            matching_run = self.db.get_completed_run_by_automation_attempt(
                attempt.id,
                memory_key=candidate.memory_key,
            )
            if matching_run is None:
                reason = "no matching completed auto run"
                return self._fail_candidate(
                    attempt,
                    reason,
                    candidate=candidate,
                    error=reason,
                )
            if not matching_run.output_path:
                reason = "matching run has no output path"
                return self._fail_candidate(
                    attempt,
                    reason,
                    candidate=candidate,
                    error=reason,
                )

            output_path = Path(matching_run.output_path)
            if not output_path.is_file():
                reason = "generated output file is missing"
                return self._fail_candidate(
                    attempt,
                    reason,
                    candidate=candidate,
                    error=reason,
                )

            logger.info("Generation completed successfully: %s", output_path)
            return self._finish(
                attempt,
                AutoOutcome.COMPLETED,
                "generation completed",
                candidate=candidate,
                run_id=matching_run.run_id,
                output_path=output_path,
            )
        except Exception as exc:
            reason = "automation failed"
            outer_error = _safe_tail(exc, self._secrets()) or exc.__class__.__name__
            logger.error("%s: %s", reason, outer_error)
            return self._fail_candidate(
                attempt,
                reason,
                candidate=candidate,
                error=outer_error,
            )
