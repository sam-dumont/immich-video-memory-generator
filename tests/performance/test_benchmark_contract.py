"""Contracts for reproducible local performance benchmarks."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
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


def test_profile_harness_requires_an_isolated_explicit_output_directory(tmp_path: Path) -> None:
    """The local profiler must confine its artifacts to the caller's temp root."""
    project_root = Path(__file__).resolve().parents[2]
    script = project_root / "scripts" / "profile_pipeline.py"
    user_state = tmp_path / "user-state"
    environment = os.environ | {
        "IMMICH_MEMORIES_PROFILE_TEST_ROOT": str(tmp_path),
        "XDG_CACHE_HOME": str(user_state / "cache"),
        "XDG_CONFIG_HOME": str(user_state / "config"),
        "XDG_DATA_HOME": str(user_state / "data"),
    }

    help_result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=environment,
    )
    missing_output = subprocess.run(
        [sys.executable, str(script), "--scenario", "controlled-tiny"],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=environment,
    )
    outside_root = subprocess.run(
        [
            sys.executable,
            str(script),
            "--scenario",
            "controlled-tiny",
            "--output-dir",
            str(tmp_path.parent / "outside"),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=environment,
    )
    output_dir = tmp_path / "profile-output"
    isolated_run = subprocess.run(
        [
            sys.executable,
            str(script),
            "--scenario",
            "controlled-tiny",
            "--repetitions",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    assert help_result.returncode == 0
    assert "--output-dir" in help_result.stdout
    assert missing_output.returncode != 0
    assert "--output-dir" in missing_output.stderr
    assert outside_root.returncode != 0
    assert "test root" in outside_root.stderr
    assert isolated_run.returncode == 0, isolated_run.stderr
    metadata = json.loads((output_dir / "controlled-tiny-metadata.json").read_text())
    assert metadata["command"] == [
        sys.executable,
        str(script),
        "--scenario",
        "controlled-tiny",
        "--repetitions",
        "1",
        "--output-dir",
        str(output_dir),
    ]
    assert metadata["config"] == {
        "cache_mode": "cold",
        "clip_count": 2,
        "duration_seconds": 1,
        "assembly": {
            "codec": "h264",
            "crf": 28,
            "transition": "crossfade",
            "transition_duration_seconds": 0.3,
        },
        "analysis": {
            "audio_boundaries": True,
            "adaptive_scene_detector": True,
            "extract_keyframes": False,
            "llm_clients_constructed": False,
            "scene_detector": "SceneDetector",
            "silence_threshold_db": -40.0,
            "min_silence_duration_seconds": 0.2,
            "min_scene_duration_seconds": 1.0,
            "scene_threshold": 27.0,
        },
        "frame_rate": 30,
        "resolution": "1280x720",
    }
    assert metadata["git_revision"]
    assert metadata["environment"].keys() >= {
        "cpu",
        "ffmpeg",
        "ffprobe",
        "platform",
        "python_version",
    }
    assert metadata["environment"]["cpu"] == perf_utils._cpu_fingerprint()
    assert metadata["stage_wall_seconds"].keys() == {"analysis", "assembly"}
    assert metadata["warmup_stage_wall_seconds"].keys() == {"analysis", "assembly"}
    assert all(metadata["stage_wall_seconds"][stage][0] > 0 for stage in ("analysis", "assembly"))
    assert all(
        metadata["warmup_stage_wall_seconds"][stage] > 0 for stage in ("analysis", "assembly")
    )
    assert "estimate" in metadata["subprocess_wait_note"]
    assert all(
        metadata["subprocess_wait_estimate_seconds"][stage][0]
        <= metadata["stage_wall_seconds"][stage][0] + 0.01
        for stage in ("analysis", "assembly")
    )
    assert (output_dir / "controlled-tiny-1-assembly.prof").is_file()
    assert (output_dir / "controlled-tiny-1-assembly-cumulative.txt").is_file()
    assert (output_dir / "controlled-tiny-1-assembly-self.txt").is_file()
    assert (output_dir / "controlled-tiny-1-analysis.prof").is_file()
    assert (output_dir / "controlled-tiny-1-analysis-self.txt").is_file()
    assert not user_state.exists()


def test_local_benchmark_submission_requires_full_assembly_metadata() -> None:
    """Local dispatch must not submit stripped benchmark projections as reproducible results."""
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()

    assert "inputs[results]=$$(cat tests/perf-results.json)" in makefile
    assert "titles skipped: full reproduction metadata is not exported yet" in makefile


@pytest.mark.parametrize(
    ("history_count", "expected"),
    [
        pytest.param(0, 0, id="no-history"),
        pytest.param(9, 9, id="nine-history-runs"),
        pytest.param(10, 10, id="ten-history-runs"),
    ],
)
def test_benchmark_baseline_threshold_uses_action_datajson_history(
    history_count: int, expected: int
) -> None:
    """The advisory threshold counts official DataJson Benchmark histories."""
    from scripts.profile_pipeline import count_comparable_baselines

    data = {"lastUpdate": 0, "repoUrl": "example", "entries": {"Benchmark": [{}] * history_count}}

    assert count_comparable_baselines(data) == expected
    assert (count_comparable_baselines(data) >= 10) is (history_count >= 10)


def test_benchmark_projection_rejects_incomplete_reproduction_metadata() -> None:
    """CI dispatch cannot convert stripped or incomplete results into comparisons."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    result = dict.fromkeys(REQUIRED_REPRODUCTION_KEYS, "value")
    result.update(
        {"scenario": "assembly", "median_wall_seconds": 1.0, "raw_repetition_seconds": [1, 1, 1]}
    )

    assert benchmark_comparison_projection({"results": [result]}) == [
        {"name": "assembly", "unit": "seconds", "value": 1.0}
    ]
    with pytest.raises(ValueError, match="full benchmark results"):
        benchmark_comparison_projection([])
    incomplete = result.copy()
    del incomplete["cpu"]
    with pytest.raises(ValueError, match="missing"):
        benchmark_comparison_projection({"results": [incomplete]})
    two_repetitions = result | {"raw_repetition_seconds": [1, 1]}
    with pytest.raises(ValueError, match="exactly three"):
        benchmark_comparison_projection({"results": [two_repetitions]})


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
