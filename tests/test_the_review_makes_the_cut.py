"""The review makes the cut instead of vetoing one (#764, slice 1).

Measured on one real month: 204 candidates reached selection, arithmetic
removed 191 of them, and the only judgment in the pipeline was then allowed to
drop two clips out of fourteen per round. It took eight rounds of drop-and-
refill to remove what it could see in the first one, and a clip it named three
rounds running was capped away twice before it went.

A photographer culls the pool downward; our judge vetoed the cut upward. So
the pass now answers with the cut: which clips belong, and which do not.

Keep-semantics turns silence into a kill, which is why the answer has to
account for every clip exactly once. A truncated answer naming four clips to
keep would otherwise cut the rest of the memory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from immich_memories.analysis.selection_review import review_selection
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_models_llm import LLMConfig

DAY = datetime(2023, 6, 1, 12, tzinfo=UTC)


def _clip(asset_id: str, *, days: float = 0.0, starred: bool = False) -> ClipWithSegment:
    when = DAY + timedelta(days=days)
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        isFavorite=starred,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=4.0)
    clip.llm_description = f"a shot called {asset_id}"
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.5)


def _cut(keep: list[int], cut: list[int]) -> str:
    return json.dumps({"keep": keep, "cut": [{"index": i, "reason": f"cut {i}"} for i in cut]})


def _review(selection, raw):
    # WHY: the model. Everything under test is what we do with its answer.
    with patch("immich_memories.analysis.selection_review._ask", return_value=raw):
        return review_selection(selection, LLMConfig())


class TestTheAnswerIsTheCut:
    def test_everything_the_model_cuts_goes_in_one_pass(self):
        """No fifth-of-the-cut cap: eight rounds of two was the disease."""
        selection = [_clip(f"c{n}", days=n) for n in range(10)]

        verdict = _review(selection, _cut(keep=[1, 2, 3, 4, 5], cut=[6, 7, 8, 9, 10]))

        assert verdict.drops == ["c5", "c6", "c7", "c8", "c9"]

    def test_a_truncated_answer_cuts_nothing(self, caplog):
        """Silence is a kill now, so an answer that stops early cannot stand.

        Four clips named to keep out of ten, and the rest never reached: read
        literally that is a six-clip cut nobody decided on. The two lists have
        to account for every clip, which turns truncation back into what it
        always was — an answer we could not read.
        """
        import logging

        selection = [_clip(f"c{n}", days=n) for n in range(10)]

        with caplog.at_level(logging.WARNING, logger="immich_memories.analysis.selection_review"):
            verdict = _review(selection, '{"keep": [1, 2, 3, 4], "cut": []}')

        assert verdict.drops == []
        assert any(record.levelno >= logging.WARNING for record in caplog.records)


class TestTheCutIsTheResult:
    def test_what_the_review_cuts_leaves_the_selection_without_a_refill(self):
        """One pass, and its answer stands.

        The old shape dropped what it could see, re-selected to fill the gap,
        and had to run again because the refill had never been judged. Eight
        rounds of that on one month. The cut is now the cut: what the review
        removes stays removed, and the memory is shorter rather than topped up
        with whatever ranked next.
        """
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from immich_memories.analysis.selection_quality import SelectionQuality
        from immich_memories.analysis.smart_pipeline import PipelineResult
        from immich_memories.config_loader import Config

        selection = [_clip(f"c{n}", days=n) for n in range(5)]
        for member in selection:
            member.analyzed = True
        result = PipelineResult(
            selected_clips=[m.clip for m in selection],
            clip_segments={m.clip.asset.id: (0.0, 4.0) for m in selection},
            errors=[],
            stats={},
        )
        # WHY MagicMock: the analyzer, refiner, tracker and client are the
        # services this orchestrator composes. Nothing here needs them —
        # every clip is already analysed and described, so the verify pass has
        # nothing to look at and never re-selects.
        quality = SelectionQuality(
            config=SimpleNamespace(max_refinement_passes=3),
            app_config=Config(content_analysis={"enabled": True}),
            analyzer=MagicMock(),
            refiner=MagicMock(),
            tracker=MagicMock(),
            client=MagicMock(),
            provider_circuit=MagicMock(),
        )

        # WHY: the model. What the pass does with its answer is the subject.
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value=_cut(keep=[1, 2, 3], cut=[4, 5]),
        ):
            trimmed, remaining = quality.cut(selection, result)

        assert [c.asset.id for c in trimmed.selected_clips] == ["c0", "c1", "c2"]
        assert set(trimmed.clip_segments) == {"c0", "c1", "c2"}
        # The pool keeps what the cut left out. Editors re-mine dismissed
        # material once the edit re-contextualises it, and the pass that made
        # this cut is one pass among four to come — condemning the material
        # here would be the hard early reject the craft warns against.
        assert [m.clip.asset.id for m in remaining] == ["c0", "c1", "c2", "c3", "c4"]


class TestASilentPassIsNotAnApprovedCut:
    """The pass is fail-open and it is now the ONLY quality judgment.

    "0 drops" has meant both "the model read the set and approved it" and
    "the model never answered" for as long as the review has existed, and a
    reader could not tell them apart — the standing advice on this repo is to
    grep the log before blaming selection. With the old loop gone a fail-open
    run ships the whole pre-cut, and the strict partition check makes an
    unreadable answer MORE likely, by design. So the trace has to say it, in
    the artifact the cut is judged from rather than in another file.
    """

    def _quality(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from immich_memories.analysis.selection_quality import SelectionQuality
        from immich_memories.config_loader import Config

        # WHY MagicMock: the composed services. Every clip here is analysed
        # and described, so the verify pass has nothing to look at.
        return SelectionQuality(
            config=SimpleNamespace(max_refinement_passes=3),
            app_config=Config(content_analysis={"enabled": True}),
            analyzer=MagicMock(),
            refiner=MagicMock(),
            tracker=MagicMock(),
            client=MagicMock(),
            provider_circuit=MagicMock(),
        )

    def _run(self, answer: str, tmp_path):
        from immich_memories.analysis import selection_trace as trace
        from immich_memories.analysis.smart_pipeline import PipelineResult

        selection = [_clip(f"c{n}", days=n) for n in range(5)]
        for member in selection:
            member.analyzed = True
        result = PipelineResult(
            selected_clips=[m.clip for m in selection],
            clip_segments={m.clip.asset.id: (0.0, 4.0) for m in selection},
            errors=[],
            stats={},
        )
        report_path = tmp_path / "trace.md"
        # WHY: the model. What the trace says about its answer is the subject.
        with (
            trace.tracing(report_path),
            patch("immich_memories.analysis.selection_review._ask", return_value=answer),
        ):
            self._quality().cut(selection, result)
        return report_path.read_text()

    def test_an_unreadable_answer_says_so_in_the_trace(self, tmp_path):
        report = self._run("I could not decide, sorry", tmp_path)

        assert "could not be read" in report
        assert "not an approved cut" in report.lower()

    def test_an_approved_cut_is_not_reported_as_a_failure(self, tmp_path):
        report = self._run(_cut(keep=[1, 2, 3, 4, 5], cut=[]), tmp_path)

        assert "not an approved cut" not in report.lower()


class TestTheAccountSaysWhyAClipWasCut:
    def test_a_cut_clip_carries_its_reason_into_the_account(self, tmp_path):
        """Sam reads the account, not the log.

        The reason a clip went used to live only in the fate lines beside the
        funnel; the per-clip account named the stage and stopped there. "And
        why the rest are not" has to answer the question it asks.
        """
        from immich_memories.analysis import selection_trace as trace
        from immich_memories.analysis.smart_pipeline import PipelineResult

        selection = [_clip(f"c{n}", days=n) for n in range(5)]
        for member in selection:
            member.analyzed = True
        result = PipelineResult(
            selected_clips=[m.clip for m in selection],
            clip_segments={m.clip.asset.id: (0.0, 4.0) for m in selection},
            errors=[],
            stats={},
        )
        answer = json.dumps(
            {
                "keep": [1, 2, 3, 4],
                "cut": [{"index": 5, "reason": "an empty hallway, records a place"}],
            }
        )
        report_path = tmp_path / "trace.md"
        # WHY: the model. What the trace does with its reasons is the subject.
        with (
            trace.tracing(report_path),
            patch("immich_memories.analysis.selection_review._ask", return_value=answer),
        ):
            TestASilentPassIsNotAnApprovedCut()._quality().cut(selection, result)

        rejected = report_path.read_text().split("and why the rest are not")[1]
        assert "an empty hallway, records a place" in rejected
