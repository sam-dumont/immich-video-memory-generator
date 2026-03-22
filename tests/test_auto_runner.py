"""Tests for AutoRunner orchestrator."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.automation.candidates import MemoryCandidate
from immich_memories.automation.runner import AutoRunner, _build_generate_command
from immich_memories.config_loader import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a Config with a temp database and Immich credentials."""
    db_path = tmp_path / "test.db"
    return Config(
        immich={"url": "http://immich.test:2283", "api_key": "test-key"},
        cache={"database": str(db_path), "directory": str(tmp_path / "cache")},
    )


def _make_time_bucket(year: int, month: int, count: int):
    """Build a mock TimeBucket object."""
    bucket = MagicMock()
    bucket.time_bucket = f"{year}-{month:02d}-01T00:00:00.000Z"
    bucket.count = count
    return bucket


class TestSuggestReturnsCandidates:
    def test_monthly_candidates_from_time_buckets(self, config: Config) -> None:
        """Given time buckets with recent months, suggest returns monthly candidates."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = [
            _make_time_bucket(2026, 2, 150),
            _make_time_bucket(2026, 1, 200),
            _make_time_bucket(2025, 12, 100),
        ]
        mock_client.get_all_people.return_value = []

        from immich_memories.preflight import CheckStatus

        with (
            # WHY: external Immich server
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=mock_client,
            ),
            # WHY: external Immich server (preflight check)
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
        ):
            runner = AutoRunner(config)
            candidates = runner.suggest(limit=10)

        assert len(candidates) > 0
        types = {c.memory_type for c in candidates}
        assert "monthly_highlights" in types


class TestSuggestEmptyLibrary:
    def test_no_assets_returns_empty(self, config: Config) -> None:
        """No time buckets means no assets, so no candidates."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = []
        mock_client.get_all_people.return_value = []

        from immich_memories.preflight import CheckStatus

        with (
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=mock_client,
            ),
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
        ):
            runner = AutoRunner(config)
            candidates = runner.suggest(limit=10)

        assert candidates == []


class TestRunOneCooldown:
    def test_within_cooldown_returns_none(self, config: Config) -> None:
        """If a run completed within the cooldown window, run_one returns None."""
        from immich_memories.tracking.models import RunMetadata

        runner = AutoRunner(config)
        recent_run = RunMetadata(
            run_id="test_recent",
            created_at=datetime.now(tz=UTC) - timedelta(hours=1),
            status="completed",
        )
        runner.db.save_run(recent_run)

        result = runner.run_one(cooldown_hours=24)
        assert result is None


class TestRunOneDryRun:
    def test_dry_run_does_not_execute(self, config: Config) -> None:
        """Dry run logs the command but does not call subprocess."""
        candidate = MemoryCandidate(
            memory_type="monthly_highlights",
            date_range_start=date(2026, 2, 1),
            date_range_end=date(2026, 2, 28),
            person_names=[],
            memory_key="monthly_highlights:2026-02-01:2026-02-28:",
            score=0.7,
            reason="150 assets",
            asset_count=150,
        )

        runner = AutoRunner(config)

        # WHY: would launch real pipeline subprocess
        with (
            patch("immich_memories.automation.runner.subprocess") as mock_sub,
            patch.object(runner, "suggest", return_value=[candidate]),
        ):
            result = runner.run_one(force=True, dry_run=True)

        assert result is None
        mock_sub.run.assert_not_called()


class TestRunOneNoCandidates:
    def test_no_candidates_returns_none(self, config: Config) -> None:
        """If suggest returns empty, run_one returns None."""
        runner = AutoRunner(config)

        with patch.object(runner, "suggest", return_value=[]):
            result = runner.run_one(force=True)

        assert result is None


class TestBuildGenerateCommand:
    def test_monthly_command(self) -> None:
        candidate = MemoryCandidate(
            memory_type="monthly_highlights",
            date_range_start=date(2026, 2, 1),
            date_range_end=date(2026, 2, 28),
            person_names=[],
            memory_key="monthly_highlights:2026-02-01:2026-02-28:",
            score=0.7,
            reason="150 assets",
            asset_count=150,
        )
        cmd = _build_generate_command(candidate, upload=False)
        assert cmd == [
            "immich-memories",
            "generate",
            "--memory-type",
            "monthly_highlights",
            "--year",
            "2026",
            "--month",
            "2",
        ]

    def test_person_spotlight_with_upload(self) -> None:
        candidate = MemoryCandidate(
            memory_type="person_spotlight",
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            person_names=["Alice"],
            memory_key="person_spotlight:2025-01-01:2025-12-31:alice",
            score=0.6,
            reason="1st most featured person",
            asset_count=0,
        )
        cmd = _build_generate_command(candidate, upload=True)
        assert "--upload-to-immich" in cmd
        assert "--person" in cmd
        assert "Alice" in cmd

    def test_year_in_review_command(self) -> None:
        candidate = MemoryCandidate(
            memory_type="year_in_review",
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            person_names=[],
            memory_key="year_in_review:2025-01-01:2025-12-31:",
            score=0.8,
            reason="500 assets",
            asset_count=500,
        )
        cmd = _build_generate_command(candidate, upload=False)
        assert cmd == [
            "immich-memories",
            "generate",
            "--memory-type",
            "year_in_review",
            "--year",
            "2025",
        ]

    def test_trip_command(self) -> None:
        candidate = MemoryCandidate(
            memory_type="trip",
            date_range_start=date(2025, 7, 10),
            date_range_end=date(2025, 7, 17),
            person_names=[],
            memory_key="trip:2025-07-10:2025-07-17:",
            score=0.5,
            reason="7-day trip to Paris",
            asset_count=200,
        )
        cmd = _build_generate_command(candidate, upload=False)
        assert "--start" in cmd
        assert "2025-07-10" in cmd
        assert "--end" in cmd
        assert "2025-07-17" in cmd
