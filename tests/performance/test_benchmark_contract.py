"""Contracts that keep performance results reproducible and honest."""

from __future__ import annotations

import hashlib
import json
import statistics
import tempfile
from pathlib import Path

import pytest
from tests.integration.assembly.conftest import make_n_clips
from tests.integration.assembly.perf_utils import (
    REQUIRED_REPRODUCTION_KEYS,
    PerfResult,
    run_repetitions,
    save_benchmark_json,
    save_summary_results,
)
from tests.integration.conftest import ffprobe_json, get_duration, requires_ffmpeg


@requires_ffmpeg
def test_media_fixture_key_and_fingerprint_cover_every_generation_input(tmp_path: Path) -> None:
    """Duration/fps/codec/index and full FFmpeg args must identify a fixture."""
    five = make_n_clips(tmp_path, 1, "160x90", duration=5, fps=12, codec="h264")
    ten = make_n_clips(tmp_path, 1, "160x90", duration=10, fps=12, codec="h264")

    assert five[0].name == "perf_clip_160x90_5s_12fps_h264_00.mp4"
    assert ten[0].name == "perf_clip_160x90_10s_12fps_h264_00.mp4"
    assert five[0] != ten[0]
    assert get_duration(ffprobe_json(five[0])) == pytest.approx(5, abs=0.2)
    assert get_duration(ffprobe_json(ten[0])) == pytest.approx(10, abs=0.2)

    metadata = json.loads(five[0].with_suffix(".mp4.fixture.json").read_text())
    ffmpeg_args = metadata["ffmpeg_args"]
    expected_fingerprint = hashlib.sha256(
        json.dumps(ffmpeg_args, separators=(",", ":")).encode()
    ).hexdigest()
    assert metadata["ffmpeg_args_sha256"] == expected_fingerprint
    assert metadata["identity"] == {
        "codec": "h264",
        "duration_seconds": 5.0,
        "frame_rate": 12.0,
        "height": 90,
        "width": 160,
    }

    variants = {
        make_n_clips(tmp_path, 1, "320x180", duration=5, fps=12, codec="h264")[0],
        make_n_clips(tmp_path, 1, "160x90", duration=5, fps=24, codec="h264")[0],
        make_n_clips(tmp_path, 1, "160x90", duration=5, fps=12, codec="h265")[0],
        make_n_clips(tmp_path, 2, "160x90", duration=5, fps=12, codec="h264")[1],
    }
    assert len(variants | {five[0]}) == 5


@requires_ffmpeg
def test_media_fixture_is_regenerated_when_probe_cannot_confirm_identity(tmp_path: Path) -> None:
    """An existing filename is not reusable proof when its media is invalid."""
    clip = make_n_clips(tmp_path, 1, "160x90", duration=1, fps=12, codec="h264")[0]
    clip.write_bytes(b"stale partial fixture")

    regenerated = make_n_clips(
        tmp_path,
        1,
        "160x90",
        duration=1,
        fps=12,
        codec="h264",
    )[0]

    probe = ffprobe_json(regenerated)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert (video["width"], video["height"]) == (160, 90)
    assert get_duration(probe) == pytest.approx(1, abs=0.2)


@pytest.mark.parametrize(
    ("encoder", "resolution", "fps", "duration"),
    [
        pytest.param("libx265", "160x90", 12, 1, id="codec"),
        pytest.param("libx264", "320x180", 12, 1, id="dimensions"),
        pytest.param("libx264", "160x90", 24, 1, id="frame-rate"),
        pytest.param("libx264", "160x90", 12, 2, id="duration"),
    ],
)
@requires_ffmpeg
def test_media_fixture_rejects_each_wrong_probe_identity_field(
    tmp_path: Path,
    encoder: str,
    resolution: str,
    fps: int,
    duration: int,
) -> None:
    """Each probed identity field independently invalidates fixture reuse."""
    import subprocess

    clip = make_n_clips(tmp_path, 1, "160x90", duration=1, fps=12, codec="h264")[0]
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={resolution}:rate={fps}:duration={duration}",
            "-c:v",
            encoder,
            "-preset",
            "ultrafast",
            "-an",
            str(clip),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    regenerated = make_n_clips(
        tmp_path,
        1,
        "160x90",
        duration=1,
        fps=12,
        codec="h264",
    )[0]

    probe = ffprobe_json(regenerated)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert (video["width"], video["height"]) == (160, 90)
    assert float(video["avg_frame_rate"].split("/")[0]) == 12
    assert get_duration(probe) == pytest.approx(1, abs=0.2)


def _perf_result(*, wall_seconds: float, cache_mode: str = "warm") -> PerfResult:
    return PerfResult(
        scenario="contract",
        python_peak_mb=1.0,
        wall_seconds=wall_seconds,
        cpu_user_seconds=1.0,
        cpu_sys_seconds=0.2,
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode=cache_mode,
        python_version="3.13.5",
        platform="darwin-arm64",
        cpu="test-cpu",
        git_revision="abc1234",
    )


def test_perf_result_records_reproduction_inputs() -> None:
    """Each raw measurement must carry enough context to reproduce it."""
    payload = _perf_result(wall_seconds=2.0, cache_mode="cold").to_dict()

    assert payload.keys() >= REQUIRED_REPRODUCTION_KEYS
    assert payload["input_duration_seconds"] == 5.0
    assert payload["codec"] == "h264"
    assert payload["frame_rate"] == 30.0
    assert payload["cache_mode"] == "cold"


def test_measurement_populates_machine_and_git_reproduction_fields() -> None:
    """Callers provide input identity; the harness supplies environment identity."""
    from tests.integration.assembly.perf_utils import measure_resources

    with measure_resources(
        "contract",
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode="cold",
    ) as result:
        pass

    assert result.python_version
    assert result.platform
    assert result.cpu not in {"", "unknown"}
    assert result.git_revision not in {"", "unknown"}


