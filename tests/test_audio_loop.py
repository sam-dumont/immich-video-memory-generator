"""Looping music to a video's runtime without an audible seam."""

from __future__ import annotations

from immich_memories.audio.mixer import plan_loop_copies


class TestPlanLoopCopies:
    def test_a_track_shorter_than_the_target_repeats_enough_times(self):
        """Each crossfade eats `crossfade` seconds of overlap, so copies must cover it."""
        # 30 s track, 2 s crossfade -> each extra copy adds 28 s. 100 s needs 4 copies (114 s).
        assert plan_loop_copies(audio_duration=30.0, target_duration=100.0, crossfade=2.0) == 4

    def test_two_copies_are_enough_when_one_repeat_covers_it(self):
        assert plan_loop_copies(audio_duration=30.0, target_duration=45.0, crossfade=2.0) == 2

    def test_a_track_that_already_covers_the_target_is_not_repeated(self):
        assert plan_loop_copies(audio_duration=120.0, target_duration=60.0, crossfade=2.0) == 1

    def test_a_track_shorter_than_the_crossfade_still_terminates(self):
        """A 1 s sting with a 2 s crossfade must not demand infinite copies."""
        copies = plan_loop_copies(audio_duration=1.0, target_duration=60.0, crossfade=2.0)

        assert 1 < copies < 1000
