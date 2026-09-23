"""A video reaches the exposure head as frames across its length, a still as its preview."""

import shutil
from dataclasses import replace

import pytest

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _wiring(tmp_path, read_playback):
    """Run one pass over a still and a video, recording what the detector seam was handed.

    The tier has no caption seat, so the motion line is not owed and the only producer
    reaching for the playback is the exposure head's frame sampler.
    """
    from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
    from tests.test_editorial_preparation import asset, preview, run, successful_ports
    from tests.test_editorial_preparation_motion import prepared_video

    calls = []
    ports = successful_ports(calls)
    handed = {}

    def detectors(**kwargs):
        # Recorded while the seam holds them: the frames live only for the call.
        handed.update(
            {key: [path.exists() for path in paths] for key, paths in kwargs["frame_paths"].items()}
        )
        return ports.detectors(**kwargs)

    result = run(
        tmp_path,
        assets=[asset("aa1"), prepared_video("vv1")],
        ports=replace(ports, detectors=detectors),
        preparation_config=EditorialPreparationConfig(tier="no_captions"),
        fetch_preview=lambda _: preview(),
        read_playback=read_playback,
    )
    return result, handed


@requires_ffmpeg
def test_the_pass_samples_a_videos_frames_and_leaves_a_still_its_preview(tmp_path):
    from immich_memories.analysis.editorial_preparation_detector_frames import FRAMES
    from tests.test_playback_keyframes import encode

    data = encode(tmp_path / "clip.mp4", gop=30)

    # WHY: the Immich playback endpoint; everything past it is the real sampler and FFmpeg.
    def read(_playback_id, start, length):
        return data[start : start + length], len(data)

    result, handed = _wiring(tmp_path, read)

    assert result.complete
    assert set(handed) == {"vv1"}
    # Up to eight, at the keyframes nearest eight evenly spaced moments: a clip with
    # fewer keyframes than that offers fewer, and none is read twice.
    assert 2 <= len(handed["vv1"]) <= FRAMES
    assert all(handed["vv1"])
    assert result.pictures_by_stage["detector_frames"] == 1


def test_a_clip_whose_playback_cannot_be_read_falls_back_to_its_preview_and_says_so(tmp_path):
    # WHY: the Immich playback endpoint, refusing the way a dropped connection does.
    def read(*_args):
        raise OSError("connection reset")

    result, handed = _wiring(tmp_path, read)

    assert handed == {}
    assert result.failures["detector_frames:vv1"] == "OSError: connection reset"
    # The cut is not blocked by it, and the per-clip note is not a dead producer.
    assert result.complete and result.producer_failures == ()


def test_without_a_playback_reader_no_source_is_owed_frames(tmp_path):
    from immich_memories.analysis.editorial_preparation_detector_frames import DetectorFrames
    from tests.test_editorial_preparation_motion import prepared_video

    assert DetectorFrames([prepared_video("vv1")], None).video_ids == frozenset()


def _live_photo(asset_id, clip_id):
    """A still with a clip hanging off it, the way Immich reports a Live Photo."""
    from tests.test_editorial_preparation import asset

    return asset(asset_id).model_copy(update={"live_photo_video_id": clip_id})


@requires_ffmpeg
def test_a_live_photos_clip_is_read_on_frames_and_banked_under_its_own_id(tmp_path):
    """The clip is not a candidate, so nothing prepared it and nothing had ever read it."""
    import sqlite3

    from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
    from tests.test_editorial_preparation import asset, preview, run, successful_ports
    from tests.test_playback_keyframes import encode

    data = encode(tmp_path / "clip.mp4", gop=30)
    calls = []
    ports = successful_ports(calls)

    result = run(
        tmp_path,
        assets=[asset("aa1"), _live_photo("bb2", "cc3")],
        ports=ports,
        preparation_config=EditorialPreparationConfig(tier="no_captions"),
        fetch_preview=lambda _: preview(),
        # WHY: the Immich playback endpoint; the sampler and FFmpeg past it are real.
        read_playback=lambda _id, start, length: (data[start : start + length], len(data)),
    )

    assert result.complete
    detector_calls = [tuple(sorted(pending)) for name, pending in calls if name == "detectors"]
    # The clip's pass asks for the exposure head alone: no caption, no context head.
    assert detector_calls[-1] == ("nsfw_marqo",)
    with sqlite3.connect(tmp_path / "annotations.sqlite") as connection:
        banked = {
            row[0]
            for row in connection.execute("SELECT asset_id FROM head_facts WHERE head='nsfw_marqo'")
        }
    assert "cc3" in banked
    # The clip is not a candidate: nothing else is owed for it.
    assert not connection.execute(
        "SELECT 1 FROM head_facts WHERE asset_id='cc3' AND head!='nsfw_marqo'"
    ).fetchone()
    assert result.pictures_by_stage["detector_frames"] == 1


@requires_ffmpeg
def test_a_warm_pass_does_not_read_the_same_clip_twice(tmp_path):
    from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
    from tests.test_editorial_preparation import asset, preview, run, successful_ports
    from tests.test_playback_keyframes import encode

    data = encode(tmp_path / "clip.mp4", gop=30)

    def once(**kwargs):
        calls = []
        return run(
            tmp_path,
            assets=[asset("aa1"), _live_photo("bb2", "cc3")],
            ports=successful_ports(calls),
            preparation_config=EditorialPreparationConfig(tier="no_captions"),
            fetch_preview=lambda _: preview(),
            # WHY: the Immich playback endpoint; the sampler and FFmpeg past it are real.
            read_playback=lambda _id, start, length: (data[start : start + length], len(data)),
            **kwargs,
        ), calls

    cold, _ = once()
    warm, warm_calls = once()

    assert cold.pictures_by_stage["detector_frames"] == 1
    assert "detector_frames" not in warm.pictures_by_stage
    assert warm.complete and warm_calls == []


def test_a_clip_immich_will_not_serve_leaves_its_still_in_the_film(tmp_path):
    import httpx

    from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
    from tests.test_editorial_preparation import asset, preview, run, successful_ports

    def fetch_preview(asset_id):
        if asset_id == "cc3":
            # WHY: Immich's settled answer for a source it holds no preview for.
            raise httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "http://immich.test"),
                response=httpx.Response(404),
            )
        return preview()

    result = run(
        tmp_path,
        assets=[asset("aa1"), _live_photo("bb2", "cc3")],
        ports=successful_ports([]),
        preparation_config=EditorialPreparationConfig(tier="no_captions"),
        fetch_preview=fetch_preview,
        read_playback=lambda *_: (b"", 0),
    )

    assert result.complete
    assert result.failures["clip_companion:cc3"] == "preview unavailable at Immich (HTTP 404)"
    # The still stays: a clip nobody could read is what every Live Photo had before.
    assert result.unservable_sources == {}
