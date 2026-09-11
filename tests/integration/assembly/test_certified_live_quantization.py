"""A certified Live merge encodes exactly what its source packets predict, cut by cut."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from immich_memories import generate_downloads as downloads
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.processing import editorial_live_render as certified
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.conftest import make_asset
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _companion(path: Path, frames: int = 89) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-frames:v", str(frames), "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-video_track_timescale", "600", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def test_two_cuts_certify_at_their_packet_predicted_length(tmp_path):
    # Each cut rounds its own segment up to whole output frames before concat:
    # 89 + 74 frames at 30 fps is 38.8 ms past the declared 5.3945 s, more than one frame.
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("still-a", "video-a", 0.0, 0.0, 2.94),
            LiveSourceEntry("still-b", "video-b", 1.0, 0.4855, 2.94),
        )
    )
    asset = make_asset("still-a", duration=None)
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = "video-a"
    clip = VideoClipInfo(
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
    paths = [_companion(tmp_path / f"{video_id}.mov") for video_id in material.video_ids]

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
