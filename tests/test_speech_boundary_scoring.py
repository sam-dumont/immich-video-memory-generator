"""Tests for boundary candidate ranking. Pure functions, no mocks."""

from __future__ import annotations

from immich_memories.speech.boundary_scoring import (
    BoundaryWeights,
    best_boundary,
    candidates_from_gaps,
)


class TestCandidatesFromGaps:
    def test_short_gaps_are_rejected(self):
        candidates = candidates_from_gaps([(0.0, 0.1), (1.0, 2.0)], min_gap=0.3)

        assert len(candidates) == 1
        assert candidates[0].gap_start == 1.0

    def test_candidate_time_is_the_gap_midpoint(self):
        candidates = candidates_from_gaps([(2.0, 3.0)], min_gap=0.3)

        assert candidates[0].time == 2.5


class TestBestBoundary:
    def test_prefers_the_wider_gap(self):
        candidates = candidates_from_gaps([(1.0, 1.4), (4.0, 6.0)], min_gap=0.3)

        chosen = best_boundary(candidates, target_time=3.0, max_shift=5.0)

        assert chosen.gap_start == 4.0

    def test_completion_score_is_ignored_by_default(self):
        """gap_width carries the ranking alone -- BoundaryWeights.completion is 0.0.

        Both gaps are the same width, so if completion score had any default
        influence the 0.95-scoring candidate would win. It doesn't: the tie
        goes to the first candidate exactly as an unweighted comparison would.
        """
        candidates = candidates_from_gaps([(1.0, 2.0), (4.0, 5.0)], min_gap=0.3)
        candidates[0].completion_score = 0.1
        candidates[1].completion_score = 0.95

        chosen = best_boundary(candidates, target_time=3.0, max_shift=5.0)

        assert chosen.gap_start == 1.0

    def test_completion_score_breaks_a_tie_when_weighted(self):
        """The completion signal still works when explicitly weighted -- it's wired, not dead."""
        candidates = candidates_from_gaps([(1.0, 2.0), (4.0, 5.0)], min_gap=0.3)
        candidates[0].completion_score = 0.1
        candidates[1].completion_score = 0.95
        weights = BoundaryWeights(gap=1.0, completion=1.0)

        chosen = best_boundary(candidates, target_time=3.0, max_shift=5.0, weights=weights)

        assert chosen.gap_start == 4.0

    def test_candidates_beyond_max_shift_are_ignored(self):
        candidates = candidates_from_gaps([(50.0, 60.0)], min_gap=0.3)

        assert best_boundary(candidates, target_time=1.0, max_shift=5.0) is None

    def test_no_candidates_returns_none(self):
        assert best_boundary([], target_time=1.0, max_shift=5.0) is None
