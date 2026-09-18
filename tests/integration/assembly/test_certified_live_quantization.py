"""A certified Live merge encodes exactly what its source packets predict, cut by cut."""

from __future__ import annotations

import json
import struct
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from immich_memories import generate_downloads as downloads
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.processing import editorial_live_render as certified
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from immich_memories.processing.probe_cache import ProbeCache
from tests.conftest import make_asset
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _companion(path: Path, frames: int = 89, *, size: str = "320x240") -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-frames:v", str(frames), "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-video_track_timescale", "600", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _over_declaring_companion(path: Path) -> Path:
    """A companion whose audio outlives its video: 2.000 s of container over 59 frames.

    One video tick is one frame, and the audio is uncompressed, so every FFmpeg
    writes the same over-declaration: FFmpeg 6 gives the final sample a 1-tick
    duration and FFmpeg 8 a whole frame, which here are the same thing.
    """
    video, audio = path.with_name("video.mov"), path.with_name("audio.wav")
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30",
            "-frames:v", "59",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-video_track_timescale", "30", str(video),
        ],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-t", "2", "-i", "sine=frequency=440:sample_rate=48000",
            "-c:a", "pcm_s16le", str(audio),
        ],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio),
            "-c", "copy", "-video_track_timescale", "30", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _certified_clip(material: LiveRenderMaterial) -> VideoClipInfo:
    asset = make_asset(material.still_ids[0], duration=None)
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = material.video_ids[0]
    return VideoClipInfo(
        asset=asset,
        duration_seconds=material.duration_seconds,
        live_burst_still_ids=list(material.still_ids),
        live_burst_video_ids=list(material.video_ids),
        live_burst_trim_points=list(material.trim_points),
        live_burst_shutter_timestamps=list(material.shutter_timestamps),
        live_burst_material=material.as_dict(),
        editorial_live_manifest={
            "version": certified.RENDER_VERSION,
            "material": material.as_dict(),
            "selected_interval": [0.0, material.duration_seconds],
        },
    )


@pytest.mark.parametrize("sizes", [("320x240", "320x240"), ("320x240", "160x120")])
def test_two_cuts_certify_at_their_packet_predicted_length(tmp_path, sizes):
    # Each cut rounds its own segment up to whole output frames before concat:
    # 89 + 74 frames at 30 fps is 38.8 ms past the declared 5.3945 s, more than one frame.
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("still-a", "video-a", 0.0, 0.0, 2.94),
            LiveSourceEntry("still-b", "video-b", 1.0, 0.4855, 2.94),
        )
    )
    clip = _certified_clip(material)
    paths = [
        _companion(tmp_path / f"{video_id}.mov", size=size)
        for video_id, size in zip(material.video_ids, sizes, strict=True)
    ]

    merged = certified.render_certified_live(
        clip, paths, tmp_path, merge=downloads._try_merge_burst, hardware_enabled=False
    )

    record = json.loads(merged.with_suffix(".json").read_text())
    assert record["encoded_duration_seconds"] == pytest.approx(163 / 30, abs=1e-5)
    assert record["predicted_duration_seconds"] == pytest.approx(163 / 30, abs=1e-9)
    assert record["encoded_duration_seconds"] - record["declared_duration_seconds"] > 1 / 30
    assert [s["quantized_segment"]["frames"] for s in record["frame_quantization"]["sources"]] == [
        89,
        74,
    ]
    assert record["frame_quantization"]["final_frame_hold"] is None
    probe = ProbeCache().get(merged)
    assert probe.resolution == (320, 240)
    assert probe.has_audio


def test_container_over_declaring_its_video_renders_to_its_final_packet(tmp_path):
    # The container header, which is what Immich publishes and the editor declares,
    # counts one frame more than the video packets deliver. The declared 2.0 s stops
    # at the last packet, 1.966667 s, and that frame is held for the remainder.
    material = LiveRenderMaterial((LiveSourceEntry("still-a", "video-a", 0.0, 0.0, 2.0),))
    source = _over_declaring_companion(tmp_path / "companion.mov")
    probes = ProbeCache()
    assert probes.get(source).duration_seconds == 2.0
    assert probes.last_video_frame(source)["end_seconds"] == pytest.approx(59 / 30)

    merged = certified.render_certified_live(
        _certified_clip(material), [source], tmp_path, merge=downloads._try_merge_burst,
        hardware_enabled=False,
    )  # fmt: skip

    record = json.loads(merged.with_suffix(".json").read_text())
    source_timing = record["frame_quantization"]["sources"][0]
    assert source_timing["boundary"] == "millisecond-container-end-within-final-source-frame"
    assert source_timing["final_packet"]["pts"] == 58
    assert record["predicted_duration_seconds"] == pytest.approx(59 / 30, abs=1e-9)
    assert record["encoded_duration_seconds"] >= 2.0
    hold = record["frame_quantization"]["final_frame_hold"]
    assert hold is None or hold["nominal_seconds"] == 2.0
    assert ProbeCache().get(merged).has_audio


def test_mov_edit_list_reference_packets_do_not_extend_visible_material(tmp_path):
    source = _companion(tmp_path / "edited.mov", frames=120)
    data = bytearray(source.read_bytes())
    edit = data.index(b"elst")
    movie = data.index(b"mvhd")
    assert data[edit + 4] == 0  # Version-zero edit list, with one video edit.
    assert struct.unpack_from(">I", data, edit + 8)[0] == 1
    timescale = struct.unpack_from(">I", data, movie + 16)[0]
    # Camera MOVs retain compressed reference frames outside the visible edit.
    # Shorten only the video edit; the audio/container still run for four seconds.
    struct.pack_into(">I", data, edit + 12, round(2.85 * timescale))
    source.write_bytes(data)
    decoded = json.loads(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_frames", "-show_entries", "frame=pts", "-of", "json", str(source),
            ]
        )
    )  # fmt: skip
    visible_frames = len(decoded["frames"])
    assert visible_frames == 86

    probes = ProbeCache()
    # An edit can shorten its last packet; compare the displayed frame's actual PTS.
    assert probes.last_video_frame(source)["pts"] == decoded["frames"][-1]["pts"]
    segment = probes.quantized_segment(source, 0.0, 4.0, Fraction(30))
    assert segment["frames"] == visible_frames
    assert segment["kept_packets"] == visible_frames
