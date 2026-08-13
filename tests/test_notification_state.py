"""Durable notification health and cooldown contracts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.automation.notification_state import (
    NotificationFailureCategory,
    NotificationStateStore,
)
from immich_memories.automation.notifications import notify_job_complete
from immich_memories.cache import database as cache_database
from immich_memories.cache.database import VideoAnalysisCache


def test_v15_adds_singleton_notification_health_to_existing_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "v14.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 14)
    VideoAnalysisCache(db_path)

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 15)
    VideoAnalysisCache(db_path)
    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(notification_health)").fetchall()
        }
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert columns == {
        "id",
        "last_attempt_at",
        "last_success_at",
        "last_failure_at",
        "failure_category",
        "failure_message",
    }
    assert version == 15


def test_failed_delivery_opens_cooldown_and_success_closes_it(tmp_path: Path) -> None:
    state = NotificationStateStore(tmp_path / "health.db")
    failed_at = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)

    state.record_failure(NotificationFailureCategory.QUOTA, now=failed_at)

    health = state.get()
    assert health is not None
    assert health.failure_category is NotificationFailureCategory.QUOTA
    assert health.failure_message == "Notification provider quota or rate limit reached"
    assert state.is_cooling_down(24, now=failed_at + timedelta(hours=23)) is True
    assert state.is_cooling_down(24, now=failed_at + timedelta(hours=24)) is False

    state.record_success(now=failed_at + timedelta(hours=1))

    health = state.get()
    assert health is not None
    assert health.last_failure_at == failed_at
    assert health.last_success_at == failed_at + timedelta(hours=1)
    assert state.is_cooling_down(24, now=failed_at + timedelta(hours=2)) is False


def test_failure_state_never_persists_provider_exception_text(tmp_path: Path) -> None:
    db_path = tmp_path / "health.db"
    credential_url = "https://user:notification-secret@example.test/raw-body"
    apprise = MagicMock()
    apprise.Apprise.return_value.notify.side_effect = RuntimeError(
        f"429 quota response from {credential_url}"
    )

    with patch.dict("sys.modules", {"apprise": apprise}):
        result = notify_job_complete(
            memory_type="trip",
            status="completed",
            urls=[credential_url],
            db_path=db_path,
        )

    assert result is False
    health = NotificationStateStore(db_path).get()
    assert health is not None
    assert health.failure_category is NotificationFailureCategory.QUOTA
    serialized = str(health.to_dict(cooldown_hours=24))
    assert credential_url not in serialized
    assert "raw-body" not in serialized


def test_cooldown_suppresses_normal_delivery_but_test_bypasses(tmp_path: Path) -> None:
    db_path = tmp_path / "health.db"
    state = NotificationStateStore(db_path)
    state.record_failure(NotificationFailureCategory.TRANSPORT)
    apprise = MagicMock()
    apprise.Apprise.return_value.notify.return_value = True

    with patch.dict("sys.modules", {"apprise": apprise}):
        suppressed = notify_job_complete(
            memory_type="monthly",
            status="completed",
            urls=["ntfy://topic"],
            db_path=db_path,
        )
        test_result = notify_job_complete(
            memory_type="test",
            status="completed",
            urls=["ntfy://topic"],
            db_path=db_path,
            bypass_cooldown=True,
        )

    assert suppressed is False
    assert test_result is True
    apprise.Apprise.return_value.notify.assert_called_once()
    assert state.is_cooling_down(24) is False


def test_thumbnail_attachment_is_opt_in(tmp_path: Path) -> None:
    apprise = MagicMock()
    apprise.Apprise.return_value.notify.return_value = True
    video = tmp_path / "memory.mp4"
    video.write_bytes(b"video")

    with (
        patch.dict("sys.modules", {"apprise": apprise}),
        patch(
            "immich_memories.automation.notifications._extract_thumbnail",
            return_value=str(tmp_path / "thumb.jpg"),
        ) as extract,
        patch("immich_memories.automation.notifications._cleanup_thumbnail"),
    ):
        notify_job_complete(
            memory_type="trip",
            status="completed",
            output_path=str(video),
            urls=["ntfy://topic"],
        )
        notify_job_complete(
            memory_type="trip",
            status="completed",
            output_path=str(video),
            urls=["ntfy://topic"],
            attach_thumbnail=True,
        )

    assert extract.call_count == 1
    first, second = apprise.Apprise.return_value.notify.call_args_list
    assert "attach" not in first.kwargs
    assert second.kwargs["attach"].endswith("thumb.jpg")
