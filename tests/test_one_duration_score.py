"""Duration scoring is one rule, so there is one implementation of it.

It was written twice — analysis/scoring.py as a pure function taking its
parameters, and UnifiedSegmentAnalyzer._compute_duration_score as a method
reading the same values off self. Same Gaussian, same 0.3x floor penalty
below the minimum, same sigma, same >15s long-clip penalty. Two call sites,
two copies, nothing connecting them: tune one and the other silently keeps
the old rule, and which one a clip meets depends on the path it took.
"""

from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.scoring import compute_duration_score
from immich_memories.config_loader import Config

_SOURCES = (5.0, 15.0, 21.0, 60.0, 120.0, 600.0)
_CLIPS = (0.5, 1.9, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 16.0, 25.0, 40.0)


def _analyzer():
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer

    config = Config()
    return UnifiedSegmentAnalyzer(
        scorer=MagicMock(),
        audio_content_config=config.audio_content,
        analysis_config=config.analysis,
    )


@pytest.mark.parametrize("source", _SOURCES)
def test_the_analyzer_scores_a_duration_the_same_way_the_scorer_does(source: float) -> None:
    """The two agreed exactly when this was written; they must not drift apart."""
    analyzer = _analyzer()

    for clip in _CLIPS:
        assert analyzer._compute_duration_score(clip, source) == pytest.approx(
            compute_duration_score(
                clip,
                source,
                analyzer.optimal_clip_duration,
                analyzer.max_optimal_duration,
                analyzer.target_extraction_ratio,
                analyzer.min_segment_duration,
            )
        ), f"duration scoring disagreed at source={source}s clip={clip}s"


def test_a_clip_under_the_minimum_is_penalised_not_zeroed() -> None:
    """Behaviour worth pinning while both copies are in play."""
    analyzer = _analyzer()
    below = analyzer.min_segment_duration / 2

    assert 0.0 < analyzer._compute_duration_score(below, 60.0) < 0.3


def test_a_very_long_clip_keeps_a_floor() -> None:
    """The >15s penalty subtracts, but never below 0.2."""
    assert _analyzer()._compute_duration_score(120.0, 600.0) >= 0.2
