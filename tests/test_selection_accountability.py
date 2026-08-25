"""Why THIS picture is in the cut, and why that one is not.

The funnel says a stage dropped nine clips. It never said which nine, though
it worked them out to count them — `record` computes the losers per asset and
kept only the total. So the one question actually asked of a rejected sheet,
"why is that in there", took a render cycle and a guess to answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo

WHEN = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _clip(asset_id: str, *, score: float = 0.5, starred: bool = False) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=WHEN,
        fileModifiedAt=WHEN,
        updatedAt=WHEN,
        isFavorite=starred,
        originalFileName=f"{asset_id}.mp4",
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=4.0)
    clip.llm_category = "landscape"
    clip.llm_interestingness = 0.4
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=score)


def _traced() -> Trace:
    kept, cut, late = _clip("kept", score=0.9), _clip("cut", score=0.2), _clip("late")
    trace = Trace()
    trace.record("per-day photo cap", [kept, cut], [kept])
    trace.record("fit to 30s", [kept], [kept])
    trace.record("duration backfill", [kept], [kept, late], ["occasion_revisit"])
    return trace


class TestEveryClipCanAccountForItself:
    def test_a_shipped_clip_names_the_stages_it_survived(self):
        story = _traced().story_of("kept")

        assert story.shipped
        assert "per-day photo cap" in story.survived
        assert "fit to 30s" in story.survived

    def test_a_dropped_clip_names_the_stage_that_dropped_it(self):
        story = _traced().story_of("cut")

        assert not story.shipped
        assert story.dropped_at == "per-day photo cap"

    def test_a_clip_that_arrived_late_says_where_it_came_in(self):
        """Backfill admits clips no earlier stage ever saw. "Why is that in
        there" is answered by the stage that let it in, not by the funnel."""
        story = _traced().story_of("late")

        assert story.shipped
        assert story.admitted_at == "duration backfill"


class TestTheReportShowsIt:
    def test_the_report_accounts_for_each_clip(self):
        report = _traced().report()

        assert "kept" in report
        assert "cut" in report
        assert "per-day photo cap" in report


class TestTheAccountShowsTheClipAsItFinallyWas:
    def test_facts_learned_later_reach_the_account(self):
        """A look can happen after a clip is already in the cut.

        Facts were snapshotted the first time any stage saw a clip and never
        refreshed, so a carrier looked at during verify still read as
        category-less in the account — and that artifact was mistaken for
        evidence that the label never arrived.
        """
        clip = _clip("carrier")
        clip.clip.llm_category = None

        trace = Trace()
        trace.record("pool", [clip], [clip])
        clip.clip.llm_category = "screen"
        trace.record("verify", [clip], [clip])

        assert "screen" in trace.story_of("carrier").facts
