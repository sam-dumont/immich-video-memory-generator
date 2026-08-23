"""A scoring component with no information must not vote.

Every scoring defect found in the #292 audit was this same mistake: a component
that had nothing to say returned a middling constant instead of abstaining, and
that constant then competed with real measurements. A midpoint is only neutral
if it sits at the centre of what the component actually produces, and none of
these did.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.analysis.analyzer_models import CutPoint
from immich_memories.analysis.scoring import SceneScorer
from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
from immich_memories.config_models_analysis import (
    AnalysisConfig,
    AudioContentConfig,
    ContentAnalysisConfig,
)


def _analyzer() -> UnifiedSegmentAnalyzer:
    return UnifiedSegmentAnalyzer(
        scorer=SceneScorer(
            content_analysis_config=ContentAnalysisConfig(),
            analysis_config=AnalysisConfig(),
        ),
        audio_content_enabled=True,
        audio_content_config=AudioContentConfig(),
        analysis_config=AnalysisConfig(),
    )


def test_unavailable_audio_scores_the_same_as_disabled_audio(tmp_path: Path):
    """Audio analysis that produced no result must abstain, not vote its default.

    `ScoredSegment.audio_score` defaults to 0.5. When audio analysis was enabled
    but returned nothing -- unavailable, or it raised -- that default was left in
    place and still weighted at `audio_content_weight`. A video whose audio
    analysis failed therefore scored *higher* than one with real speech, which
    measures around 0.32.

    Asking for the same total as the explicitly-disabled path states the contract
    without pinning either number.
    """
    analyzer = _analyzer()
    unreadable = tmp_path / "not-a-video.mp4"
    unreadable.write_bytes(b"")
    candidates = [
        (
            CutPoint(time=0.0, is_visual=True, is_audio=True),
            CutPoint(time=4.0, is_visual=True, is_audio=True),
        )
    ]

    with_audio_enabled_but_absent = analyzer._score_segments_visual_only(
        unreadable, candidates, [], None, 10.0, enable_audio_content_analysis=True
    )
    with_audio_disabled = analyzer._score_segments_visual_only(
        unreadable, candidates, [], None, 10.0, enable_audio_content_analysis=False
    )

    assert with_audio_enabled_but_absent[0].total_score == with_audio_disabled[0].total_score
