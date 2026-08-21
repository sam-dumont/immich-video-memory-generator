"""A delivery that cannot succeed must stop consuming every nightly run.

`_run_one_under_lease` retries a pending delivery and returns -- so the wake ends
there, before candidate selection. `delivery_attempts` was incremented on each
failure and never compared to anything, so a deterministic upload failure (an
API key without upload scope, an album ACL) meant one failed upload per night
and no new memories, indefinitely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.automation.delivery_retry import abandon_if_exhausted
from immich_memories.tracking.models import DeliveryStatus
from immich_memories.tracking.run_database import RunDatabase


@pytest.fixture
def db(tmp_path: Path) -> RunDatabase:
    return RunDatabase(tmp_path / "runs.db")


def _completed_run(db: RunDatabase, tmp_path: Path, run_id: str) -> None:
    from datetime import UTC, datetime

    from immich_memories.tracking.models import RunMetadata

    output = tmp_path / f"{run_id}.mp4"
    output.write_bytes(b"video")
    db.save_run(
        RunMetadata(
            run_id=run_id,
            created_at=datetime.now(tz=UTC),
            status="completed",
            source="auto",
            output_path=str(output),
            delivery_status=DeliveryStatus.PENDING,
        )
    )


class TestAbandoningAStuckDelivery:
    def test_an_abandoned_delivery_leaves_the_pending_queue(self, db: RunDatabase, tmp_path: Path):
        """While it is pending it blocks every wake, so abandoning must clear it."""
        _completed_run(db, tmp_path, "run-1")
        assert db.get_oldest_pending_delivery(source="auto") is not None

        db.mark_delivery_abandoned("run-1", "gave up after 5 attempts")

        assert db.get_oldest_pending_delivery(source="auto") is None

    def test_the_reason_is_kept_for_the_operator(self, db: RunDatabase, tmp_path: Path):
        _completed_run(db, tmp_path, "run-2")

        db.mark_delivery_abandoned("run-2", "upload scope missing")

        saved = db.get_run("run-2")
        assert saved is not None
        assert saved.delivery_status is DeliveryStatus.ABANDONED
        assert "upload scope missing" in (saved.delivery_error or "")

    def test_abandoning_does_not_touch_the_artifact(self, db: RunDatabase, tmp_path: Path):
        """The video is finished and on disk; only its delivery gave up."""
        _completed_run(db, tmp_path, "run-3")

        db.mark_delivery_abandoned("run-3", "gave up")

        saved = db.get_run("run-3")
        assert saved is not None
        assert saved.status == "completed"
        assert saved.output_path is not None
        assert Path(saved.output_path).exists()


class TestTheRunnerGivesUpAndMovesOn:
    """The runner retries a pending delivery *before* it considers generating,
    and returns afterwards. A delivery that can never succeed therefore consumed
    every wake, forever, producing nothing.
    """

    @staticmethod
    def _runner_with_pending(tmp_path: Path, attempts: int):
        from datetime import UTC, datetime, timedelta
        from unittest.mock import MagicMock

        from immich_memories.automation.runner import AutoRunner
        from immich_memories.config_loader import Config
        from immich_memories.tracking.models import RunMetadata

        config = Config(
            immich={"url": "http://immich.test:2283", "api_key": "k"},
            cache={
                "database": str(tmp_path / "runs.db"),
                "directory": str(tmp_path / "cache"),
            },
            automation={"upload_to_immich": True},
        )
        runner = AutoRunner(config, execute=MagicMock())
        output = tmp_path / "pending.mp4"
        output.write_bytes(b"video")
        now = datetime.now(tz=UTC)
        runner.db.save_run(
            RunMetadata(
                run_id="pending-run",
                created_at=now - timedelta(minutes=2),
                completed_at=now - timedelta(minutes=1),
                status="completed",
                source="auto",
                output_path=str(output),
                delivery_status=DeliveryStatus.PENDING,
                delivery_attempts=attempts,
                delivery_error="403 no upload scope",
            )
        )
        return runner

    def test_a_delivery_past_its_limit_is_abandoned(self, tmp_path: Path):
        runner = self._runner_with_pending(tmp_path, attempts=5)

        abandoned = abandon_if_exhausted(
            runner.db.get_oldest_pending_delivery(source="auto"),
            config=runner.config,
            db=runner.db,
        )

        assert abandoned is True
        assert runner.db.get_oldest_pending_delivery(source="auto") is None

    def test_the_original_failure_is_kept_in_the_reason(self, tmp_path: Path):
        """ "Gave up" without the cause sends the operator back to the logs."""
        runner = self._runner_with_pending(tmp_path, attempts=5)

        abandon_if_exhausted(
            runner.db.get_oldest_pending_delivery(source="auto"),
            config=runner.config,
            db=runner.db,
        )

        saved = runner.db.get_run("pending-run")
        assert saved is not None
        assert "403 no upload scope" in (saved.delivery_error or "")

    def test_a_delivery_inside_its_limit_is_still_retried(self, tmp_path: Path):
        runner = self._runner_with_pending(tmp_path, attempts=4)

        abandoned = abandon_if_exhausted(
            runner.db.get_oldest_pending_delivery(source="auto"),
            config=runner.config,
            db=runner.db,
        )

        assert abandoned is False
        assert runner.db.get_oldest_pending_delivery(source="auto") is not None
