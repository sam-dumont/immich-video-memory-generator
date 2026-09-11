"""Sampling frames from a video happens one way, and only once per video.

It was written three times: the mood analyser at scale=512, the title colour
sampler at scale=320, and the analysis preview builder. The first two are the
same algorithm — probe the duration, take duration*i/(n+1) evenly spaced
timestamps, run one ffmpeg per frame — and neither cached anything, so every
run re-decoded the same video forever.
"""

from pathlib import Path
from unittest.mock import patch

from immich_memories.processing.frame_sampling import (
    even_timestamps,
    sample_frames,
    sample_segment_frames,
)


def test_frames_are_spread_evenly_across_the_video() -> None:
    """The distribution both copies used, in one place."""
    assert even_timestamps(duration=60.0, count=3) == [15.0, 30.0, 45.0]


def test_a_video_of_unknown_length_still_yields_the_asked_for_count() -> None:
    """Duration probing fails on odd containers; a caller still needs frames."""
    assert len(even_timestamps(duration=0.0, count=4)) == 4


def test_the_same_video_is_only_decoded_once(tmp_path) -> None:
    """The whole point: a second ask costs nothing."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    calls: list = []

    def _fake_extract(_video, timestamp, width, out_path):
        calls.append(timestamp)
        out_path.write_bytes(b"jpeg")
        return True

    # WHY: ffmpeg is the external boundary — decoding is the cost being counted.
    with patch("immich_memories.processing.frame_sampling._extract_one", new=_fake_extract):
        first = sample_frames(video, count=3, width=512, cache_dir=tmp_path / "frames")
        second = sample_frames(video, count=3, width=512, cache_dir=tmp_path / "frames")

    assert len(first) == 3
    assert first == second
    assert len(calls) == 3, f"the video was decoded again: {len(calls)} extractions"


def test_a_different_width_is_a_different_ask(tmp_path) -> None:
    """A colour palette wants 320px and a vision model wants 512; both are valid."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    calls: list = []

    def _fake_extract(_video, timestamp, width, out_path):
        calls.append((timestamp, width))
        out_path.write_bytes(b"jpeg")
        return True

    # WHY: ffmpeg is the external boundary.
    with patch("immich_memories.processing.frame_sampling._extract_one", new=_fake_extract):
        sample_frames(video, count=2, width=512, cache_dir=tmp_path / "frames")
        sample_frames(video, count=2, width=320, cache_dir=tmp_path / "frames")

    assert len({w for _, w in calls}) == 2, "one width served the other's frames"


def test_segment_cache_identity_changes_for_bounds_and_render_version(tmp_path) -> None:
    """Filmstrip frames never survive a change to the portion or pixel recipe."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    calls: list[float] = []

    def _fake_extract(_video, timestamp, _width, out_path):
        calls.append(timestamp)
        out_path.write_bytes(b"jpeg")
        return True

    # WHY: FFmpeg extraction is external; cache identity is the behavior under test.
    with patch("immich_memories.processing.frame_sampling._extract_one", new=_fake_extract):
        first = sample_segment_frames(
            video, start_time=0, end_time=2, count=2, width=320, cache_dir=tmp_path / "frames"
        )
        same = sample_segment_frames(
            video, start_time=0, end_time=2, count=2, width=320, cache_dir=tmp_path / "frames"
        )
        changed_segment = sample_segment_frames(
            video, start_time=1, end_time=2, count=2, width=320, cache_dir=tmp_path / "frames"
        )
        changed_end = sample_segment_frames(
            video, start_time=0, end_time=3, count=2, width=320, cache_dir=tmp_path / "frames"
        )
        changed_render = sample_segment_frames(
            video,
            start_time=0,
            end_time=2,
            count=2,
            width=320,
            cache_dir=tmp_path / "frames",
            render_version="new-filmstrip",
        )

    assert first == same
    assert changed_segment != first
    assert changed_end != first
    assert changed_render != first
    assert len(calls) == 8


def test_segment_sampler_rejects_partial_and_empty_video_inputs(tmp_path) -> None:
    """An in-flight download or empty placeholder must never be sent to FFmpeg."""
    partial = tmp_path / "clip.mp4.part"
    empty = tmp_path / "empty.mp4"
    partial.write_bytes(b"in flight")
    empty.touch()

    # WHY: FFmpeg is external and must not run for rejected cache entries.
    with patch("immich_memories.processing.frame_sampling._extract_one") as extract:
        assert (
            sample_segment_frames(
                partial, start_time=0, end_time=1, count=1, width=320, cache_dir=tmp_path
            )
            == ()
        )
        assert (
            sample_segment_frames(
                empty, start_time=0, end_time=1, count=1, width=320, cache_dir=tmp_path
            )
            == ()
        )

    extract.assert_not_called()


def test_a_cache_that_cannot_be_written_still_returns_frames(tmp_path) -> None:
    """Losing the cache costs decodes, never the run."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")

    def _fake_extract(_video, timestamp, width, out_path):
        out_path.write_bytes(b"jpeg")
        return True

    # WHY: ffmpeg is the external boundary.
    with patch("immich_memories.processing.frame_sampling._extract_one", new=_fake_extract):
        frames = sample_frames(video, count=2, width=512, cache_dir=None)

    assert len(frames) == 2
    assert all(isinstance(f, Path) for f in frames)


