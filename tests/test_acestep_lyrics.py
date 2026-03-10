"""Tests for ACE-Step lyrics/section-tag generation from video timeline."""

from immich_memories.audio.music_generator_models import ClipMood, VideoTimeline


class TestBuildAceStepLyrics:
    def test_single_clip_gets_verse(self):
        timeline = VideoTimeline(clips=[ClipMood(duration=60.0, mood="happy")])
        lyrics = timeline.build_acestep_lyrics()
        # Single clip: no Intro/Outro split, just section based on mood
        assert "[Instrumental]" in lyrics

    def test_multiple_clips_get_sections(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=15.0, mood="calm"),
                ClipMood(duration=30.0, mood="energetic"),
                ClipMood(duration=20.0, mood="happy"),
                ClipMood(duration=10.0, mood="calm"),
            ]
        )
        lyrics = timeline.build_acestep_lyrics()
        sections = [
            line
            for line in lyrics.split("\n")
            if line.startswith("[") and line.endswith("]") and line != "[Instrumental]"
        ]
        assert len(sections) >= 2

    def test_first_short_clip_gets_intro(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=10.0, mood="calm"),
                ClipMood(duration=30.0, mood="energetic"),
            ]
        )
        lyrics = timeline.build_acestep_lyrics()
        assert "[Intro]" in lyrics

    def test_last_clip_gets_outro(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=30.0, mood="energetic"),
                ClipMood(duration=10.0, mood="calm"),
            ]
        )
        lyrics = timeline.build_acestep_lyrics()
        assert "[Outro]" in lyrics

    def test_energetic_clip_gets_chorus(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=10.0, mood="calm"),
                ClipMood(duration=30.0, mood="energetic"),
                ClipMood(duration=10.0, mood="calm"),
            ]
        )
        lyrics = timeline.build_acestep_lyrics()
        assert "[Chorus]" in lyrics

    def test_transition_clips_get_bridge(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=20.0, mood="happy"),
                ClipMood(duration=10.0, mood="calm", has_transition_after=True),
                ClipMood(duration=20.0, mood="energetic"),
            ]
        )
        lyrics = timeline.build_acestep_lyrics()
        assert "[Bridge]" in lyrics

    def test_empty_timeline_returns_instrumental(self):
        timeline = VideoTimeline()
        lyrics = timeline.build_acestep_lyrics()
        assert "[Instrumental]" in lyrics

    def test_all_instrumental(self):
        timeline = VideoTimeline(
            clips=[
                ClipMood(duration=30.0, mood="happy"),
            ]
        )
        lyrics = timeline.build_acestep_lyrics()
        assert "[Instrumental]" in lyrics
        lines = [
            line.strip()
            for line in lyrics.split("\n")
            if line.strip() and not line.strip().startswith("[")
        ]
        assert len(lines) == 0, f"Unexpected non-tag lines: {lines}"
