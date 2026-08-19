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


class TestStemsAreLoopedToo:
    """Stem ducking trimmed stems to the video length with no loop call, so short
    music stopped partway and the rest of the video was silent."""

    def test_short_stems_are_extended_to_cover_the_video(self, tmp_path, monkeypatch):
        from immich_memories.audio import mixer_helpers

        looped: list[tuple[str, float]] = []

        # WHY: replaces the FFmpeg loop pass; the routing is what is under test.
        def _fake_loop(path, target, output=None, crossfade_seconds=2.0):  # noqa: ARG001
            looped.append((path.name, target))
            return path

        monkeypatch.setattr(mixer_helpers, "loop_audio_to_duration", _fake_loop)
        monkeypatch.setattr(mixer_helpers, "get_video_duration", lambda _p: 120.0)
        monkeypatch.setattr(mixer_helpers, "get_audio_duration", lambda _p: 30.0)

        stems = mixer_helpers.stems_covering(
            [tmp_path / "vocals.wav", tmp_path / "accompaniment.wav"], 120.0
        )

        assert [name for name, _ in looped] == ["vocals.wav", "accompaniment.wav"]
        assert all(target == 120.0 for _, target in looped)
        assert len(stems) == 2

    def test_stems_already_long_enough_are_untouched(self, tmp_path, monkeypatch):
        from immich_memories.audio import mixer_helpers

        called: list[str] = []

        def _record(path, _target, output=None, crossfade_seconds=2.0):  # noqa: ARG001
            called.append(path.name)
            return path

        monkeypatch.setattr(mixer_helpers, "loop_audio_to_duration", _record)
        monkeypatch.setattr(mixer_helpers, "get_audio_duration", lambda _p: 200.0)

        mixer_helpers.stems_covering([tmp_path / "vocals.wav"], 120.0)

        assert called == []