def test_repetition_summary_keeps_warmup_raw_runs_and_median(tmp_path: Path) -> None:
    """A benchmark reports all observations and never cherry-picks its fastest run."""
    outputs: list[Path] = []

    def operation(output_path: Path) -> Path:
        outputs.append(output_path)
        output_path.write_bytes(b"measured")
        return output_path

    summary = run_repetitions(
        operation,
        scenario="contract",
        output_dir=tmp_path,
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
    )

    assert len(outputs) == 4
    assert all(output.is_relative_to(tmp_path) for output in outputs)
    assert summary.warmup.cache_mode == "cold"
    assert len(summary.repetitions) == 3
    assert {result.cache_mode for result in summary.repetitions} == {"warm"}
    raw = [result.wall_seconds for result in summary.repetitions]
    assert summary.median_wall_seconds == statistics.median(raw)
    assert summary.median_wall_seconds != min(raw) or len(set(raw)) == 1


def test_repetition_accepts_explicit_posix_system_temp_root() -> None:
    """Named benchmark roots under canonical /tmp remain valid on macOS."""
    system_tmp = Path("/tmp").resolve()
    if not system_tmp.is_dir():
        pytest.skip("POSIX /tmp is unavailable")

    def write_output(output: Path) -> Path:
        output.write_bytes(b"measured")
        return output

    with tempfile.TemporaryDirectory(prefix="immich-perf-contract-", dir=system_tmp) as root:
        summary = run_repetitions(
            write_output,
            scenario="system-temp",
            output_dir=Path(root),
            clip_count=1,
            resolution="640x360",
            input_duration_seconds=1.0,
            codec="h264",
            frame_rate=30.0,
        )

    assert len(summary.repetitions) == 3


def test_repetition_rejects_repository_output_root() -> None:
    """Benchmark measurements must never overwrite tracked repository artifacts."""
    with pytest.raises(ValueError, match="temporary root"):
        run_repetitions(
            lambda output: output,
            scenario="workspace",
            output_dir=Path.cwd() / "tests",
            clip_count=1,
            resolution="640x360",
            input_duration_seconds=1.0,
            codec="h264",
            frame_rate=30.0,
        )


def test_summary_exports_warmup_repetitions_and_median_to_temp_root(tmp_path: Path) -> None:
    """Both detailed and CI summaries preserve the median contract."""
    warmup = _perf_result(wall_seconds=9.0, cache_mode="cold")
    measured = tuple(_perf_result(wall_seconds=value) for value in (4.0, 8.0, 6.0))

    from tests.integration.assembly.perf_utils import BenchmarkSummary

    summary = BenchmarkSummary(warmup=warmup, repetitions=measured)
    details_path = tmp_path / "perf-results.json"
    benchmark_path = tmp_path / "benchmark.json"

    save_summary_results([summary], details_path)
    save_benchmark_json([summary], benchmark_path)

    details = json.loads(details_path.read_text())
    assert details["results"][0]["warmup"]["wall_seconds"] == 9.0
    assert [run["wall_seconds"] for run in details["results"][0]["repetitions"]] == [
        4.0,
        8.0,
        6.0,
    ]
    assert details["results"][0]["median_wall_seconds"] == 6.0
    benchmark = json.loads(benchmark_path.read_text())
    wall = next(entry for entry in benchmark if entry["unit"] == "seconds")
    assert wall["value"] == 6.0


def test_summary_rejects_fewer_than_three_measured_repetitions() -> None:
    """One lucky timing cannot masquerade as a baseline."""
    from tests.integration.assembly.perf_utils import BenchmarkSummary

    with pytest.raises(ValueError, match="at least three"):
        BenchmarkSummary(
            warmup=_perf_result(wall_seconds=9.0, cache_mode="cold"),
            repetitions=(
                _perf_result(wall_seconds=4.0),
                _perf_result(wall_seconds=6.0),
            ),
        )


@pytest.mark.parametrize(
    ("warmup", "repetitions", "message"),
    [
        (
            _perf_result(wall_seconds=9.0, cache_mode="warm"),
            tuple(_perf_result(wall_seconds=value) for value in (4.0, 6.0, 8.0)),
            "warmup.*cold",
        ),
        (
            _perf_result(wall_seconds=9.0, cache_mode="cold"),
            (
                _perf_result(wall_seconds=4.0),
                _perf_result(wall_seconds=6.0, cache_mode="cold"),
                _perf_result(wall_seconds=8.0),
            ),
            "repetitions.*warm",
        ),
        (
            _perf_result(wall_seconds=9.0, cache_mode="cold"),
            (
                _perf_result(wall_seconds=4.0),
                _perf_result(wall_seconds=6.0),
                PerfResult(
                    **{
                        **_perf_result(wall_seconds=8.0).to_dict(),
                        "codec": "h265",
                    }
                ),
            ),
            "reproduction identity",
        ),
        (
            PerfResult(
                **{
                    **_perf_result(wall_seconds=9.0, cache_mode="cold").to_dict(),
                    "git_revision": "unknown",
                }
            ),
            tuple(_perf_result(wall_seconds=value) for value in (4.0, 6.0, 8.0)),
            "reproduction field",
        ),
    ],
)
def test_summary_rejects_invalid_measurement_identity(
    warmup: PerfResult,
    repetitions: tuple[PerfResult, ...],
    message: str,
) -> None:
    """Cold/warm labels and every reproduction field are enforced, not advisory."""
    from tests.integration.assembly.perf_utils import BenchmarkSummary

    with pytest.raises(ValueError, match=message):
        BenchmarkSummary(warmup=warmup, repetitions=repetitions)
