"""Read-only automation status: the cooldown gate and the durable status contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from immich_memories.automation.models import AutomationAttempt
from immich_memories.automation.notification_state import NotificationHealth
from immich_memories.tracking.models import RunMetadata

logger = logging.getLogger(__name__)

# A fixed-time scheduler (cron, launchd, in-process timer) fires at the same wall-clock
# time every day; the child process records its start a few seconds later. Without slack,
# "24h since the last run" is never quite true at the next day's fire and every other day
# gets skipped (#330). 30 min still rejects any realistic double fire.
_COOLDOWN_SCHEDULE_TOLERANCE = timedelta(minutes=30)


class CompletedRunReader(Protocol):
    """Small durable read seam required by the cooldown gate."""

    def list_runs(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        source: str | None = None,
        order_by_completion: bool = False,
    ) -> list[RunMetadata]: ...


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
    pending_delivery_count: int
    oldest_pending_delivery: RunMetadata | None
    notification_health: NotificationHealth | None
    notification_cooldown_hours: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize the stable machine-facing automation status contract."""
        attempt = self.last_attempt
        run = self.last_completed_auto_run
        pending = self.oldest_pending_delivery
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
                    "last_phase": attempt.last_phase.value if attempt.last_phase else None,
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
                    "last_phase": run.last_phase.value if run.last_phase else None,
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
            "pending_delivery_count": self.pending_delivery_count,
            "oldest_pending_delivery": (
                {
                    "run_id": pending.run_id,
                    "completed_at": (
                        pending.completed_at.isoformat() if pending.completed_at else None
                    ),
                    "output_path": pending.output_path,
                    "delivery_attempts": pending.delivery_attempts,
                    "delivery_error": pending.delivery_error,
                    "delivery_album": pending.delivery_album,
                }
                if pending
                else None
            ),
            "notification_health": (
                self.notification_health.to_dict(cooldown_hours=self.notification_cooldown_hours)
                if self.notification_health is not None
                else None
            ),
        }


def cooldown_status(
    last_run: RunMetadata | None,
    cooldown_hours: int,
    now: datetime | None = None,
) -> CooldownStatus:
    """Cooldown counts from the last run's *start* so a daily timer means once a day."""
    if last_run is None:
        return CooldownStatus(hours=cooldown_hours, active=False, until=None)
    started_at = last_run.created_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    until = started_at + timedelta(hours=cooldown_hours) - _COOLDOWN_SCHEDULE_TOLERANCE
    return CooldownStatus(hours=cooldown_hours, active=current < until, until=until)


def resolve_cooldown_hours(requested: int | None, configured: int) -> int:
    """Keep explicit CLI cooldown provenance, including a zero override."""
    return configured if requested is None else requested


def is_within_cooldown(db: CompletedRunReader, cooldown_hours: int) -> bool:
    """Check if the most recent completed auto run is within the cooldown window."""
    runs = db.list_runs(
        limit=1,
        status="completed",
        source="auto",
        order_by_completion=True,
    )
    status = cooldown_status(runs[0] if runs else None, cooldown_hours)
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
