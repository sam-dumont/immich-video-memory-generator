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


class TestSelectSegmentBoundaries:
    def test_segment_inside_speech_is_pulled_to_a_real_gap(self):
        """The old code gave up here and returned the segment unchanged."""
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        gaps = [(0.0, 1.0), (5.0, 6.0), (9.0, 10.0)]

        start, end, adjusted = select_segment_boundaries(
            start=2.0, end=8.0, gaps=gaps, video_duration=10.0, min_segment_duration=1.0
        )

        assert adjusted is True
        assert start == 0.5
        assert end == 9.5

    def test_adjustment_that_would_undershoot_minimum_is_rejected(self):
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        gaps = [(4.0, 5.0), (5.5, 6.0)]

        start, end, adjusted = select_segment_boundaries(
            start=4.2, end=5.8, gaps=gaps, video_duration=10.0, min_segment_duration=3.0
        )

        assert adjusted is False
        assert (start, end) == (4.2, 5.8)

    def test_no_usable_gaps_leaves_the_segment_alone(self):
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        start, end, adjusted = select_segment_boundaries(
            start=2.0, end=8.0, gaps=[], video_duration=10.0, min_segment_duration=1.0
        )

        assert adjusted is False
        assert (start, end) == (2.0, 8.0)

    def test_edges_move_inward_when_silence_exists_on_both_sides(self):
        """The point of the task: a reachable gap on each side, and the clip shrinks.

        Both directions are safe here, so nothing but the trimming preference
        decides it. Growing would pick 1.75 and 14.5 instead.
        """
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        gaps = [(1.0, 2.0), (4.0, 5.0), (11.0, 12.0), (14.0, 15.0)]

        start, end, adjusted = select_segment_boundaries(
            start=3.5, end=12.5, gaps=gaps, video_duration=16.0, min_segment_duration=1.0
        )

        assert adjusted is True
        assert (start, end) == (4.5, 11.5)
        assert end - start < 12.5 - 3.5

    def test_a_long_silence_is_reachable_by_its_near_edge(self):
        """Ranking whole gaps would reject this: the gap's midpoint is 10.5s away."""
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        gaps = [(0.0, 1.0), (10.0, 30.0)]

        start, end, adjusted = select_segment_boundaries(
            start=9.5, end=28.0, gaps=gaps, video_duration=30.0, min_segment_duration=1.0
        )

        assert adjusted is True
        assert start == 10.75
        # The end is already 2s deep in that silence, so it stays put.
        assert end == 28.0

    def test_a_boundary_already_in_a_gap_is_left_alone(self):
        """The inward window is inclusive of the target, so re-clipping the gap it
        already sits in yields a midpoint past it. Left unchecked that pushes a
        safe boundary toward its gap's far edge -- burning the margin for nothing.
        """
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        gaps = [(4.0, 5.0), (10.0, 11.0)]

        start, end, adjusted = select_segment_boundaries(
            start=4.5, end=10.5, gaps=gaps, video_duration=15.0, min_segment_duration=1.0
        )

        assert adjusted is False
        assert (start, end) == (4.5, 10.5)

    def test_selection_is_idempotent(self):
        """The pipeline runs this twice -- once per candidate, once on the winner.

        Without idempotence the second pass erodes what the first established:
        4.5 -> 4.75 -> 4.875, each step closer to the speech at 5.0.
        """
        from immich_memories.analysis.boundary_placement import select_segment_boundaries

        gaps = [(1.0, 2.0), (4.0, 5.0), (11.0, 12.0)]

        first_start, first_end, _ = select_segment_boundaries(
            start=3.5, end=12.5, gaps=gaps, video_duration=16.0, min_segment_duration=1.0
        )
        second_start, second_end, adjusted_again = select_segment_boundaries(
            start=first_start,
            end=first_end,
            gaps=gaps,
            video_duration=16.0,
            min_segment_duration=1.0,
        )

        assert (second_start, second_end) == (first_start, first_end)
        assert adjusted_again is False


