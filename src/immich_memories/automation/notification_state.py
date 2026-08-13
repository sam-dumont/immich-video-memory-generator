"""Durable, sanitized notification delivery health."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from immich_memories.cache.database import VideoAnalysisCache


class NotificationFailureCategory(StrEnum):
    """Stable failure classes safe to expose through status endpoints."""

    UNAVAILABLE = "unavailable"
    AUTH = "authentication"
    QUOTA = "quota"
    PROVIDER_REJECTED = "provider_rejected"
    TRANSPORT = "transport"

    @property
    def message(self) -> str:
        """Return a bounded diagnostic that cannot contain provider response data."""
        return {
            self.UNAVAILABLE: "Notification support is not installed",
            self.AUTH: "Notification provider rejected configured credentials",
            self.QUOTA: "Notification provider quota or rate limit reached",
            self.PROVIDER_REJECTED: "Notification provider rejected the delivery",
            self.TRANSPORT: "Notification transport failed",
        }[self]


@dataclass(frozen=True)
class NotificationHealth:
    """The singleton notification health record."""

    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    failure_category: NotificationFailureCategory | None
    failure_message: str | None

    def cooldown_until(self, cooldown_hours: int) -> datetime | None:
        """Return the active failure cooldown boundary, if any."""
        if self.last_failure_at is None:
            return None
        if self.last_success_at is not None and self.last_success_at >= self.last_failure_at:
            return None
        return self.last_failure_at + timedelta(hours=max(0, cooldown_hours))

    def is_cooling_down(self, cooldown_hours: int, *, now: datetime | None = None) -> bool:
        """Return whether a newer failure still suppresses normal delivery attempts."""
        until = self.cooldown_until(cooldown_hours)
        return until is not None and (now or datetime.now(tz=UTC)) < until

    def to_dict(
        self,
        *,
        cooldown_hours: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Serialize only stable, credential-free health fields."""
        until = self.cooldown_until(cooldown_hours)
        return {
            "last_attempt_at": (
                self.last_attempt_at.isoformat() if self.last_attempt_at is not None else None
            ),
            "last_success_at": (
                self.last_success_at.isoformat() if self.last_success_at is not None else None
            ),
            "last_failure_at": (
                self.last_failure_at.isoformat() if self.last_failure_at is not None else None
            ),
            "failure_category": (
                self.failure_category.value if self.failure_category is not None else None
            ),
            "failure_message": self.failure_message,
            "cooldown_active": self.is_cooling_down(cooldown_hours, now=now),
            "cooldown_until": until.isoformat() if until is not None else None,
            "cooldown_hours": cooldown_hours,
        }


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class NotificationStateStore:
    """Read and update notification delivery health in the shared cache database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        VideoAnalysisCache(self.db_path)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get(self) -> NotificationHealth | None:
        """Return the singleton health row, or None before the first attempt."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM notification_health WHERE id = 1").fetchone()
        if row is None:
            return None
        category = row["failure_category"]
        return NotificationHealth(
            last_attempt_at=_parse_timestamp(row["last_attempt_at"]),
            last_success_at=_parse_timestamp(row["last_success_at"]),
            last_failure_at=_parse_timestamp(row["last_failure_at"]),
            failure_category=(NotificationFailureCategory(category) if category else None),
            failure_message=row["failure_message"],
        )

    def is_cooling_down(self, cooldown_hours: int, *, now: datetime | None = None) -> bool:
        """Return whether normal notifications should currently be suppressed."""
        health = self.get()
        return health.is_cooling_down(cooldown_hours, now=now) if health is not None else False

    def record_success(self, *, now: datetime | None = None) -> NotificationHealth:
        """Record successful delivery; retained failure history becomes inactive."""
        attempted_at = now or datetime.now(tz=UTC)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO notification_health (id, last_attempt_at, last_success_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = excluded.last_success_at
                """,
                (attempted_at.isoformat(), attempted_at.isoformat()),
            )
            conn.commit()
        health = self.get()
        assert health is not None
        return health

    def record_failure(
        self,
        category: NotificationFailureCategory,
        *,
        now: datetime | None = None,
    ) -> NotificationHealth:
        """Record one generic failure class without accepting raw provider text."""
        attempted_at = now or datetime.now(tz=UTC)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO notification_health (
                    id, last_attempt_at, last_failure_at, failure_category, failure_message
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_failure_at = excluded.last_failure_at,
                    failure_category = excluded.failure_category,
                    failure_message = excluded.failure_message
                """,
                (
                    attempted_at.isoformat(),
                    attempted_at.isoformat(),
                    category.value,
                    category.message,
                ),
            )
            conn.commit()
        health = self.get()
        assert health is not None
        return health
