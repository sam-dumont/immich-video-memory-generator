"""Contracts for reproducible local performance benchmarks."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
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
    forbidden_temp = tmp_path / "forbidden-temp"
    forbidden_temp.mkdir()
    environment["TMPDIR"] = str(forbidden_temp)

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
            "3",
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
        "3",
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
    assert all(
        len(metadata["stage_wall_seconds"][stage]) == 3 for stage in ("analysis", "assembly")
    )
    assert all(
        metadata["warmup_stage_wall_seconds"][stage] > 0 for stage in ("analysis", "assembly")
    )
    assert "estimate" in metadata["subprocess_wait_note"]
    assert all(
        metadata["subprocess_wait_estimate_seconds"][stage][0]
        <= metadata["stage_wall_seconds"][stage][0] + 0.01
        for stage in ("analysis", "assembly")
    )
    for index in range(1, 4):
        assert (output_dir / f"controlled-tiny-{index}-assembly.prof").is_file()
        assert (output_dir / f"controlled-tiny-{index}-assembly-cumulative.txt").is_file()
        assert (output_dir / f"controlled-tiny-{index}-assembly-self.txt").is_file()
        assert (output_dir / f"controlled-tiny-{index}-analysis.prof").is_file()
        assert (output_dir / f"controlled-tiny-{index}-analysis-self.txt").is_file()
    assert not user_state.exists()
    assert list(forbidden_temp.iterdir()) == []


def test_profile_harness_rejects_less_than_three_repetitions(tmp_path: Path) -> None:
    """A profile needs one warm-up plus at least three comparable samples."""
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "profile_pipeline.py"),
            "--scenario",
            "controlled-tiny",
            "--repetitions",
            "2",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "at least three" in result.stderr


def test_local_benchmark_submission_requires_full_assembly_metadata() -> None:
    """Local dispatch must not submit stripped benchmark projections as reproducible results."""
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()

    assert "inputs[results]=$$(cat tests/perf-results.json)" in makefile
    assert "titles skipped: full reproduction metadata is not exported yet" in makefile


def test_ci_comparison_uses_validated_assembly_projection_only() -> None:
    """Title JSON cannot dilute assembly comparison identity before it exports metadata."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "benchmark.yml"
    ).read_text()

    assert (
        "open('tests/benchmark-results.json', 'w').write(json.dumps(projection, indent=2))"
        in workflow
    )
    assert "Title performance comparison is advisory-unavailable" in workflow
    assert "Merge benchmark JSON files" not in workflow
    assert "count_comparable_baselines(data, current)" in workflow


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

    projection = [{"name": "assembly", "extra": "identity"}]
    data["entries"]["Benchmark"] = [
        {"benches": [{"name": "assembly", "extra": "identity"}]} for _ in range(history_count)
    ]
    assert count_comparable_baselines(data, projection) == expected
    assert (count_comparable_baselines(data, projection) >= 10) is (history_count >= 10)


def test_benchmark_baseline_history_excludes_mismatched_and_partial_suites() -> None:
    """A cached run is comparable only when every current workload identity matches."""
    from scripts.profile_pipeline import count_comparable_baselines

    projection = [
        {"name": "assembly", "extra": "cpu-a"},
        {"name": "analysis", "extra": "cpu-a"},
    ]
    matching = {
        "benches": [{"name": "assembly", "extra": "cpu-a"}, {"name": "analysis", "extra": "cpu-a"}]
    }
    mismatched = {
        "benches": [{"name": "assembly", "extra": "cpu-b"}, {"name": "analysis", "extra": "cpu-b"}]
    }
    partial = {"benches": [{"name": "assembly", "extra": "cpu-a"}]}
    data = {"entries": {"Benchmark": [matching] * 10 + [mismatched, partial]}}

    assert count_comparable_baselines(data, projection) == 10


