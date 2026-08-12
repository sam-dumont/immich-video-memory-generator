"""Bounded durable-state handling for one pending auto-delivery retry."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from immich_memories.automation.models import (
    AutoAction,
    AutomationAttempt,
    AutoOutcome,
    AutoRunResult,
)
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.config_loader import Config
from immich_memories.operations.phases import OperationalPhase, PhaseEvent
from immich_memories.tracking.models import DeliveryStatus, RunMetadata
from immich_memories.tracking.run_database import RunDatabase

logger = logging.getLogger(__name__)

_PERSISTENCE_ATTEMPTS = 2


class PendingDeliveryRetry:
    """Retry one queued artifact while never repeating its external upload for a DB retry."""

    def __init__(
        self,
        config: Config,
        db: RunDatabase,
        state: AutomationStateStore,
        safe_error: Callable[[Any], str],
        recent_categories: tuple[str, ...],
    ) -> None:
        self._config = config
        self._db = db
        self._state = state
        self._safe_error = safe_error
        self._recent_categories = recent_categories

    def _persist(
        self,
        operation: Callable[[], object],
        *,
        description: str,
        already_persisted: Callable[[], bool] | None = None,
        retry_requires_readable_noncommit: bool = False,
    ) -> str | None:
        """Retry a durable write only when any required non-commit proof is readable."""
        first_error: str | None = None
        for _ in range(_PERSISTENCE_ATTEMPTS):
            try:
                operation()
                return None
            except Exception as exc:
                safe_error = self._safe_error(exc) or exc.__class__.__name__
                first_error = first_error or safe_error
                persisted = self._was_persisted(already_persisted, description)
                if persisted is True:
                    return None
                if retry_requires_readable_noncommit and persisted is None:
                    return first_error
        assert first_error is not None
        logger.error(
            "%s failed after %d attempts: %s",
            description,
            _PERSISTENCE_ATTEMPTS,
            first_error,
        )
        return first_error

    def _was_persisted(
        self,
        probe: Callable[[], bool] | None,
        description: str,
    ) -> bool | None:
        """Return committed, not committed, or unknown after an ambiguous write."""
        if probe is None:
            return None
        try:
            return probe()
        except Exception as exc:
            logger.warning(
                "%s state probe failed: %s",
                description,
                self._safe_error(exc) or exc.__class__.__name__,
            )
            return None

    def _attempt_is_finished(
        self,
        attempt: AutomationAttempt,
        outcome: AutoOutcome,
        run_id: str,
    ) -> bool:
        saved = self._state.get_last_attempt()
        return (
            saved is not None
            and saved.id == attempt.id
            and saved.outcome is outcome
            and saved.run_id == run_id
        )

    def _delivery_is_recorded(self, run_id: str, asset_id: str) -> bool:
        saved = self._db.get_run(run_id)
        return (
            saved is not None
            and saved.delivery_status is DeliveryStatus.DELIVERED
            and saved.immich_asset_id == asset_id
        )

    def _pending_is_recorded(self, run_id: str, error: str, expected_attempts: int) -> bool:
        saved = self._db.get_run(run_id)
        return (
            saved is not None
            and saved.delivery_status is DeliveryStatus.PENDING
            and saved.delivery_error == error
            and saved.delivery_attempts == expected_attempts
        )

    def _record_phase(
        self,
        attempt: AutomationAttempt,
        phase: OperationalPhase,
        message: str,
    ) -> None:
        """Persist retry status only after an executable artifact is selected."""
        try:
            self._state.update_phase(attempt.id, PhaseEvent(phase, 0, 0, message, 0.0))
        except Exception:
            logger.warning("Could not persist pending delivery phase %s", phase.value)

    def finish(
        self,
        attempt: AutomationAttempt,
        outcome: AutoOutcome,
        reason: str,
        *,
        run_id: str,
        output_path: Path,
        error: str | None = None,
    ) -> AutoRunResult:
        """Keep attempt-persistence faults within the retry result contract."""
        finish_error = self._persist(
            lambda: self._state.finish_attempt(
                attempt.id,
                outcome,
                reason,
                run_id=run_id,
                error=error,
            ),
            description="Retry attempt persistence",
            already_persisted=lambda: self._attempt_is_finished(attempt, outcome, run_id),
        )
        if finish_error is not None:
            error = "; ".join(
                part
                for part in (error, f"retry attempt persistence failed: {finish_error}")
                if part
            )
            outcome = AutoOutcome.FAILED
            reason = "pending delivery persistence failed"
        if outcome is AutoOutcome.COMPLETED:
            self._record_phase(attempt, OperationalPhase.COMPLETE, "Pending delivery complete")
        return AutoRunResult(
            outcome=outcome,
            reason=reason,
            action=AutoAction.DELIVERY_RETRY,
            run_id=run_id,
            output_path=output_path,
            error=error,
            recent_categories=self._recent_categories,
        )

    def run(
        self, attempt: AutomationAttempt, pending: RunMetadata, *, dry_run: bool
    ) -> AutoRunResult:
        """Retry the executable artifact selected by the caller's one preflight."""
        if pending.output_path is None:  # guarded by the database query
            raise RuntimeError(f"Pending delivery run has no output path: {pending.run_id}")

        output_path = Path(pending.output_path)
        self._record_phase(attempt, OperationalPhase.DELIVERY, "Retrying pending delivery")
        if dry_run:
            logger.info("Dry run — would retry pending delivery: %s", output_path)
            return self.finish(
                attempt,
                AutoOutcome.DRY_RUN,
                "pending delivery dry run",
                run_id=pending.run_id,
                output_path=output_path,
            )

        from immich_memories.api.immich import SyncImmichClient

        try:
            with SyncImmichClient(
                base_url=self._config.immich.url,
                api_key=self._config.immich.api_key,
                api_version=self._config.immich.api_version,
            ) as client:
                upload = client.upload_memory(
                    video_path=output_path,
                    album_name=pending.delivery_album,
                )
            asset_id = upload.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise ValueError("Immich upload returned no asset ID")
            asset_id = asset_id.strip()
        except Exception as exc:
            delivery_error = self._safe_error(exc) or "Immich delivery failed"
            logger.warning("Pending Immich delivery failed: %s", delivery_error)
            persistence_error = self._persist(
                lambda: self._db.mark_delivery_pending(pending.run_id, delivery_error),
                description="Pending delivery persistence",
                already_persisted=lambda: self._pending_is_recorded(
                    pending.run_id,
                    delivery_error,
                    pending.delivery_attempts + 1,
                ),
                retry_requires_readable_noncommit=True,
            )
            if persistence_error is not None:
                return self.finish(
                    attempt,
                    AutoOutcome.FAILED,
                    "pending delivery persistence failed",
                    run_id=pending.run_id,
                    output_path=output_path,
                    error=(
                        f"pending delivery persistence failed: {persistence_error}; "
                        f"delivery failure: {delivery_error}"
                    ),
                )
            return self.finish(
                attempt,
                AutoOutcome.FAILED,
                "pending delivery failed",
                run_id=pending.run_id,
                output_path=output_path,
                error=delivery_error,
            )

        persistence_error = self._persist(
            lambda: self._db.mark_delivered(pending.run_id, asset_id),
            description="Delivered asset persistence",
            already_persisted=lambda: self._delivery_is_recorded(pending.run_id, asset_id),
        )
        if persistence_error is not None:
            return self.finish(
                attempt,
                AutoOutcome.FAILED,
                "pending delivery persistence failed",
                run_id=pending.run_id,
                output_path=output_path,
                error=f"delivered asset persistence failed: {persistence_error}",
            )
        return self.finish(
            attempt,
            AutoOutcome.COMPLETED,
            "pending delivery completed",
            run_id=pending.run_id,
            output_path=output_path,
        )