class TestAdjustCandidatesForAudioProportionalMax:
    def test_oversized_segment_snaps_cap_to_a_real_gap(self):
        """The proportional-max cap used to be pure arithmetic: new_start + proportional_max,
        with no regard for what sits at that time. Here the raw cap lands inside the
        12-25s protected range. The cap must instead land on the best real gap at or before
        it -- the 9-12s gap, whose midpoint is 10.5s. The start is already deep in the
        opening silence and stays where it is.
        """
        from immich_memories.analysis.analyzer_models import CutPoint
        from immich_memories.analysis.segment_generation import adjust_candidates_for_audio
        from immich_memories.audio.audio_models import AudioAnalysisResult

        audio_result = AudioAnalysisResult(events=[], protected_ranges=[(5.0, 9.0), (12.0, 25.0)])
        start_cp = CutPoint(time=2.0, is_visual=True)
        end_cp = CutPoint(time=20.0, is_visual=True)

        adjusted = adjust_candidates_for_audio(
            [(start_cp, end_cp)],
            audio_result,
            video_duration=30.0,
            min_segment_duration=1.0,
            proportional_max=10.0,
            min_silence_ms=200,
        )

        assert len(adjusted) == 1
        new_start, new_end = adjusted[0]
        assert (new_start.time, new_end.time) == (2.0, 10.5)
        assert new_end.time <= new_start.time + 10.0


class TestCapEndToGap:
    def test_a_usable_gap_near_the_cap_beats_a_wider_one_far_from_it(self):
        """max_shift used to be the whole window, so every gap was reachable and
        width alone decided it. That traded 6.3 seconds of clip for 1.4 seconds
        of extra silence: the 2s-wide gap at 3.0s won over the 0.6s one at 9.3s,
        turning a 10-second allowance into a 3-second clip.
        """
        from immich_memories.analysis.boundary_placement import cap_end_to_gap

        end = cap_end_to_gap(
            new_start=0.0,
            cap_time=10.0,
            gaps=[(2.0, 4.0), (9.0, 9.6)],
            min_segment_duration=2.0,
        )

        assert end == 9.3

    def test_a_distant_gap_still_beats_a_speech_blind_cap(self):
        """Nothing near the cap, so the whole window competes again -- a cut
        seconds early is still better than one mid-word at the cap itself.
        """
        from immich_memories.analysis.boundary_placement import cap_end_to_gap

        end = cap_end_to_gap(
            new_start=0.0, cap_time=10.0, gaps=[(2.0, 4.0)], min_segment_duration=2.0
        )

        assert end == 3.0


class TestScoreCompletions:
    def test_scores_below_threshold_are_zeroed(self):
        import numpy as np

        from immich_memories.speech.boundary_scoring import (
            candidates_from_gaps,
            score_completions,
        )

        class _StubDetector:
            # WHY: stands in for the smart-turn-v3 ONNX session, an optional
            # model download that is absent in CI.
            def completion_probability(self, audio, sample_rate):
                return 0.4

        candidates = candidates_from_gaps([(1.0, 2.0)], min_gap=0.3)
        score_completions(
            candidates,
            np.zeros(16000 * 5, dtype=np.float32),
            16000,
            _StubDetector(),
            threshold=0.85,
        )

        assert candidates[0].completion_score == 0.0

    def test_scores_at_or_above_threshold_are_kept(self):
        import numpy as np

        from immich_memories.speech.boundary_scoring import (
            candidates_from_gaps,
            score_completions,
        )

        class _StubDetector:
            # WHY: stands in for the smart-turn-v3 ONNX session, an optional
            # model download that is absent in CI.
            def completion_probability(self, audio, sample_rate):
                return 0.93

        candidates = candidates_from_gaps([(1.0, 2.0)], min_gap=0.3)
        score_completions(
            candidates,
            np.zeros(16000 * 5, dtype=np.float32),
            16000,
            _StubDetector(),
            threshold=0.85,
        )

        assert candidates[0].completion_score == 0.93