def test_benchmark_projection_rejects_incomplete_reproduction_metadata() -> None:
    """CI dispatch cannot convert stripped or incomplete results into comparisons."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    result = _complete_benchmark_result()

    projection = benchmark_comparison_projection({"results": [result]})
    assert projection[0]["name"].startswith("assembly [")
    assert projection[0]["name"].endswith("]")
    assert projection[0]["unit"] == "seconds"
    assert projection[0]["value"] == 1.0
    assert isinstance(projection[0]["extra"], str)
    with pytest.raises(ValueError, match="full benchmark results"):
        benchmark_comparison_projection([])
    incomplete = result.copy()
    del incomplete["cpu"]
    with pytest.raises(ValueError, match="missing"):
        benchmark_comparison_projection({"results": [incomplete]})
    two_repetitions = result | {"raw_repetition_seconds": [1, 1]}
    with pytest.raises(ValueError, match="exactly three"):
        benchmark_comparison_projection({"results": [two_repetitions]})


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        pytest.param("cpu", "different CPU", id="cpu"),
        pytest.param("platform", "linux-x86_64", id="platform"),
        pytest.param("python_version", "3.13.0", id="python"),
        pytest.param("cache_mode", "cold", id="config"),
        pytest.param("codec", "h265", id="workload"),
    ],
)
def test_benchmark_projection_identity_changes_for_comparability_fields(
    field: str, changed: object
) -> None:
    """Workload and environment changes produce a distinct benchmark history identity."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    baseline = benchmark_comparison_projection({"results": [_complete_benchmark_result()]})[0]
    modified = benchmark_comparison_projection(
        {"results": [_complete_benchmark_result() | {field: changed}]}
    )[0]

    assert baseline["name"] != modified["name"]
    assert baseline["extra"] != modified["extra"]


def test_benchmark_projection_identity_is_deterministic_for_identical_input() -> None:
    """The same workload and environment always create the same cache identity."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    first = benchmark_comparison_projection({"results": [_complete_benchmark_result()]})[0]
    second = benchmark_comparison_projection({"results": [_complete_benchmark_result()]})[0]

    assert first["name"] == second["name"]
    assert first["extra"] == second["extra"]


def test_benchmark_projection_identity_ignores_git_revision_and_timing() -> None:
    """Comparable workload identity excludes revision and timing observations."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    baseline = benchmark_comparison_projection({"results": [_complete_benchmark_result()]})[0]
    modified = benchmark_comparison_projection(
        {
            "results": [
                _complete_benchmark_result()
                | {
                    "git_revision": "different",
                    "warmup_wall_seconds": 9.0,
                    "raw_repetition_seconds": [9.0, 9.0, 9.0],
                    "median_wall_seconds": 9.0,
                }
            ]
        }
    )[0]

    assert baseline["name"] == modified["name"]
    assert baseline["extra"] == modified["extra"]


@pytest.mark.parametrize(
    "raw", ["123", {"seconds": [1, 1, 1]}, [1, 1, True], [1, 1, math.nan], [1, 1, "one"]]
)
def test_benchmark_projection_rejects_non_numeric_repetition_values(raw: object) -> None:
    """Only three finite numeric sample values are comparable benchmark evidence."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    result = _complete_benchmark_result() | {"raw_repetition_seconds": raw}

    with pytest.raises(ValueError, match="raw_repetition_seconds"):
        benchmark_comparison_projection({"results": [result]})


@pytest.mark.parametrize(
    "field, value",
    [("cpu", ""), ("frame_rate", True), ("input_duration_seconds", math.nan), ("codec", 1)],
)
def test_benchmark_projection_rejects_malformed_identity_values(field: str, value: object) -> None:
    """Identity metadata must be valid before it becomes a comparison signature."""
    from scripts.profile_pipeline import benchmark_comparison_projection

    result = _complete_benchmark_result() | {field: value}

    with pytest.raises(ValueError, match=field):
        benchmark_comparison_projection({"results": [result]})


def _complete_benchmark_result() -> dict[str, object]:
    return {
        "scenario": "assembly",
        "clip_count": 2,
        "resolution": "1280x720",
        "input_duration_seconds": 3.0,
        "codec": "h264",
        "frame_rate": 30.0,
        "cache_mode": "warm",
        "python_version": "3.12.11",
        "platform": "darwin-arm64",
        "cpu": "Apple M5 Max",
        "git_revision": "abcdef0",
        "warmup_wall_seconds": 1.0,
        "raw_repetition_seconds": [1.0, 1.0, 1.0],
        "median_wall_seconds": 1.0,
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
