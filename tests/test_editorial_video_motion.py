"""A motion sentence on a real video counts only where its frames measure motion (#1166).

The fixtures are generated: a flat "room" drawn in a few grey bands, and the same room with a
bright figure crossing it, written as the JPEG frames the exposure head's sampler hands over.
The residual is the production optical-flow measurement run on those frames.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from immich_memories.analysis.editorial_structure_budget import RESIDUAL_MIN
from immich_memories.analysis.editorial_video_motion import (
    STAGE,
    VIDEO_RESIDUAL_PRODUCER,
    measure_frame_motion,
)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def room_frames(directory: Path, *, figure: bool, count: int = 8) -> list[Path]:
    """Eight frames of an empty room; with `figure`, somebody walks across it."""
    import cv2
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        frame = np.full((432, 768, 3), 180, dtype=np.uint8)
        frame[290:, :] = 120  # floor
        frame[200:300, 80:260] = 60  # sofa
        if figure:
            x = 200 + index * 40
            frame[100:400, x - 80 : x] = 230
            frame[160:220, x - 60 : x - 20] = 90
        path = directory / f"detector-{index:02d}.jpg"
        cv2.imwrite(str(path), frame)
        paths.append(path)
    return paths


def test_a_still_room_measures_below_the_bar_and_a_walk_across_it_above(tmp_path):
    still = measure_frame_motion(room_frames(tmp_path / "still", figure=False))
    walk = measure_frame_motion(room_frames(tmp_path / "walk", figure=True))

    assert still["residual"] < RESIDUAL_MIN <= walk["residual"]
    assert still["frames"] == walk["frames"] == 8


def test_fewer_than_two_readable_frames_measure_nothing(tmp_path):
    (one,) = room_frames(tmp_path, figure=True, count=1)

    assert measure_frame_motion([one, tmp_path / "missing.jpg"]) == {
        "unreadable": True,
        "frames": 1,
    }


def static_room_clip(path: Path) -> bytes:
    """Six seconds of a still room, encoded the way Immich serves a playback."""
    import subprocess

    subprocess.run(  # noqa: S603 - fixed argv in a test
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=0xb4b4b4:size=640x360:rate=30:duration=6",
            "-vf", "drawbox=x=60:y=150:w=200:h=90:color=0x3c3c3c:t=fill,"
            "drawbox=x=0:y=240:w=640:h=120:color=0x787878:t=fill",
            "-c:v", "mpeg4", "-b:v", "3M", "-g", "30", "-movflags", "+faststart", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path.read_bytes()


def clip_video(asset_id: str, *, favourite: bool = False):
    from tests.test_editorial_preparation_motion import prepared_video

    return prepared_video(asset_id).model_copy(
        update={"duration_seconds": 6.0, "is_favorite": favourite}
    )


def prepare_videos(tmp_path, clips: dict[str, bytes], videos=None, **seams):
    """One pass over these videos on a tier that reads frames; the samplers past Immich are real."""
    import sqlite3
    from dataclasses import replace

    from immich_memories.analysis.editorial_clip_frames import (
        CLIP_FRAMES_HEAD,
        CLIP_FRAMES_VERSION,
    )
    from immich_memories.analysis.editorial_clip_frames import SHOWS_ITS_MOMENT as SHOWS
    from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
    from tests.test_editorial_preparation import preview, run, successful_ports

    def clip_frames(**kwargs):
        with sqlite3.connect(kwargs["store_path"]) as connection:
            for asset_id in kwargs["frame_paths"]:
                connection.execute(
                    "INSERT OR REPLACE INTO head_facts VALUES (?,?,?,?,?,?,?)",
                    (asset_id, CLIP_FRAMES_HEAD, CLIP_FRAMES_VERSION, SHOWS, 1.0, "t", "now"),
                )
        return {}

    # WHY: the Immich playback endpoint; the keyframe sampler and FFmpeg past it are real.
    def read(asset_id, start, length):
        data = clips[asset_id]
        return data[start : start + length], len(data)

    return run(
        tmp_path,
        assets=videos or [clip_video(asset_id) for asset_id in clips],
        ports=replace(successful_ports([]), clip_frames=clip_frames, **seams),
        preparation_config=EditorialPreparationConfig(tier="no_captions"),
        fetch_preview=lambda _: preview(),
        read_playback=read,
    )


@requires_ffmpeg
def test_a_prepared_video_banks_the_motion_its_sampled_frames_measure(tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import read_motion_residuals
    from tests.test_playback_keyframes import encode

    clips = {
        "room": static_room_clip(tmp_path / "room.mp4"),
        "busy": encode(tmp_path / "busy.mp4", gop=30),
    }

    cold = prepare_videos(tmp_path, clips)
    warm = prepare_videos(tmp_path, clips)

    assert cold.complete and warm.complete
    # Measured on the frames the exposure head read: no clip was sampled a second time.
    assert cold.pictures_by_stage["detector_frames"] == 2
    assert cold.pictures_by_stage[STAGE] == 2
    assert "detector_frames" not in warm.pictures_by_stage
    measured = read_motion_residuals(
        tmp_path / "annotations.sqlite", [clip_video("room"), clip_video("busy")]
    )
    assert measured["room"]["residual"] < RESIDUAL_MIN <= measured["busy"]["residual"]
    assert measured["room"]["producer"] == VIDEO_RESIDUAL_PRODUCER
    assert measured["room"]["frames"] >= 2


def video_unit(video, residuals):
    """One video through the unit builder both tiers plan on."""
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_structure_material import UnitBuilder
    from immich_memories.config_loader import Config

    source = SimpleNamespace(
        assets={video.id: video},
        motion_residuals=residuals,
        speech_regions={},
        config=Config(),
        pixel_facts={},
        clip_frames={},
    )
    wall = SimpleNamespace(event_assets={"F01": [video.id]}, moment_of_asset={video.id: "M01"})
    ports = SimpleNamespace(resolve_motion=None, thumbnail_hash=lambda _a: None, rules=None)
    builder = UnitBuilder(source, ports, wall, renderings={}, never_auto=set(), document_sources={})
    (unit,) = builder.units_of("F01")
    return unit


def banked_sentence(store: Path, video, sentence: str) -> None:
    import sqlite3

    from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
    from immich_memories.analysis.editorial_preparation_motion import MOTION_PRODUCER
    from immich_memories.store.motion_lines import (
        DESCRIBED,
        MotionLine,
        initialize_motion_lines,
        remember_motion_line,
    )

    with sqlite3.connect(store) as connection:
        initialize_motion_lines(connection)
        remember_motion_line(
            connection,
            asset_id=video.id,
            producer=MOTION_PRODUCER,
            source_digest=source_metadata_digest(video),
            line=MotionLine(DESCRIBED, sentence, 3),
            bytes_read=0,
        )


@requires_ffmpeg
@pytest.mark.parametrize("favourite", [False, True])
def test_a_still_clip_captioned_with_an_action_is_judged_by_its_frames(tmp_path, favourite):
    from immich_memories.analysis.editorial_preparation_motion import (
        BankedMotionLines,
        read_motion_residuals,
    )

    store = tmp_path / "annotations.sqlite"
    room = clip_video("room", favourite=favourite)
    prepare_videos(tmp_path, {"room": static_room_clip(tmp_path / "room.mp4")}, [room])
    banked_sentence(store, room, "A woman dances across the living room.")
    unit = video_unit(room, read_motion_residuals(store, [room]))
    lines = BankedMotionLines(store_path=store, assets={"room": room}, described=True)

    assert unit["kind"] == "video" and unit["residual"] < RESIDUAL_MIN
    # Judged by its frames: nothing in them moves, so a non-favourite's sentence is withheld.
    # A favourite is the owner's own word: this never removes it.
    assert ("dances" in lines.observe(unit)) is favourite
    assert lines.metrics()["unsupported"] == (0 if favourite else 1)


@requires_ffmpeg
def test_a_clip_whose_frames_measure_motion_keeps_its_sentence(tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import (
        BankedMotionLines,
        read_motion_residuals,
    )
    from tests.test_playback_keyframes import encode

    store = tmp_path / "annotations.sqlite"
    busy = clip_video("busy")
    prepare_videos(tmp_path, {"busy": encode(tmp_path / "busy.mp4", gop=30)}, [busy])
    banked_sentence(store, busy, "A child runs in and jumps onto the swing.")
    unit = video_unit(busy, read_motion_residuals(store, [busy]))
    lines = BankedMotionLines(store_path=store, assets={"busy": busy}, described=True)

    assert unit["residual"] >= RESIDUAL_MIN
    assert lines.observe(unit).startswith("A child runs in and jumps onto the swing.")


def test_a_video_nobody_measured_keeps_its_sentence(tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import BankedMotionLines

    store = tmp_path / "annotations.sqlite"
    clip = clip_video("clip")
    banked_sentence(store, clip, "A dog runs.")
    unit = video_unit(clip, {})
    lines = BankedMotionLines(store_path=store, assets={"clip": clip}, described=True)

    assert unit["residual"] is None
    assert lines.observe(unit).startswith("A dog runs.")


@requires_ffmpeg
def test_a_measurement_that_fails_never_blocks_the_cut_and_is_owed_next_pass(tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import read_motion_residuals

    clips = {"room": static_room_clip(tmp_path / "room.mp4")}

    # WHY: the bank write is the boundary that fails here; the frames and sampler are real.
    def broken(**_kwargs):
        raise OSError("disk full")

    failed = prepare_videos(tmp_path, clips, video_motion=broken)
    healed = prepare_videos(tmp_path, clips)

    assert failed.complete and failed.failures == {STAGE: "OSError: disk full"}
    assert failed.producer_failures == ("OSError: disk full",)
    # The frames and the exposure/frame heads are banked; only the residual is still owed.
    assert healed.pictures_by_stage == {"previews": 1, "detector_frames": 1, STAGE: 1}
    assert "room" in read_motion_residuals(tmp_path / "annotations.sqlite", [clip_video("room")])


def test_a_clip_whose_frames_cannot_be_decoded_stays_unmeasured(tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import read_motion_residuals
    from immich_memories.analysis.editorial_video_motion import bank_video_motion

    store = tmp_path / "annotations.sqlite"
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")
    clip = clip_video("clip")

    failures = bank_video_motion(
        store_path=store, videos={"clip": clip}, frame_paths={"clip": [broken, broken]}
    )

    assert failures == {"clip": "0 of 2 frames readable"}
    assert read_motion_residuals(store, [clip]) == {}
