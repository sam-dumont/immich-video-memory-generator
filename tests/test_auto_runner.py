"""Tests for AutoRunner orchestrator."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.models import AutoOutcome, ProcessResult
from immich_memories.automation.runner import (
    AutoRunner,
    _build_generate_command,
    _build_last_runs_by_type,
    _execute_generate,
    _trailing_year_range,
)
from immich_memories.cli.auto_cmd import _candidates_to_json, _print_candidates_table
from immich_memories.config_loader import Config
from immich_memories.tracking.models import RunMetadata


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


@pytest.fixture
def candidate() -> MemoryCandidate:
    """Return one valid candidate for orchestration-boundary tests."""
    return MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2026, 2, 1),
        date_range_end=date(2026, 2, 28),
        person_names=[],
        memory_key="monthly_highlights:2026-02-01:2026-02-28:",
        score=0.7,
        reason="150 assets",
        asset_count=150,
    )


def _save_completed_run(
    runner: AutoRunner,
    candidate: MemoryCandidate,
    output_path: Path | None,
    *,
    run_id: str = "new-auto-run",
    memory_key: str | None = None,
    source: str = "auto",
    created_at: datetime | None = None,
    automation_attempt_id: str | None = None,
) -> None:
    """Seed the exact pipeline record that run_one validates after execution."""
    started = created_at or datetime.now() + timedelta(milliseconds=10)
    if automation_attempt_id is None:
        attempt = runner.state.get_last_attempt()
        automation_attempt_id = attempt.id if attempt is not None else None
    runner.db.save_run(
        RunMetadata(
            run_id=run_id,
            created_at=started,
            completed_at=started + timedelta(seconds=1),
            status="completed",
            source=source,
            memory_type=candidate.memory_type,
            memory_key=memory_key or candidate.memory_key,
            memory_category=candidate.category.value,
            automation_attempt_id=automation_attempt_id,
            output_path=str(output_path) if output_path else None,
        )
    )


class TestSuggestReturnsCandidates:
    def test_suggest_passes_configured_api_version_to_client(self, config: Config) -> None:
        from immich_memories.preflight import CheckStatus

        config.immich.api_version = ApiVersionPolicy.V2
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get_time_buckets.return_value = []
        client.get_all_people.return_value = []

        with (
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch(
                "immich_memories.api.immich.SyncImmichClient", return_value=client
            ) as client_factory,
        ):
            AutoRunner(config).suggest(limit=1)

        client_factory.assert_called_once_with(
            base_url="http://immich.test:2283",
            api_key="test-key",
            api_version=ApiVersionPolicy.V2,
        )

    def test_trailing_year_range_handles_leap_day(self) -> None:
        requested = _trailing_year_range(date(2024, 2, 29))

        assert requested.start == datetime(2023, 2, 28)
        assert requested.end == datetime(2024, 2, 29, 23, 59, 59, 999999)

    def test_trip_discovery_uses_trailing_year_and_keeps_new_year_trip(
        self, config: Config
    ) -> None:
        """Current-year and New-Year-crossing trips must reach production detection."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.preflight import CheckStatus

        config.trips.homebase_latitude = 50.85
        config.trips.homebase_longitude = 4.35
        config.automation.detect_monthly = False
        config.automation.detect_yearly = False
        config.automation.detect_person_spotlight = False
        config.automation.detect_activity_burst = False
        today = date(2026, 1, 20)
        gps_assets = [MagicMock(id="new-year-photo")]
        trip = DetectedTrip(
            start_date=date(2025, 12, 29),
            end_date=date(2026, 1, 4),
            location_name="New Year away",
            asset_count=80,
            centroid_lat=48.85,
            centroid_lon=2.35,
        )
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get_time_buckets.return_value = []
        asset_service = MagicMock()
        query = object()
        asset_service.get_assets_for_date_range.return_value = query
        client._run.return_value = gps_assets

        with (
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.api.all_assets_service.AllAssetsService",
                return_value=asset_service,
            ),
            patch(
                "immich_memories.analysis.trip_detection.detect_trips",
                return_value=[trip],
            ),
            patch("immich_memories.automation.runner.date") as clock,
        ):
            clock.today.return_value = today
            clock.side_effect = date
            candidates = AutoRunner(config).suggest(limit=10)

        requested = asset_service.get_assets_for_date_range.call_args.args[0]
        assert requested.start == datetime(2025, 1, 20)
        assert requested.end == datetime(2026, 1, 20, 23, 59, 59, 999999)
        client._run.assert_called_once_with(query)
        assert [
            (candidate.date_range_start, candidate.date_range_end) for candidate in candidates
        ] == [(date(2025, 12, 29), date(2026, 1, 4))]

    def test_same_type_scoring_cooldown_uses_only_auto_completions(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """A recent manual run does not penalize; a recent auto run does."""
        runner = AutoRunner(config)
        recent = datetime(2026, 8, 10, 9, 0)
        runner.db.save_run(
            RunMetadata(
                run_id="manual-monthly",
                created_at=recent,
                completed_at=recent + timedelta(minutes=10),
                status="completed",
                source="manual",
                memory_type=candidate.memory_type,
                memory_key="different-manual-key",
            )
        )
        manual_candidate = replace(candidate)
        score_and_rank(
            [manual_candidate],
            generated_keys=set(),
            today=date(2026, 8, 11),
            last_runs_by_type=_build_last_runs_by_type(runner.db),
        )

        runner.db.save_run(
            RunMetadata(
                run_id="auto-monthly",
                created_at=recent,
                completed_at=recent + timedelta(minutes=20),
                status="completed",
                source="auto",
                memory_type=candidate.memory_type,
                memory_key="different-auto-key",
            )
        )
        auto_candidate = replace(candidate)
        score_and_rank(
            [auto_candidate],
            generated_keys=set(),
            today=date(2026, 8, 11),
            last_runs_by_type=_build_last_runs_by_type(runner.db),
        )

        assert auto_candidate.score == pytest.approx(manual_candidate.score * 0.3)

    def test_monthly_candidates_from_time_buckets(self, config: Config) -> None:
        """Given time buckets with recent months, suggest returns monthly candidates."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = [
            _make_time_bucket(2026, 7, 150),
            _make_time_bucket(2026, 6, 200),
            _make_time_bucket(2026, 5, 100),
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
            patch("immich_memories.automation.runner.date") as mock_date,
        ):
            mock_date.today.return_value = date(2026, 8, 11)
            runner = AutoRunner(config)
            candidates = runner.suggest(limit=10)

        assert len(candidates) > 0
        types = {c.memory_type for c in candidates}
        assert "monthly_highlights" in types

    def test_suggest_handles_leap_day_person_in_non_leap_year(self, config: Config) -> None:
        """The public discovery flow uses the same observed-birthday rule as detection."""
        from immich_memories.preflight import CheckStatus

        person = MagicMock()
        person.id = "person-leap"
        person.name = "Leap"
        person.thumbnail_path = "/thumb.jpg"
        person.birth_date = date(2000, 2, 29)
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get_time_buckets.return_value = []
        client.get_all_people.return_value = [person]
        client.get_person_asset_count.return_value = 50

        with (
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=client,
            ),
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.automation.runner.date") as mock_date,
        ):
            mock_date.today.return_value = date(2025, 3, 10)
            mock_date.side_effect = date
            candidates = AutoRunner(config).suggest(limit=10)

        birthday = next(c for c in candidates if c.category is CandidateCategory.BIRTHDAY)
        assert birthday.date_range_start == date(2024, 2, 29)
        assert birthday.date_range_end == date(2025, 2, 27)

    def test_upcoming_january_birthday_suppresses_december_spotlight(self, config: Config) -> None:
        """Discovery looks into next year when rotating people near New Year."""
        from immich_memories.preflight import CheckStatus

        person = MagicMock()
        person.id = "person-new-year"
        person.name = "New Year"
        person.thumbnail_path = "/thumb.jpg"
        person.birth_date = date(2000, 1, 1)
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get_time_buckets.return_value = []
        client.get_all_people.return_value = [person]
        client.get_person_asset_count.return_value = 50

        with (
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=client,
            ),
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.automation.runner.date") as mock_date,
        ):
            mock_date.today.return_value = date(2025, 12, 27)
            mock_date.side_effect = date
            candidates = AutoRunner(config).suggest(limit=10)

        assert all(
            candidate.category is not CandidateCategory.PERSON_SPOTLIGHT for candidate in candidates
        )

    def test_variety_history_uses_only_completed_auto_runs(self, config: Config) -> None:
        """Manual, scheduled, failed, and running rows cannot block auto candidates."""
        from immich_memories.preflight import CheckStatus
        from immich_memories.tracking.models import RunMetadata

        runner = AutoRunner(config)
        histories = [
            RunMetadata(
                run_id="auto-trip",
                created_at=datetime(2026, 8, 6, 9, 0),
                completed_at=datetime(2026, 8, 6, 10, 0),
                status="completed",
                source="auto",
                memory_category=CandidateCategory.TRIP.value,
            ),
            RunMetadata(
                run_id="manual-monthly",
                created_at=datetime(2026, 8, 10, 9, 0),
                completed_at=datetime(2026, 8, 10, 10, 0),
                status="completed",
                source="manual",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
            ),
            RunMetadata(
                run_id="scheduled-monthly",
                created_at=datetime(2026, 8, 9, 9, 0),
                completed_at=datetime(2026, 8, 9, 10, 0),
                status="completed",
                source="scheduled",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
            ),
            RunMetadata(
                run_id="failed-auto-monthly",
                created_at=datetime(2026, 8, 11, 7, 0),
                status="failed",
                source="auto",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
            ),
            RunMetadata(
                run_id="running-auto-monthly",
                created_at=datetime(2026, 8, 11, 8, 0),
                status="running",
                source="auto",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
            ),
        ]
        for run in histories:
            runner.db.save_run(run)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = [_make_time_bucket(2026, 7, 150)]
        mock_client.get_all_people.return_value = []

        with (
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=mock_client,
            ),
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.automation.runner.date") as mock_date,
            patch.object(runner.db, "list_runs", wraps=runner.db.list_runs) as mock_list_runs,
        ):
            mock_date.today.return_value = date(2026, 8, 11)
            candidates = runner.suggest(limit=10)

        assert [candidate.category for candidate in candidates] == [
            CandidateCategory.MONTHLY_REVIEW
        ]
        assert runner.last_variety_decision.rejected == []
        mock_list_runs.assert_called_once_with(
            limit=6,
            status="completed",
            source="auto",
            order_by_completion=True,
        )

    def test_suggest_preserves_rejections_and_does_not_fallback(self, config: Config) -> None:
        """An all-rejected day stays empty and exposes the stable rule."""
        from immich_memories.automation.candidate_scorer import score_and_rank
        from immich_memories.preflight import CheckStatus
        from immich_memories.tracking.models import RunMetadata

        runner = AutoRunner(config)
        runner.db.save_run(
            RunMetadata(
                run_id="previous-monthly",
                created_at=datetime(2026, 8, 10, 9, 0),
                completed_at=datetime(2026, 8, 10, 10, 0),
                status="completed",
                source="auto",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
            )
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = [_make_time_bucket(2026, 7, 150)]
        mock_client.get_all_people.return_value = []

        with (
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=mock_client,
            ),
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.automation.runner.date") as mock_date,
            patch(
                "immich_memories.automation.runner.score_and_rank",
                wraps=score_and_rank,
            ) as mock_score,
        ):
            mock_date.today.return_value = date(2026, 8, 11)
            candidates = runner.suggest(limit=10)

        assert candidates == []
        assert [item.rule for item in runner.last_variety_decision.rejected] == [
            "same_category_as_previous"
        ]
        assert mock_score.call_args.args[0] == []

    def test_variety_uses_completion_order_for_category_and_people(self, config: Config) -> None:
        """Later completion wins even when its run started earlier."""
        from immich_memories.preflight import CheckStatus
        from immich_memories.tracking.models import RunMetadata

        runner = AutoRunner(config)
        history = [
            RunMetadata(
                run_id="created-last-completed-first",
                created_at=datetime(2026, 8, 10, 10, 0),
                completed_at=datetime(2026, 8, 10, 10, 30),
                status="completed",
                source="auto",
                memory_category=CandidateCategory.ON_THIS_DAY.value,
                memory_people=("Alice",),
            ),
            RunMetadata(
                run_id="middle",
                created_at=datetime(2026, 8, 10, 9, 30),
                completed_at=datetime(2026, 8, 10, 11, 0),
                status="completed",
                source="auto",
                memory_category=CandidateCategory.BIRTHDAY.value,
                memory_people=("Bob",),
            ),
            RunMetadata(
                run_id="created-first-completed-last",
                created_at=datetime(2026, 8, 10, 9, 0),
                completed_at=datetime(2026, 8, 10, 12, 0),
                status="completed",
                source="auto",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
                memory_people=("Carol",),
            ),
        ]
        for run in history:
            runner.db.save_run(run)

        monthly = MemoryCandidate(
            memory_type="monthly_highlights",
            category=CandidateCategory.MONTHLY_REVIEW,
            date_range_start=date(2026, 7, 1),
            date_range_end=date(2026, 7, 31),
            person_names=[],
            memory_key="monthly:completion-order",
            score=0.7,
            reason="latest month",
            asset_count=100,
        )
        alice = MemoryCandidate(
            memory_type="multi_person",
            category=CandidateCategory.MULTI_PERSON,
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            person_names=["Alice", "Dani"],
            memory_key="people:completion-order",
            score=0.6,
            reason="pair",
            asset_count=50,
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = []
        mock_client.get_all_people.return_value = []

        with (
            patch(
                "immich_memories.api.immich.SyncImmichClient",
                return_value=mock_client,
            ),
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.automation.runner.date") as mock_date,
            patch(
                "immich_memories.automation.runner._run_all_detectors",
                return_value=[monthly, alice],
            ),
        ):
            mock_date.today.return_value = date(2026, 8, 11)
            candidates = runner.suggest(limit=10)

        assert candidates == [alice]
        assert [item.rule for item in runner.last_variety_decision.rejected] == [
            "same_category_as_previous"
        ]


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
    def test_recent_completed_auto_run_returns_skipped_and_finishes_attempt_once(
        self, config: Config
    ) -> None:
        """Cooldown is an observable, durable zero-success outcome."""
        runner = AutoRunner(config)
        recent_run = RunMetadata(
            run_id="test_recent",
            created_at=datetime.now(tz=UTC) - timedelta(hours=1),
            status="completed",
            source="auto",
        )
        runner.db.save_run(recent_run)

        with patch.object(
            runner.state, "finish_attempt", wraps=runner.state.finish_attempt
        ) as finish:
            result = runner.run_one(cooldown_hours=24)

        assert result.outcome is AutoOutcome.SKIPPED
        assert result.reason == "cooldown active"
        assert result.candidate is None
        assert result.run_id is None
        assert result.output_path is None
        finish.assert_called_once()
        assert runner.state.get_last_attempt().outcome is AutoOutcome.SKIPPED

    def test_recent_manual_run_does_not_activate_auto_cooldown(self, config: Config) -> None:
        """A manual export must not consume the smart automation cooldown."""
        runner = AutoRunner(config)
        runner.db.save_run(
            RunMetadata(
                run_id="recent-manual",
                created_at=datetime.now(tz=UTC) - timedelta(hours=1),
                status="completed",
                source="manual",
            )
        )

        with patch.object(runner, "suggest", return_value=[]) as suggest:
            result = runner.run_one(cooldown_hours=24)

        assert result.outcome is AutoOutcome.SKIPPED
        assert result.reason == "no eligible candidates"
        suggest.assert_called_once_with(limit=1)


class TestRunOneDryRun:
    def test_dry_run_does_not_execute(self, config: Config, candidate: MemoryCandidate) -> None:
        """Dry run reports the candidate without crossing the process boundary."""
        execute = MagicMock()
        runner = AutoRunner(config, execute=execute)

        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True, dry_run=True)

        assert result.outcome is AutoOutcome.DRY_RUN
        assert result.reason == "dry run"
        assert result.candidate == candidate
        assert result.run_id is None
        assert result.output_path is None
        execute.assert_not_called()
        assert runner.state.get_last_attempt().outcome is AutoOutcome.DRY_RUN


class TestRunOneNoCandidates:
    def test_variety_rejection_is_typed_and_does_not_generate(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """A hard rejection must stay visible without relaxing or generating."""
        from immich_memories.preflight import CheckStatus

        execute = MagicMock()
        runner = AutoRunner(config, execute=execute)
        completed = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
        runner.db.save_run(
            RunMetadata(
                run_id="previous-monthly",
                created_at=completed - timedelta(minutes=5),
                completed_at=completed,
                status="completed",
                source="auto",
                memory_type="monthly_highlights",
                memory_category=CandidateCategory.MONTHLY_REVIEW.value,
            )
        )
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get_time_buckets.return_value = []
        client.get_all_people.return_value = []
        rejected_candidates = [
            replace(candidate, memory_key=f"{candidate.memory_key}:{index}") for index in range(25)
        ]

        with (
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.automation.runner._run_all_detectors",
                return_value=rejected_candidates,
            ),
            patch("immich_memories.automation.runner.date") as clock,
        ):
            clock.today.return_value = date(2026, 8, 11)
            clock.side_effect = date
            result = runner.run_one(force=True, dry_run=True)

        assert result.outcome is AutoOutcome.SKIPPED
        assert result.reason == "no eligible candidates"
        assert result.recent_categories == (CandidateCategory.MONTHLY_REVIEW.value,)
        assert len(result.rejections) == len(rejected_candidates)
        assert [(item.category, item.memory_key, item.rule) for item in result.rejections][:1] == [
            (
                CandidateCategory.MONTHLY_REVIEW.value,
                rejected_candidates[0].memory_key,
                "same_category_as_previous",
            )
        ]
        assert result.rejections[-1].memory_key == rejected_candidates[-1].memory_key
        execute.assert_not_called()
        assert len(runner.db.list_runs()) == 1

    def test_no_candidates_returns_skipped(self, config: Config) -> None:
        """An empty eligible set has its own truthful reason."""
        runner = AutoRunner(config)

        with patch.object(runner, "suggest", return_value=[]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.SKIPPED
        assert result.reason == "no eligible candidates"
        assert result.candidate is None
        assert runner.state.get_last_attempt().outcome is AutoOutcome.SKIPPED

    def test_attempt_is_running_before_suggest_preflight(self, config: Config) -> None:
        """The daily wake is durable before suggest performs its network preflight."""
        runner = AutoRunner(config)

        def observe_attempt(*, limit: int) -> list[MemoryCandidate]:
            assert limit == 1
            attempt = runner.state.get_last_attempt()
            assert attempt is not None
            assert attempt.outcome is AutoOutcome.RUNNING
            assert attempt.finished_at is None
            return []

        with patch.object(runner, "suggest", side_effect=observe_attempt):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.SKIPPED

    def test_suggest_error_finishes_attempt_as_failed(self, config: Config) -> None:
        """A preflight exception cannot leave a forever-running attempt."""
        runner = AutoRunner(config)

        with (
            patch.object(runner, "suggest", side_effect=RuntimeError("preflight exploded")),
            patch.object(
                runner.state, "finish_attempt", wraps=runner.state.finish_attempt
            ) as finish,
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "automation failed"
        assert result.error == "preflight exploded"
        finish.assert_called_once()
        assert runner.state.get_last_attempt().outcome is AutoOutcome.FAILED

    def test_preflight_error_is_failed_and_finishes_attempt_once(self, config: Config) -> None:
        """A returned Immich preflight error is not a healthy empty candidate set."""
        from immich_memories.preflight import CheckResult, CheckStatus

        runner = AutoRunner(config)
        preflight = CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Authentication failed",
            details=f"API key rejected: {config.immich.api_key}",
        )

        with (
            patch("immich_memories.preflight.check_immich", return_value=preflight),
            patch.object(
                runner.state, "finish_attempt", wraps=runner.state.finish_attempt
            ) as finish,
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "Immich preflight failed"
        assert result.error is not None
        assert "Authentication failed" in result.error
        assert config.immich.api_key not in result.error
        finish.assert_called_once()
        assert runner.state.get_last_attempt().outcome is AutoOutcome.FAILED

    def test_preflight_failure_signal_resets_before_next_healthy_suggest(
        self, config: Config
    ) -> None:
        """One server outage cannot poison a later healthy empty-library decision."""
        from immich_memories.preflight import CheckResult, CheckStatus

        runner = AutoRunner(config)
        failed = CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details="temporary outage",
        )
        healthy = CheckResult(name="Immich", status=CheckStatus.OK, message="Connected")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_time_buckets.return_value = []
        mock_client.get_all_people.return_value = []

        with patch("immich_memories.preflight.check_immich", return_value=failed):
            assert runner.suggest(limit=1) == []

        with (
            patch("immich_memories.preflight.check_immich", return_value=healthy),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=mock_client),
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.SKIPPED
        assert result.reason == "no eligible candidates"


class TestRunOneOutcomes:
    def test_held_configured_lease_skips_before_suggest_or_execute(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """A second wake skips without hiding the active durable attempt."""
        from immich_memories.automation.runner import AutomationLease

        suggest = MagicMock(return_value=[candidate])
        execute = MagicMock(return_value=ProcessResult(0, "", ""))
        runner = AutoRunner(config, execute=execute)
        lease_path = config.cache.database_path.parent / ".auto.lock"
        active = runner.state.start_attempt(reason="daily wake")

        with (
            AutomationLease(lease_path),
            patch.object(runner, "suggest", suggest),
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.SKIPPED
        assert result.reason == "automation already running"
        assert result.action is None
        last_attempt = runner.state.get_last_attempt()
        assert last_attempt is not None
        assert last_attempt.id == active.id
        assert last_attempt.outcome is AutoOutcome.RUNNING
        suggest.assert_not_called()
        execute.assert_not_called()
        notify.assert_not_called()

    def test_lease_uses_temp_database_scope_without_touching_real_home(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """A temp automation run cannot create or rewrite the production lease."""
        real_lease = Path.home() / ".immich-memories" / ".auto.lock"

        def file_state(path: Path) -> tuple[bool, int | None, int | None]:
            try:
                stat = path.stat()
            except FileNotFoundError:
                return False, None, None
            return True, stat.st_mtime_ns, stat.st_size

        before = file_state(real_lease)
        runner = AutoRunner(config)
        with patch.object(runner, "suggest", return_value=[]):
            runner.run_one(force=True)

        assert (config.cache.database_path.parent / ".auto.lock").exists()
        assert file_state(real_lease) == before

    @pytest.mark.parametrize("terminal", ["completed", "failed"])
    def test_lease_releases_after_terminal_generation(
        self,
        config: Config,
        candidate: MemoryCandidate,
        tmp_path: Path,
        terminal: str,
    ) -> None:
        """No terminal branch may strand the OS lease for the next daily wake."""
        import fcntl

        output = tmp_path / "released.mp4"
        output.write_bytes(b"video")
        runner = AutoRunner(config)

        def execute(argv: list[str]) -> ProcessResult:
            if terminal == "failed":
                return ProcessResult(2, "", "failed")
            attempt_id = next(
                value.split("=", 1)[1]
                for value in argv
                if value.startswith("--automation-attempt-id=")
            )
            _save_completed_run(
                runner,
                candidate,
                output,
                automation_attempt_id=attempt_id,
            )
            return ProcessResult(0, "", "")

        runner.execute = execute
        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is (
            AutoOutcome.COMPLETED if terminal == "completed" else AutoOutcome.FAILED
        )
        lease_path = config.cache.database_path.parent / ".auto.lock"
        with lease_path.open("w") as lease_file:
            fcntl.flock(lease_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lease_file, fcntl.LOCK_UN)

    def test_child_argv_and_completed_run_share_exact_attempt_id(
        self, config: Config, candidate: MemoryCandidate, tmp_path: Path
    ) -> None:
        """Removing the UUID from either side breaks durable parent-child correlation."""
        output = tmp_path / "exact.mp4"
        output.write_bytes(b"video")
        runner = AutoRunner(config)
        captured_id = ""

        def execute(argv: list[str]) -> ProcessResult:
            nonlocal captured_id
            captured_id = next(
                value.split("=", 1)[1]
                for value in argv
                if value.startswith("--automation-attempt-id=")
            )
            _save_completed_run(
                runner,
                candidate,
                output,
                run_id="exact-child",
                automation_attempt_id=captured_id,
            )
            return ProcessResult(0, "", "")

        runner.execute = execute
        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.COMPLETED
        assert captured_id == runner.state.get_last_attempt().id
        assert runner.db.get_run("exact-child").automation_attempt_id == captured_id

    def test_lease_is_held_through_output_file_verification(
        self, config: Config, candidate: MemoryCandidate, tmp_path: Path
    ) -> None:
        """The parent lease covers the final filesystem integrity check."""
        from immich_memories.automation.runner import (
            AutomationAlreadyRunningError,
            AutomationLease,
        )

        output = tmp_path / "verified.mp4"
        output.write_bytes(b"video")
        runner = AutoRunner(config)
        real_is_file = Path.is_file
        lease_path = config.cache.database_path.parent / ".auto.lock"

        def execute(_argv: list[str]) -> ProcessResult:
            _save_completed_run(runner, candidate, output)
            return ProcessResult(0, "", "")

        def verify_file(path: Path) -> bool:
            if path == output:
                with (
                    pytest.raises(AutomationAlreadyRunningError),
                    AutomationLease(lease_path),
                ):
                    pass
            return real_is_file(path)

        runner.execute = execute
        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch.object(Path, "is_file", verify_file),
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.COMPLETED

    def test_same_key_run_from_another_attempt_cannot_prove_success(
        self, config: Config, candidate: MemoryCandidate, tmp_path: Path
    ) -> None:
        """Key/time proximity is not proof that this wake generated the file."""
        output = tmp_path / "wrong-parent.mp4"
        output.write_bytes(b"video")
        runner = AutoRunner(config)

        def execute(_argv: list[str]) -> ProcessResult:
            _save_completed_run(
                runner,
                candidate,
                output,
                automation_attempt_id="some-other-attempt",
            )
            return ProcessResult(0, "", "")

        runner.execute = execute
        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "no matching completed auto run"

    @pytest.mark.parametrize(
        ("integrity_failure", "expected_reason"),
        [
            ("no_run", "no matching completed auto run"),
            ("no_output", "matching run has no output path"),
            ("missing_file", "generated output file is missing"),
        ],
    )
    def test_parent_integrity_failures_persist_then_notify_once(
        self,
        config: Config,
        candidate: MemoryCandidate,
        tmp_path: Path,
        integrity_failure: str,
        expected_reason: str,
    ) -> None:
        """Every parent-owned integrity failure has one durable, notified result."""
        config.immich.api_key = "integrity-secret-5e91"
        runner = AutoRunner(config)

        def execute(_argv: list[str]) -> ProcessResult:
            if integrity_failure == "no_output":
                _save_completed_run(runner, candidate, None)
            elif integrity_failure == "missing_file":
                _save_completed_run(runner, candidate, tmp_path / "absent.mp4")
            return ProcessResult(0, "", "")

        runner.execute = execute
        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch.object(
                runner.state, "finish_attempt", wraps=runner.state.finish_attempt
            ) as finish,
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == expected_reason
        assert result.error == attempt.error == expected_reason
        assert "integrity-secret-5e91" not in result.error
        finish.assert_called_once()
        notify.assert_called_once()
        assert notify.call_args.kwargs["error"] == result.error

    def test_notification_exception_cannot_replace_durable_failure(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """Notification transport failure happens after the terminal DB write."""
        runner = AutoRunner(
            config,
            execute=lambda _argv: ProcessResult(9, "child failed", ""),
        )
        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch.object(
                runner.state, "finish_attempt", wraps=runner.state.finish_attempt
            ) as finish,
            patch(
                "immich_memories.automation.runner._send_notification",
                side_effect=RuntimeError("notification transport broke"),
            ) as notify,
        ):
            result = runner.run_one(force=True)

        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert result.outcome is AutoOutcome.FAILED
        assert result.error == attempt.error
        assert "child failed" in result.error
        finish.assert_called_once()
        notify.assert_called_once()

    def test_outer_candidate_exception_is_redacted_persisted_and_notified_once(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """An exception after selection belongs to that candidate and one finalizer."""
        config.immich.api_key = "outer-secret-78dd"
        runner = AutoRunner(config)
        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch(
                "immich_memories.automation.runner._build_generate_command",
                side_effect=RuntimeError("bad request outer-secret-78dd"),
            ),
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "automation failed"
        assert result.error == attempt.error
        assert "outer-secret-78dd" not in result.error
        assert "***" in result.error
        notify.assert_called_once()
        assert notify.call_args.kwargs["error"] == result.error

    def test_failed_process_uses_stdout_error_and_finishes_once(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """A nonzero process is FAILED even when its only useful detail is stdout."""
        execute = MagicMock(return_value=ProcessResult(7, "root cause on stdout", ""))
        runner = AutoRunner(config, execute=execute)

        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch.object(
                runner.state, "finish_attempt", wraps=runner.state.finish_attempt
            ) as finish,
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "generation subprocess exited with code 7"
        assert result.error is not None
        assert "root cause on stdout" in result.error
        assert result.run_id is None
        assert result.output_path is None
        execute.assert_called_once()
        finish.assert_called_once()
        notify.assert_called_once()

    def test_nonzero_failure_redacts_secret_before_persisting_and_notifying(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """The finalizer sends the same redacted child error it stores and returns."""
        configured_value = "configured-child-secret-92bf"
        config.immich.api_key = configured_value
        runner = AutoRunner(
            config,
            execute=lambda _argv: ProcessResult(7, f"child rejected {configured_value}", ""),
        )

        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert result.error == attempt.error
        assert configured_value not in result.error
        assert "***" in result.error
        notify.assert_called_once()
        assert notify.call_args.kwargs["error"] == result.error

    def test_failure_redacts_every_configured_generation_credential(
        self,
        config: Config,
        candidate: MemoryCandidate,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Separate music and title credentials cannot reach any failure surface."""
        secrets = {
            "immich": "immich-secret-8d2a",
            "llm": "primary-llm-secret-6f13",
            "title_llm": "title-llm-secret-a497",
            "musicgen": "musicgen-secret-32ce",
            "ace_step": "ace-step-secret-4b81",
            "notification": "https://notify.test/notification-secret-0ea5",
        }
        config.immich.api_key = secrets["immich"]
        config.llm.api_key = secrets["llm"]
        config.title_llm = config.llm.model_copy(update={"api_key": secrets["title_llm"]})
        config.musicgen.api_key = secrets["musicgen"]
        config.ace_step.api_key = secrets["ace_step"]
        config.notifications.urls = [secrets["notification"]]
        child_output = "child exposed " + " ".join(secrets.values())
        runner = AutoRunner(
            config,
            execute=lambda _argv: ProcessResult(7, child_output, ""),
        )

        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner._send_notification") as notify,
            caplog.at_level(logging.ERROR),
        ):
            result = runner.run_one(force=True)

        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert result.error == attempt.error
        assert notify.call_args.kwargs["error"] == result.error
        exposed_surfaces = "\n".join(
            (
                result.error or "",
                attempt.error or "",
                notify.call_args.kwargs["error"] or "",
                caplog.text,
            )
        )
        for secret in secrets.values():
            assert secret not in exposed_surfaces
        assert "***" in exposed_surfaces

    def test_process_error_details_are_sanitized_and_bounded(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """Persisted subprocess tails cannot grow forever or retain obvious API keys."""
        execute = MagicMock(
            return_value=ProcessResult(
                2,
                "x" * 5000 + " api_key=stdout-secret",
                "y" * 5000 + " x-api-key: stderr-secret",
            )
        )
        runner = AutoRunner(config, execute=execute)

        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.error is not None
        assert len(result.error) <= 4100
        assert "stdout-secret" not in result.error
        assert "stderr-secret" not in result.error
        assert "***" in result.error

    def test_long_process_streams_keep_both_sanitized_tails_in_failure_outputs(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """The finalizer cannot collapse two bounded stream tails into stderr alone."""
        configured_value = "combined-stream-secret-4d71"
        config.immich.api_key = configured_value
        stdout = "x" * 3000 + f" STDOUT-TAIL-MARKER {configured_value}"
        stderr = "y" * 3000 + f" STDERR-TAIL-MARKER {configured_value}"
        runner = AutoRunner(
            config,
            execute=lambda _argv: ProcessResult(8, stdout, stderr),
        )

        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert result.error == attempt.error
        assert result.error is not None
        assert "STDOUT-TAIL-MARKER" in result.error
        assert "STDERR-TAIL-MARKER" in result.error
        assert configured_value not in result.error
        assert "***" in result.error
        assert len(result.error) <= 4100
        notify.assert_called_once()
        assert notify.call_args.kwargs["error"] == result.error

    def test_configured_immich_key_is_redacted_before_tail_boundary(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """Truncation cannot split a configured key and retain its secret suffix."""
        secret = "immich-key-" + "q" * 80 + "-IMMICH-END-73ac"
        config.immich.api_key = secret
        stdout = f"process output {secret}{'z' * 1980}"
        completed = subprocess.CompletedProcess(["generate"], 7, stdout, "")
        runner = AutoRunner(config)

        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner.subprocess.run", return_value=completed),
        ):
            result = runner.run_one(force=True)

        assert result.error is not None
        assert secret not in result.error
        assert secret[-16:] not in result.error
        assert "***" in result.error
        assert runner.state.get_last_attempt().error == result.error

    @pytest.mark.parametrize(
        "credential_path",
        [
            "immich.api_key",
            "llm.api_key",
            "title_llm.api_key",
            "musicgen.api_key",
            "ace_step.api_key",
            "auth.password",
            "auth.client_secret",
        ],
    )
    def test_every_configured_credential_is_redacted_before_tail_boundary(
        self,
        config: Config,
        candidate: MemoryCandidate,
        credential_path: str,
    ) -> None:
        """Every credential-bearing config field must redact before truncation."""
        secret = credential_path.replace(".", "-") + "-" + "q" * 80 + "-SECRET-END-91de"
        section_name, field_name = credential_path.split(".")
        if section_name == "title_llm":
            config.title_llm = config.llm.model_copy(update={field_name: secret})
        else:
            setattr(getattr(config, section_name), field_name, secret)
        safe_marker = "configured-model-name-must-remain"
        config.llm.model = safe_marker
        stderr = f"process output {secret}{'z' * 1940} {safe_marker}"
        runner = AutoRunner(config, execute=lambda _argv: ProcessResult(7, "", stderr))

        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.error is not None
        assert secret not in result.error
        assert secret[-16:] not in result.error
        assert "***" in result.error
        assert safe_marker in result.error
        assert runner.state.get_last_attempt().error == result.error

    def test_notification_url_is_redacted_before_tail_boundary(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        """A webhook URL crossing the retained tail boundary cannot leak its token suffix."""
        secret_url = "https://notify.test/" + "w" * 80 + "/WEBHOOK-END-19bd"
        config.notifications.urls = [secret_url]
        stderr = f"delivery error {secret_url}{'y' * 1980}"
        completed = subprocess.CompletedProcess(["generate"], 9, "", stderr)
        runner = AutoRunner(config)

        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner.subprocess.run", return_value=completed),
        ):
            result = runner.run_one(force=True)

        assert result.error is not None
        assert secret_url not in result.error
        assert secret_url[-16:] not in result.error
        assert "***" in result.error
        assert runner.state.get_last_attempt().error == result.error

    def test_exit_zero_without_matching_new_run_is_failure(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        runner = AutoRunner(config, execute=lambda _argv: ProcessResult(0, "", ""))

        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "no matching completed auto run"
        assert result.run_id is None
        assert result.output_path is None

    def test_stale_matching_and_new_unrelated_runs_are_rejected(
        self, config: Config, candidate: MemoryCandidate, tmp_path: Path
    ) -> None:
        """Neither a pre-attempt match nor a post-attempt different key proves success."""
        output = tmp_path / "stale.mp4"
        output.touch()
        runner = AutoRunner(config)
        _save_completed_run(
            runner,
            candidate,
            output,
            run_id="stale-match",
            created_at=datetime.now() - timedelta(days=1),
        )

        def execute(_argv: list[str]) -> ProcessResult:
            _save_completed_run(
                runner,
                candidate,
                output,
                run_id="new-unrelated",
                memory_key="other:key",
            )
            return ProcessResult(0, "", "")

        runner.execute = execute
        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.run_id is None
        assert result.output_path is None

    def test_matching_run_without_output_path_is_failure(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        runner = AutoRunner(config)

        def execute(_argv: list[str]) -> ProcessResult:
            _save_completed_run(runner, candidate, None)
            return ProcessResult(0, "", "")

        runner.execute = execute
        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "matching run has no output path"
        assert result.run_id is None
        assert result.output_path is None

    def test_matching_run_with_missing_file_is_failure(
        self, config: Config, candidate: MemoryCandidate, tmp_path: Path
    ) -> None:
        missing = tmp_path / "missing.mp4"
        runner = AutoRunner(config)

        def execute(_argv: list[str]) -> ProcessResult:
            _save_completed_run(runner, candidate, missing)
            return ProcessResult(0, "", "")

        runner.execute = execute
        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "generated output file is missing"
        assert result.run_id is None
        assert result.output_path is None

    def test_matching_new_run_and_existing_file_is_completed(
        self, config: Config, candidate: MemoryCandidate, tmp_path: Path
    ) -> None:
        output = tmp_path / "memory.mp4"
        output.write_bytes(b"video")
        runner = AutoRunner(config)

        def execute(_argv: list[str]) -> ProcessResult:
            _save_completed_run(runner, candidate, output, run_id="proved-run")
            return ProcessResult(0, "generated", "")

        runner.execute = execute
        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.COMPLETED
        assert result.reason == "generation completed"
        assert result.run_id == "proved-run"
        assert result.output_path == output
        assert result.error is None
        assert runner.state.get_last_attempt().run_id == "proved-run"
        notify.assert_not_called()

    def test_timeout_is_failed_with_specific_message_and_one_notification(
        self, config: Config, candidate: MemoryCandidate
    ) -> None:
        def timeout(_argv: list[str]) -> ProcessResult:
            raise subprocess.TimeoutExpired(
                cmd=["immich-memories", "generate"],
                timeout=7200,
                output="partial stdout",
                stderr="partial stderr",
            )

        runner = AutoRunner(config, execute=timeout)
        with (
            patch.object(runner, "suggest", return_value=[candidate]),
            patch("immich_memories.automation.runner._send_notification") as notify,
        ):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.reason == "generation timed out after 2 hours"
        assert result.error is not None
        assert "partial stdout" in result.error
        assert "partial stderr" in result.error
        notify.assert_called_once()

    def test_execute_adapter_captures_process_result_with_two_hour_timeout(self) -> None:
        completed = subprocess.CompletedProcess(["generate"], 0, "stdout", "stderr")
        with patch(
            "immich_memories.automation.runner.subprocess.run", return_value=completed
        ) as run:
            result = _execute_generate(["immich-memories", "generate"])

        assert result == ProcessResult(0, "stdout", "stderr")
        run.assert_called_once_with(
            ["immich-memories", "generate"],
            capture_output=True,
            text=True,
            timeout=7200,
        )


class TestBuildGenerateCommand:
    def test_monthly_command(self) -> None:
        candidate = MemoryCandidate(
            memory_type="monthly_highlights",
            category=CandidateCategory.MONTHLY_REVIEW,
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
            "--source=auto",
            "--memory-key=monthly_highlights:2026-02-01:2026-02-28:",
            "--memory-category=monthly_review",
        ]

    def test_person_spotlight_with_upload(self) -> None:
        candidate = MemoryCandidate(
            memory_type="person_spotlight",
            category=CandidateCategory.PERSON_SPOTLIGHT,
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
        assert "--person=Alice" in cmd

    def test_year_in_review_command(self) -> None:
        candidate = MemoryCandidate(
            memory_type="year_in_review",
            category=CandidateCategory.YEAR_IN_REVIEW,
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
            "--source=auto",
            "--memory-key=year_in_review:2025-01-01:2025-12-31:",
            "--memory-category=year_in_review",
        ]

    def test_unknown_category_never_launches_subprocess(self, config: Config) -> None:
        candidate = MemoryCandidate(
            memory_type="year_in_review",
            category=cast(CandidateCategory, "unknown"),
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            person_names=[],
            memory_key="unknown:key",
            score=0.8,
            reason="unknown detector",
            asset_count=500,
        )
        runner = AutoRunner(config)

        with patch.object(runner, "suggest", return_value=[candidate]):
            result = runner.run_one(force=True)

        assert result.outcome is AutoOutcome.FAILED
        assert result.error is not None
        assert "Unsupported automation category" in result.error

    def test_trip_command(self) -> None:
        candidate = MemoryCandidate(
            memory_type="trip",
            category=CandidateCategory.TRIP,
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

    def test_multi_person_uses_equals_syntax(self) -> None:
        """Person names use --person=Name to prevent flag injection."""
        candidate = MemoryCandidate(
            memory_type="multi_person",
            category=CandidateCategory.MULTI_PERSON,
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            person_names=["Alice", "--evil"],
            memory_key="multi_person:2025-01-01:2025-12-31:alice:--evil",
            score=0.5,
            reason="pair",
            asset_count=50,
        )
        cmd = _build_generate_command(candidate, upload=False)
        assert "--person=Alice" in cmd
        assert "--person=--evil" in cmd


class TestSuggestOutput:
    def test_json_and_table_include_candidate_category(self) -> None:
        candidate = MemoryCandidate(
            memory_type="monthly_highlights",
            category=CandidateCategory.MONTHLY_REVIEW,
            date_range_start=date(2026, 2, 1),
            date_range_end=date(2026, 2, 28),
            person_names=[],
            memory_key="monthly_highlights:2026-02-01:2026-02-28:",
            score=0.7,
            reason="150 assets",
            asset_count=150,
        )

        row = json.loads(_candidates_to_json([candidate]))[0]
        assert row["category"] == "monthly_review"

        with patch(
            "immich_memories.cli.auto_cmd.console", new=Console(record=True, width=200)
        ) as console:
            _print_candidates_table([candidate])

        rendered = console.export_text()
        assert "Category" in rendered
        assert "monthly_review" in rendered
