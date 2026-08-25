"""The stages that run before the pipeline opens its own trace.

The funnel report begins at the per-day photo cap because that is where
`SmartPipeline.run_selection` opens its tracing context. Two stages run before
it — the source-quality drop and the subject policy — and neither appears
anywhere in the report. On one real month the subject policy removed eleven
candidates (2 animal, 4 object, 5 screen) and the funnel could not name the
stage, let alone the clips.

That blind spot is where a life-event photograph was lost across four renders:
the trace showed a clean funnel above it and the kill was in the dark.
"""

from __future__ import annotations

from immich_memories.analysis import selection_trace as trace
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import VideoClipInfo
from tests.conftest import make_asset


def _candidate(asset_id: str, *, width: int = 1920, height: int = 1080, **asset_kwargs):
    asset_kwargs.setdefault("original_file_name", f"{asset_id}.MOV")
    asset = make_asset(asset_id, **asset_kwargs)
    asset.width, asset.height = width, height
    clip = VideoClipInfo(asset=asset, duration_seconds=4.0, width=width, height=height)
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.5)


class TestOneTracePerRun:
    def test_a_nested_context_joins_the_trace_already_open(self, tmp_path):
        """The pipeline opens its own context inside the runner's.

        Recording a pool stage is worthless if the pipeline then starts a
        second trace and writes only that one out: the stage lands in a trace
        nobody reads, and the report looks exactly as it does today.
        """
        outer, inner = tmp_path / "outer.md", tmp_path / "inner.md"
        kept, lost = _candidate("kept"), _candidate("lost")

        with trace.tracing(outer), trace.tracing(inner):
            trace.record("a stage inside the pipeline", [kept, lost], [kept])

        assert "a stage inside the pipeline" in outer.read_text()
        assert not inner.exists()


class TestThePoolStagesSayWhatTheyTook:
    def test_the_source_quality_drop_names_what_it_dropped(self, tmp_path):
        """A messaging re-encode leaves the pool before selection starts."""
        from immich_memories.cli._candidate_pool import _drop_reencoded_sources
        from immich_memories.config import Config

        kept = _candidate("from-the-camera")
        forwarded = _candidate("forwarded", width=640, height=480, exif_make=None, exif_model=None)
        report_path = tmp_path / "trace.md"

        with trace.tracing(report_path):
            survivors = _drop_reencoded_sources([kept, forwarded], config=Config())

        assert [c.clip.asset.id for c in survivors] == ["from-the-camera"]
        report = report_path.read_text()
        assert "source quality" in report
        assert "forwarded.MOV" in report

    def test_the_subject_policy_names_what_it_dropped(self, tmp_path):
        """The stage a life-event photograph died in, four renders running."""
        from immich_memories.cli._candidate_pool import _apply_subject_policy
        from immich_memories.config import Config

        pool = [_candidate(f"person-{n}") for n in range(3)]
        for member in pool:
            member.clip.llm_category = "people"
            member.score = 0.8
        screen = _candidate("a-screenshot")
        screen.clip.llm_category = "screen"
        screen.score = 0.5
        report_path = tmp_path / "trace.md"

        with trace.tracing(report_path):
            survivors = _apply_subject_policy(
                [*pool, screen], config=Config(), content_budget_seconds=60.0
            )

        assert "a-screenshot" not in {c.clip.asset.id for c in survivors}
        report = report_path.read_text()
        assert "subject policy" in report
        assert "a-screenshot.MOV" in report


class TestTheTraceNeverTakesTheRunDown:
    def test_a_candidate_it_cannot_describe_is_still_recorded(self, tmp_path):
        """Diagnostics may describe something badly; they may not raise.

        Now that the pool's stages record, the trace sees candidates far
        earlier and from more call sites than the funnel ever did.
        """
        from unittest.mock import MagicMock

        report_path = tmp_path / "trace.md"

        with trace.tracing(report_path):
            # WHY MagicMock: stands in for any candidate whose fields are not
            # the types the account assumes — the boundary being replaced is
            # "an object the trace has never seen before".
            trace.record("a stage", [MagicMock()], [])

        assert "a stage" in report_path.read_text()