def test_a_video_that_will_not_probe_reports_no_duration(tmp_path) -> None:
    """ffprobe fails on odd containers; even_timestamps has a fallback for it."""
    from immich_memories.processing.frame_sampling import probe_duration

    assert probe_duration(tmp_path / "does-not-exist.mp4") == 0.0


def test_a_frame_that_will_not_extract_is_left_out(tmp_path) -> None:
    """A video that yields nothing returns an empty list, not an exception.

    A caller that cannot see the pictures should degrade, not fail.
    """
    from immich_memories.processing.frame_sampling import _extract_one, sample_frames

    missing = tmp_path / "not-a-video.mp4"
    missing.write_bytes(b"nope")

    assert _extract_one(missing, 1.0, 320, tmp_path / "out.jpg") is False
    assert sample_frames(missing, count=2, width=320, cache_dir=tmp_path / "c") == []


def test_the_mood_analyser_asks_for_vision_sized_frames(tmp_path) -> None:
    """512px, and through the shared sampler rather than its own ffmpeg loop."""
    from immich_memories.audio.mood_analyzer import MoodAnalyzer

    class _Concrete(MoodAnalyzer):
        """extract_keyframes is concrete on the base; the two abstracts are not."""

        async def analyze_video(self, *_a, **_k):  # pragma: no cover - not exercised
            raise NotImplementedError

        async def analyze_frames(self, *_a, **_k):  # pragma: no cover - not exercised
            raise NotImplementedError

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"pretend")
    seen: dict = {}

    def _fake(_video, *, count, width, cache_dir):
        seen.update(count=count, width=width)
        return [tmp_path / "f0.jpg"]

    # WHY: frame extraction is the ffmpeg boundary; the ask is what is tested.
    with patch("immich_memories.processing.frame_sampling.sample_frames", new=_fake):
        _Concrete().extract_keyframes(video, num_frames=4, output_dir=tmp_path)

    assert seen == {"count": 4, "width": 512}


def test_the_palette_sampler_asks_for_smaller_frames(tmp_path) -> None:
    """320px: a colour histogram does not need what a vision model needs."""
    from PIL import Image

    from immich_memories.titles.colors import extract_keyframes_from_video

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"pretend")
    frame = tmp_path / "f0.jpg"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(frame)
    seen: dict = {}

    def _fake(_video, *, count, width, cache_dir):
        seen.update(count=count, width=width)
        return [frame]

    # WHY: frame extraction is the ffmpeg boundary.
    with patch("immich_memories.processing.frame_sampling.sample_frames", new=_fake):
        images = extract_keyframes_from_video(video, count=3)

    assert seen == {"count": 3, "width": 320}
    assert len(images) == 1


def test_a_frame_that_will_not_open_is_skipped_not_fatal(tmp_path) -> None:
    """A truncated jpeg loses one frame, not the palette."""
    from immich_memories.titles.colors import extract_keyframes_from_video

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"pretend")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")

    # WHY: frame extraction is the ffmpeg boundary.
    with patch("immich_memories.processing.frame_sampling.sample_frames", return_value=[broken]):
        assert extract_keyframes_from_video(video, count=1) == []
