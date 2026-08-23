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
