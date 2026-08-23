"""AutoRunner — decide, execute, and record one automation attempt."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.automation.candidate_discovery import (
    CandidateDiscovery,
    ImmichDiscoveryError,
)
from immich_memories.automation.candidates import MemoryCandidate
from immich_memories.automation.delivery_retry import PendingDeliveryRetry, abandon_if_exhausted
from immich_memories.automation.models import (
    AutoAction,
    AutomationAttempt,
    AutoOutcome,
    AutoRejection,
    AutoRunResult,
    ProcessResult,
)
from immich_memories.automation.notification_state import NotificationStateStore
from immich_memories.automation.notifications import (
    send_configured_notification as _send_notification,
)
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.automation.status import (
    AutomationStatus,
    SuggestOutcome,
    SuggestStatus,
    cooldown_status,
    is_within_cooldown,
    resolve_cooldown_hours,
)
from immich_memories.automation.variety import VarietyDecision
from immich_memories.config_loader import Config
from immich_memories.security import configured_secret_values, sanitize_error_message
from immich_memories.tracking.models import RunMetadata
from immich_memories.tracking.run_database import RunDatabase

logger = logging.getLogger(__name__)

_GENERATION_TIMEOUT_SECONDS = 7200
_GENERATION_TIMEOUT_REASON = "generation timed out after 2 hours"
_OUTPUT_TAIL_LENGTH = 2000


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


@dataclass(frozen=True)
class _BoundedProcessDetails:
    """Sanitized subprocess output with independent per-stream tail bounds."""

    text: str


def _build_generate_command(
    candidate: MemoryCandidate,
    upload: bool,
    automation_attempt_id: str | None = None,
    config_path: Path | None = None,
    album_name: str | None = None,
) -> list[str]:
    """Build CLI subprocess command from an exhaustively validated candidate."""
    from immich_memories.automation.generation_request import GenerationRequest

    return GenerationRequest.from_candidate(
        candidate,
        upload,
        automation_attempt_id=automation_attempt_id,
        config_path=config_path,
        album_name=album_name,
    ).to_argv()


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
        self.notification_state = NotificationStateStore(config.cache.database_path)
        self.execute = execute or _execute_generate
        self.config_path = config_path
        self.last_variety_decision = VarietyDecision(eligible=[], rejected=[])
        self.last_recent_categories: tuple[str, ...] = ()
        self.last_suggest_status = SuggestStatus()
        self._discovery = CandidateDiscovery(config, self.db, self.state)
        self._prepared_immich_preflight: Any | None = None
        self._prepared_pending_delivery: RunMetadata | None = None

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
        recent_runs = self._recent_auto_runs()
        rejection_reasons = tuple(
            dict.fromkeys(item.rule for item in self.last_variety_decision.rejected)
        )
        return AutomationStatus(
            last_attempt=self.state.get_last_attempt(),
            last_completed_auto_run=recent_runs[0] if recent_runs else None,
            cooldown=cooldown_status(
                recent_runs[0] if recent_runs else None,
                effective_cooldown,
            ),
            recent_categories=tuple(
                run.memory_category for run in recent_runs if run.memory_category is not None
            ),
            rejection_reasons=rejection_reasons,
            suggestion=self.last_suggest_status,
            pending_delivery_count=self.db.count_pending_deliveries(source="auto"),
            oldest_pending_delivery=self.db.get_oldest_pending_delivery(source="auto"),
            notification_health=self.notification_state.get(),
            notification_cooldown_hours=self.config.notifications.cooldown_hours,
        )

    def _recent_auto_runs(self) -> list[RunMetadata]:
        """The completed auto history that both status and variety rules read."""
        return self.db.list_runs(
            limit=6,
            status="completed",
            source="auto",
            order_by_completion=True,
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
        action: AutoAction = AutoAction.GENERATION,
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
            action=action,
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
                for item in self.last_variety_decision.rejected
            ),
        )

    def _preflight_error(self, result: Any) -> str | None:
        """Turn one Immich preflight result into a sanitized automation diagnostic."""
        from immich_memories.preflight import CheckStatus

        if result.status is not CheckStatus.ERROR:
            return None
        error = f"Immich preflight failed: {result.message}"
        if result.details:
            error = f"{error}: {result.details}"
        error = _safe_tail(error, self._secrets()) or "Immich preflight failed"
        self.last_suggest_status = SuggestStatus(
            outcome=SuggestOutcome.PREFLIGHT_FAILED,
            error=error,
        )
        logger.error("%s", error)
        return error

    def _check_immich_preflight(self) -> Any:
        """Run the one connection check shared by a retry and later discovery."""
        from immich_memories.preflight import check_immich

        return check_immich(self.config)

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

    def retry_pending_delivery(
        self,
        attempt: AutomationAttempt,
        *,
        dry_run: bool,
    ) -> AutoRunResult | None:
        """Retry the oldest deliverable auto artifact, if one exists."""
        return self._pending_delivery_retry().run(
            attempt,
            dry_run=dry_run,
            pending=self._prepared_pending_delivery,
        )

    def _pending_delivery_retry(self) -> PendingDeliveryRetry:
        """Bind this runner's durable collaborators to one retry state machine."""
        return PendingDeliveryRetry(
            self.config,
            self.db,
            self.state,
            lambda value: _safe_tail(value, self._secrets()),
            self.last_recent_categories,
        )

    def _prepare_pending_delivery_retry(
        self,
        attempt: AutomationAttempt,
    ) -> AutoRunResult | None:
        """Run retry preflight only after selecting an executable artifact."""
        pending = self.db.get_oldest_pending_delivery(source="auto")
        if pending is None or pending.output_path is None:
            return None
        if abandon_if_exhausted(pending, config=self.config, db=self.db):
            return None
        retry = self._pending_delivery_retry()
        retry.start_delivery(attempt)
        preflight = self._check_immich_preflight()
        preflight_error = self._preflight_error(preflight)
        if preflight_error is None:
            self._prepared_immich_preflight = preflight
            self._prepared_pending_delivery = pending
            return None
        return retry.finish(
            attempt,
            AutoOutcome.FAILED,
            "Immich preflight failed",
            run_id=pending.run_id,
            output_path=Path(pending.output_path),
            error=preflight_error,
        )

    def suggest(self, limit: int = 10) -> list[MemoryCandidate]:
        """Detect, score, and rank memory candidates from the Immich library."""
        self.last_variety_decision = VarietyDecision(eligible=[], rejected=[])
        self.last_backoff_skips: dict[str, str] = {}
        self.last_recent_categories = ()
        self.last_suggest_status = SuggestStatus()
        immich_result = self._prepared_immich_preflight
        self._prepared_immich_preflight = None
        if immich_result is None:
            immich_result = self._check_immich_preflight()
        if self._preflight_error(immich_result) is not None:
            return []

        recent_auto_runs = self._recent_auto_runs()
        self.last_recent_categories = tuple(
            run.memory_category for run in recent_auto_runs if run.memory_category is not None
        )
        discovered = self._discovery.discover(limit=limit, recent_auto_runs=recent_auto_runs)
        self.last_variety_decision = discovered.variety_decision
        self.last_backoff_skips = discovered.backoff_skips
        return discovered.candidates

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
            self.state.record_discovery(attempt.id)
            preflight_result = self._prepare_pending_delivery_retry(attempt)
            if preflight_result is not None:
                return preflight_result

            retry_result = self.retry_pending_delivery(attempt, dry_run=dry_run)
            if retry_result is not None:
                return retry_result

            effective_cooldown = resolve_cooldown_hours(
                cooldown_hours,
                self.config.automation.cooldown_hours,
            )
            if not force and is_within_cooldown(self.db, effective_cooldown):
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
                album_name=self.config.automation.album_name,
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
        finally:
            self._prepared_immich_preflight = None
            self._prepared_pending_delivery = None
