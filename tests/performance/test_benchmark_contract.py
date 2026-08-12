"""Contracts for reproducible local performance benchmarks."""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tests.integration.assembly import perf_utils
from tests.integration.assembly.conftest import make_n_clips
from tests.integration.assembly.perf_utils import PerfResult
from tests.integration.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg

REQUIRED_REPRODUCTION_KEYS = {
    "input_duration_seconds",
    "codec",
    "frame_rate",
    "cache_mode",
    "python_version",
    "platform",
    "cpu",
    "git_revision",
    "warmup_wall_seconds",
    "raw_repetition_seconds",
    "median_wall_seconds",
}


def test_minimal_assembly_uses_identity_checked_three_second_fixtures() -> None:
    """The controlled minimum must not reuse legacy session fixtures."""
    from tests.integration.assembly.test_perf_assembly import TestMinimalScenario

    source = inspect.getsource(TestMinimalScenario.test_minimal_assembly_resources)

    assert 'make_n_clips(fixtures_dir, 2, "1280x720", duration=3, fps=30, codec="h264")' in source
    assert "test_clip_720p" not in source


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout)


def _probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _overwrite_video(
    path: Path,
    *,
    codec: str = "h264",
    resolution: str = "320x180",
    fps: int = 24,
    duration: int = 1,
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={resolution}:rate={fps}:duration={duration}",
            "-c:v",
            {"h264": "libx264", "h265": "libx265"}[codec],
            "-preset",
            "ultrafast",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_fixture_filename_records_every_requested_media_property(tmp_path: Path) -> None:
    clip = make_n_clips(tmp_path, 1, "1280x720", duration=3, fps=30, codec="h264")[0]

    assert clip.name == "perf_clip_1280x720_3s_30fps_h264_00.mp4"


def test_fixture_sidecar_records_full_arguments_and_their_hash(tmp_path: Path) -> None:
    clip = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]

    sidecar = json.loads(clip.with_suffix(".json").read_text())
    arguments = sidecar["source_args"] + sidecar["encoder_args"]

    assert sidecar["source_args"] == [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24:duration=1:alpha=40",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:duration=1",
    ]
    assert sidecar["encoder_args"] == [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-shortest",
    ]
    assert sidecar["identity_hash"] == hashlib.sha256("\0".join(arguments).encode()).hexdigest()


def test_h265_fixture_is_generated_and_probes_as_hevc(tmp_path: Path) -> None:
    clip = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h265")[0]

    probe = _probe_video(clip)
    stream = probe["streams"][0]

    assert stream["codec_name"] == "hevc"
    assert stream["width"] == 320
    assert stream["height"] == 180
    assert stream["avg_frame_rate"] == "24/1"
    assert float(probe["format"]["duration"]) == pytest.approx(1, abs=0.2)


def test_sidecar_hash_mismatch_regenerates_the_fixture(tmp_path: Path) -> None:
    clip = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]
    metadata_path = clip.with_suffix(".json")
    sidecar = json.loads(metadata_path.read_text())
    sidecar["identity_hash"] = "stale"
    metadata_path.write_text(json.dumps(sidecar))

    regenerated = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]

    assert regenerated == clip
    assert _probe_video(regenerated)["streams"][0]["codec_name"] == "h264"
    assert json.loads(metadata_path.read_text())["identity_hash"] != "stale"


def test_sidecar_argument_mismatch_regenerates_the_fixture(tmp_path: Path) -> None:
    clip = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]
    metadata_path = clip.with_suffix(".json")
    sidecar = json.loads(metadata_path.read_text())
    sidecar["source_args"][3] = "testsrc2=size=999x999:rate=1:duration=9"
    metadata_path.write_text(json.dumps(sidecar))

    regenerated = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]

    assert _probe_video(regenerated)["streams"][0]["codec_name"] == "h264"
    assert json.loads(metadata_path.read_text())["source_args"][3].startswith(
        "testsrc2=size=320x180"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"codec": "h265"}, id="codec"),
        pytest.param({"resolution": "640x360"}, id="dimensions"),
        pytest.param({"fps": 15}, id="frame-rate"),
        pytest.param({"duration": 2}, id="duration"),
    ],
)
def test_actual_media_mismatch_regenerates_the_fixture(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    clip = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]
    _overwrite_video(clip, **overrides)

    regenerated = make_n_clips(tmp_path, 1, "320x180", duration=1, fps=24, codec="h264")[0]

    probe = _probe_video(regenerated)
    stream = probe["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["width"] == 320
    assert stream["height"] == 180
    assert stream["avg_frame_rate"] == "24/1"
    assert float(probe["format"]["duration"]) == pytest.approx(1, abs=0.2)


def test_media_fixture_key_changes_with_duration(tmp_path: Path) -> None:
    five = make_n_clips(tmp_path, 1, "320x180", duration=5, fps=30, codec="h264")
    ten = make_n_clips(tmp_path, 1, "320x180", duration=10, fps=30, codec="h264")

    assert five[0] != ten[0]
    assert _probe_duration(five[0]) == pytest.approx(5, abs=0.2)
    assert _probe_duration(ten[0]) == pytest.approx(10, abs=0.2)


def test_perf_result_records_reproduction_inputs() -> None:
    payload = PerfResult(
        scenario="contract",
        python_peak_mb=1.0,
        wall_seconds=2.0,
        cpu_user_seconds=1.0,
        cpu_sys_seconds=0.2,
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode="cold",
        python_version="3.13.5",
        platform="darwin-arm64",
        cpu="test-cpu",
        git_revision="abc1234",
    ).to_dict()

    assert payload.keys() >= REQUIRED_REPRODUCTION_KEYS


def test_repetition_summary_keeps_warmup_raw_measurements_and_median() -> None:
    calls: list[int] = []

    assert hasattr(perf_utils, "measure_repetitions")
    result = perf_utils.measure_repetitions(
        scenario="contract",
        operation=lambda index: calls.append(index),
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode="cold",
    )

    assert calls == [0, 1, 2, 3]
    assert result.warmup_wall_seconds is not None
    assert len(result.raw_repetition_seconds) == 3
    assert len(result.raw_repetition_metrics) == 3
    assert result.median_wall_seconds == pytest.approx(sorted(result.raw_repetition_seconds)[1])
    assert result.cpu_user_seconds == pytest.approx(
        sorted(sample["cpu_user_seconds"] for sample in result.raw_repetition_metrics)[1]
    )


def test_repeated_benchmarks_do_not_export_child_rss_as_comparable_peak(tmp_path: Path) -> None:
    result = PerfResult(
        scenario="contract",
        python_peak_mb=1.0,
        child_peak_rss_mb=42.0,
        wall_seconds=2.0,
        cpu_user_seconds=1.0,
        cpu_sys_seconds=0.2,
        raw_repetition_seconds=[1.9, 2.0, 2.1],
        median_wall_seconds=2.0,
    )
    output_path = tmp_path / "benchmark.json"

    perf_utils.save_benchmark_json([result], output_path)

    entries = json.loads(output_path.read_text())
    assert entries == [{"name": "contract", "unit": "seconds", "value": 2.0}]
