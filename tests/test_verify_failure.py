"""A failed analysis is not a verdict of zero.

The verify pass looks again at clips the review cannot see and writes the
result back over what selection had. When that look fails — a transient
download error, a decode that dies — the analyzer hands back a placeholder
scored 0.0, and writing it back replaces a real score with junk. The judge's
floor then drops the clip and removes it from the pool for good, so a clip
whose download blipped once is gone from the memory.

The verify docstring already promised this could not happen: "a clip whose
analysis fails keeps its fallback score".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.analysis.smart_pipeline import (
    ClipWithSegment,
    PipelineConfig,
    PipelineResult,
    SmartPipeline,
)
from immich_memories.config import Config
from immich_memories.config_models import AnalysisConfig
from tests.conftest import make_clip


def _pipeline(tmp_path: Path) -> SmartPipeline:
    analysis_cache = MagicMock()
    analysis_cache.get_analysis.return_value = None
    return SmartPipeline(
        client=MagicMock(),
        analysis_cache=analysis_cache,
        thumbnail_cache=MagicMock(),
        config=PipelineConfig(),
        analysis_config=AnalysisConfig(),
        app_config=Config(
            cache={"directory": str(tmp_path / "cache")},
            llm={"model": "qwen-3.6"},
            content_analysis={"enabled": True},
        ),
    )


def _result(clip) -> PipelineResult:
    return PipelineResult(
        selected_clips=[clip], clip_segments={clip.asset.id: (0.0, 4.0)}, errors=[]
    )


def test_a_failed_look_leaves_the_score_it_could_not_improve(tmp_path: Path) -> None:
    """A download that blips must not cost the clip its place."""
    clip = make_clip("blipped", duration=10.0)
    member = ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.72, analyzed=False)

    pipeline = _pipeline(tmp_path)
    # WHY: analysis downloads and decodes video. This is the analyzer's own
    # failure placeholder — scored 0.0 and marked unanalyzed.
    failed = ClipWithSegment(
        clip=make_clip("blipped", duration=10.0),
        start_time=0.0,
        end_time=4.0,
        score=0.0,
        analyzed=False,
    )
    pipeline.analyzer.phase_analyze = MagicMock(return_value=[failed])
    pipeline.refiner.phase_refine = MagicMock(side_effect=lambda a, _t: _result(a[0].clip))

    _result_out, analyzed = pipeline._verify_selection([member], _result(clip))

    assert [m.score for m in analyzed] == [0.72], "a failed look overwrote a real score"


def test_a_look_that_succeeded_does_replace_the_score(tmp_path: Path) -> None:
    """The pass exists to correct optimistic guesses; that must keep working."""
    clip = make_clip("optimistic", duration=10.0)
    member = ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.9, analyzed=False)

    pipeline = _pipeline(tmp_path)
    # WHY: analysis is the boundary; here it succeeds and the clip is poor.
    looked = ClipWithSegment(
        clip=make_clip("optimistic", duration=10.0),
        start_time=0.0,
        end_time=4.0,
        score=0.05,
        analyzed=True,
    )
    pipeline.analyzer.phase_analyze = MagicMock(return_value=[looked])
    pipeline.refiner.phase_refine = MagicMock(side_effect=lambda a, _t: _result(a[0].clip))

    _result_out, analyzed = pipeline._verify_selection([member], _result(clip))

    assert [m.score for m in analyzed] == [0.05]


class TestAModelSwitchCanRegenerateSemantics:
    """After changing llm.model every cached clip is objective-fresh and
    semantics-stale, and the cache hit was returned before anything could
    regenerate them. _needs_a_real_look then re-flagged the clip every round
    and the verify pass "analyzed" it as a cache-hit no-op, forever.

    The holistic review is left judging bare lines it is instructed never to
    drop for missing information — the whole class of defect that having
    descriptions is supposed to prevent, library-wide, until ANALYSIS_VERSION
    happens to bump.
    """

    def _analyzer(self, tmp_path: Path, *, content_analysis: bool):
        from immich_memories.analysis.clip_analyzer import ClipAnalyzer

        return ClipAnalyzer(
            config=PipelineConfig(),
            client=MagicMock(),
            analysis_cache=MagicMock(),
            preview_builder=MagicMock(),
            app_config=Config(
                cache={"directory": str(tmp_path / "cache")},
                llm={"model": "the-new-model"},
                content_analysis={"enabled": content_analysis},
            ),
        )

    def test_a_hit_with_no_semantics_is_not_a_complete_answer(self, tmp_path: Path) -> None:
        """Objective scores survived the model switch; the semantics did not."""
        analyzer = self._analyzer(tmp_path, content_analysis=True)

        assert not analyzer._cache_hit_is_complete((1.0, 5.0, 0.7, None, None))
        assert analyzer._cache_hit_is_complete((1.0, 5.0, 0.7, None, {"description": "a cake"}))

    def test_a_hit_with_semantics_still_short_circuits(self, tmp_path: Path) -> None:
        """A warm cache must stay warm — this is what makes reruns cheap."""
        analyzer = self._analyzer(tmp_path, content_analysis=True)
        analyzer._check_analysis_cache = MagicMock(
            return_value=(1.0, 5.0, 0.7, None, {"description": "a birthday cake"})
        )
        analyzer._download_analysis_video = MagicMock(side_effect=AssertionError("re-downloaded"))

        start, _end, _score, _preview, payload = analyzer._analyze_clip_with_preview(
            make_clip("warm", duration=10.0)
        )

        assert start == 1.0
        assert payload == {"description": "a birthday cake"}

    def test_with_content_analysis_off_a_hit_is_complete(self, tmp_path: Path) -> None:
        """Nothing is missing if nothing was going to be generated."""
        analyzer = self._analyzer(tmp_path, content_analysis=False)
        analyzer._check_analysis_cache = MagicMock(return_value=(1.0, 5.0, 0.7, None, None))
        analyzer._download_analysis_video = MagicMock(side_effect=AssertionError("re-downloaded"))

        start, *_rest = analyzer._analyze_clip_with_preview(make_clip("no-llm", duration=10.0))

        assert start == 1.0
