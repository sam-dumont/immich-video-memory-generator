"""How much of the candidate pool a run actually looked at (#489).

A pool whose scores are mostly metadata guesses ranks itself on list order.
These tests pin the number that says so out loud.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from immich_memories.analysis import selection_trace
from immich_memories.analysis.clip_refiner import ClipRefiner
from immich_memories.analysis.clip_scaler import ClipScaler
from immich_memories.analysis.selection_coverage import AnalysisCoverage, thin_coverage_notice
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.cli._pool_coverage import report_pool_coverage


def _make_clip(
    asset_id: str,
    date: datetime,
    *,
    score: float = 0.5,
    analyzed: bool = True,
) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=date,
        fileModifiedAt=date,
        updatedAt=date,
        isFavorite=False,
    )
    return ClipWithSegment(
        clip=VideoClipInfo(asset=asset, duration_seconds=5.0, width=1920, height=1080),
        start_time=0.0,
        end_time=5.0,
        score=score,
        analyzed=analyzed,
    )


def _tracker() -> MagicMock:
    # WHY: ProgressTracker is the terminal/UI progress boundary; refinement
    # only reports into it and reads back the error list it collected.
    tracker = MagicMock()
    tracker.progress = MagicMock()
    tracker.progress.errors = []
    return tracker


def _pool(analyzed_count: int, fallback_count: int) -> list[ClipWithSegment]:
    base = datetime(2021, 4, 1, tzinfo=UTC)
    looked_at = [
        _make_clip(f"seen-{i}", base + timedelta(days=i), score=0.7) for i in range(analyzed_count)
    ]
    guessed = [
        _make_clip(f"guess-{i}", base + timedelta(days=analyzed_count + i), analyzed=False)
        for i in range(fallback_count)
    ]
    return [*looked_at, *guessed]


class TestPoolCoverage:
    def test_refine_reports_how_much_of_the_pool_was_looked_at(self) -> None:
        refiner = ClipRefiner(PipelineConfig(target_clips=4), ClipScaler())

        result = refiner.phase_refine(_pool(analyzed_count=3, fallback_count=2), _tracker())

        assert (result.coverage.analyzed, result.coverage.total) == (3, 5)


class TestThinCoverageNotice:
    def test_a_mostly_guessed_pool_says_so_with_both_counts_and_a_share(self) -> None:
        # The #489 measurement: 25 of 149 candidates were actually looked at.
        notice = thin_coverage_notice(AnalysisCoverage(analyzed=25, total=149))

        assert notice == (
            "25 of 149 candidates (17%) were visually analyzed; "
            "the rest were picked on metadata. Review recommended."
        )

    def test_a_well_analyzed_pool_stays_quiet(self) -> None:
        assert thin_coverage_notice(AnalysisCoverage(analyzed=9, total=10)) is None

    def test_the_threshold_is_the_only_thing_that_makes_it_speak(self) -> None:
        assert thin_coverage_notice(AnalysisCoverage(analyzed=60, total=100)) is None
        assert thin_coverage_notice(AnalysisCoverage(analyzed=59, total=100)) is not None

    def test_an_empty_pool_has_nothing_to_warn_about(self) -> None:
        assert thin_coverage_notice(AnalysisCoverage(analyzed=0, total=0)) is None


class TestTraceCarriesCoverage:
    """--trace-selection reports coverage on every run, thin or not."""

    def test_the_trace_report_states_the_coverage(self) -> None:
        refiner = ClipRefiner(PipelineConfig(target_clips=4), ClipScaler())

        with selection_trace.tracing() as recorded:
            refiner.phase_refine(_pool(analyzed_count=3, fallback_count=2), _tracker())

        assert "3 of 5" in recorded.report()

    def test_a_healthy_run_still_records_it(self) -> None:
        refiner = ClipRefiner(PipelineConfig(target_clips=4), ClipScaler())

        with selection_trace.tracing() as recorded:
            refiner.phase_refine(_pool(analyzed_count=5, fallback_count=0), _tracker())

        assert recorded.as_dict()["coverage"] == {"analyzed": 5, "total": 5}


class TestCliSummaryLine:
    def test_a_mostly_guessed_pool_gets_one_line_in_the_generate_output(self) -> None:
        said: list[str] = []

        report_pool_coverage(AnalysisCoverage(analyzed=25, total=149), emit=said.append)

        assert said == [
            "25 of 149 candidates (17%) were visually analyzed; "
            "the rest were picked on metadata. Review recommended."
        ]

    def test_a_well_analyzed_run_says_nothing(self) -> None:
        said: list[str] = []

        report_pool_coverage(AnalysisCoverage(analyzed=95, total=100), emit=said.append)

        assert said == []
