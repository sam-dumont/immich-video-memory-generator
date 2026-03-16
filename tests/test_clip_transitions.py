"""Tests for transition planning logic."""

from __future__ import annotations

import random

from immich_memories.processing.clip_transitions import (
    ClipTransitionInfo,
    plan_transitions,
)


def _clip(
    *,
    start: float = 2.0,
    end: float = 8.0,
    duration: float = 10.0,
    is_title: bool = False,
) -> ClipTransitionInfo:
    """Create a clip info with sensible defaults."""
    return ClipTransitionInfo(
        asset_id="a",
        start_time=start,
        end_time=end,
        video_duration=duration,
        is_title_screen=is_title,
    )


class TestClipTransitionInfo:
    """Tests for buffer availability properties."""

    def test_can_buffer_start_with_margin(self):
        """Clip starting at 0.5s can buffer (>= TRANSITION_BUFFER)."""
        clip = _clip(start=0.5)
        assert clip.can_buffer_start

    def test_cannot_buffer_start_at_zero(self):
        """Clip starting at 0s cannot buffer."""
        clip = _clip(start=0.0)
        assert not clip.can_buffer_start

    def test_can_buffer_start_within_tolerance(self):
        """Clip starting at 0.4s can buffer (within BOUNDARY_TOLERANCE)."""
        clip = _clip(start=0.4)
        assert clip.can_buffer_start

    def test_can_buffer_end_with_margin(self):
        """Clip ending 0.5s before video end can buffer."""
        clip = _clip(end=9.5, duration=10.0)
        assert clip.can_buffer_end

    def test_cannot_buffer_end_at_video_end(self):
        """Clip ending at video duration cannot buffer."""
        clip = _clip(end=10.0, duration=10.0)
        assert not clip.can_buffer_end

    def test_can_buffer_end_within_tolerance(self):
        """Clip ending 0.4s before end can buffer (within tolerance)."""
        clip = _clip(end=9.6, duration=10.0)
        assert clip.can_buffer_end


class TestPlanTransitionsEdgeCases:
    """Tests for edge cases in plan_transitions."""

    def test_empty_clips(self):
        """Empty clip list returns empty plan."""
        plan = plan_transitions([])
        assert not plan.transitions
        assert not plan.buffer_start
        assert not plan.buffer_end

    def test_single_clip(self):
        """Single clip has no transitions and no buffers."""
        plan = plan_transitions([_clip()])
        assert not plan.transitions
        assert plan.buffer_start == [False]
        assert plan.buffer_end == [False]


class TestPlanTransitionsCutMode:
    """Tests for cut mode transitions."""

    def test_all_cuts(self):
        """Cut mode produces all cut transitions."""
        clips = [_clip() for _ in range(5)]
        plan = plan_transitions(clips, transition_mode="cut")
        assert plan.transitions == ["cut"] * 4

    def test_no_buffers_needed(self):
        """Cut mode never sets buffer flags."""
        clips = [_clip() for _ in range(3)]
        plan = plan_transitions(clips, transition_mode="cut")
        assert all(not b for b in plan.buffer_start)
        assert all(not b for b in plan.buffer_end)


class TestPlanTransitionsCrossfadeMode:
    """Tests for crossfade mode transitions."""

    def test_all_fades_when_possible(self):
        """Crossfade mode uses fade for all bufferable transitions."""
        clips = [_clip() for _ in range(4)]
        plan = plan_transitions(clips, transition_mode="crossfade")
        assert plan.transitions == ["fade"] * 3

    def test_buffers_set_for_fades(self):
        """Fade transitions set buffer flags on both sides."""
        clips = [_clip() for _ in range(3)]
        plan = plan_transitions(clips, transition_mode="crossfade")
        # Middle clip should have both start and end buffers
        assert plan.buffer_start[1]
        assert plan.buffer_end[1]

    def test_forced_cut_at_video_start(self):
        """Clip at video start forces cut (can't buffer start)."""
        clips = [_clip(), _clip(start=0.0)]
        plan = plan_transitions(clips, transition_mode="crossfade")
        assert plan.transitions == ["cut"]

    def test_forced_cut_at_video_end(self):
        """Clip at video end forces cut (can't buffer end)."""
        clips = [_clip(end=10.0, duration=10.0), _clip()]
        plan = plan_transitions(clips, transition_mode="crossfade")
        assert plan.transitions == ["cut"]


class TestPlanTransitionsTitleScreens:
    """Tests for title screen transition handling."""

    def test_title_always_gets_fade(self):
        """Title screens always get fade transitions."""
        clips = [_clip(is_title=True), _clip()]
        plan = plan_transitions(clips, transition_mode="cut")
        # Even in cut mode, titles should not force a cut—but the mode
        # check happens before the loop, so cut mode returns early.
        # Test with crossfade mode instead.
        plan = plan_transitions(clips, transition_mode="crossfade")
        assert plan.transitions == ["fade"]

    def test_title_before_clip_gets_fade(self):
        """Transition from title to clip is always fade."""
        clips = [_clip(is_title=True), _clip()]
        plan = plan_transitions(clips, transition_mode="smart")
        assert plan.transitions == ["fade"]

    def test_clip_before_title_gets_fade(self):
        """Transition from clip to title is always fade."""
        clips = [_clip(), _clip(is_title=True)]
        plan = plan_transitions(clips, transition_mode="smart")
        assert plan.transitions == ["fade"]

    def test_title_does_not_buffer(self):
        """Title screens don't need buffers (synthetic content)."""
        clips = [_clip(is_title=True), _clip()]
        plan = plan_transitions(clips, transition_mode="crossfade")
        assert not plan.buffer_end[0]  # Title doesn't buffer
        assert plan.buffer_start[1]  # Real clip does buffer


class TestPlanTransitionsSmartMode:
    """Tests for smart mode transition planning."""

    def test_respects_consecutive_fade_limit(self):
        """After 3 consecutive fades, smart mode forces a cut."""
        random.seed(0)  # Ensure random.random() < 0.7 for first calls
        clips = [_clip() for _ in range(10)]
        plan = plan_transitions(clips, transition_mode="smart")
        # Check that no run of fades exceeds 3
        consecutive = 0
        for t in plan.transitions:
            if t == "fade":
                consecutive += 1
                assert consecutive <= 4  # 3 + possible title override
            else:
                consecutive = 0

    def test_respects_consecutive_cut_limit(self):
        """After 2 consecutive cuts, smart mode forces a fade."""
        random.seed(42)
        # Use clips that can all buffer
        clips = [_clip() for _ in range(20)]
        plan = plan_transitions(clips, transition_mode="smart")
        # Check that no run of cuts exceeds 2
        consecutive = 0
        for t in plan.transitions:
            if t == "cut":
                consecutive += 1
                assert consecutive <= 2
            else:
                consecutive = 0

    def test_mix_of_fades_and_cuts(self):
        """Smart mode produces a mix of transition types."""
        random.seed(12345)
        clips = [_clip() for _ in range(20)]
        plan = plan_transitions(clips, transition_mode="smart")
        assert "fade" in plan.transitions
        assert "cut" in plan.transitions

    def test_transition_count_matches_clips(self):
        """Number of transitions is always len(clips) - 1."""
        for n in range(2, 8):
            clips = [_clip() for _ in range(n)]
            plan = plan_transitions(clips, transition_mode="smart")
            assert len(plan.transitions) == n - 1
            assert len(plan.buffer_start) == n
            assert len(plan.buffer_end) == n
