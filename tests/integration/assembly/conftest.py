"""Assembly-specific fixtures: multi-clip sets at various resolutions."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def test_clip_1080p(fixtures_dir: Path) -> Path:
    """5-second 1080p H.264 clip with audio."""
    out = fixtures_dir / "test_1080p.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
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
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


@pytest.fixture(scope="session")
def test_clip_1080p_b(fixtures_dir: Path) -> Path:
    """Different 5-second 1080p clip."""
    out = fixtures_dir / "test_1080p_b.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=5:alpha=160",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=5",
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
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


@pytest.fixture(scope="session")
def test_clip_1080p_c(fixtures_dir: Path) -> Path:
    """Third 5-second 1080p clip."""
    out = fixtures_dir / "test_1080p_c.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=5:alpha=80",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=5",
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
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


_CODEC_ENCODERS = {"h264": "libx264", "h265": "libx265"}
_PROBE_CODEC_NAMES = {"h264": "h264", "h265": "hevc"}


def _number_token(value: int | float) -> str:
    """Return a stable, filename-safe number without redundant zeroes."""
    return f"{float(value):g}"


def _fixture_metadata_path(path: Path) -> Path:
    return path.with_suffix(f"{path.suffix}.fixture.json")


def _ffmpeg_args(
    output_path: Path,
    *,
    resolution: str,
    duration: int | float,
    fps: int | float,
    codec: str,
    index: int,
) -> list[str]:
    freq = 220 + index * 110
    alpha = 40 + index * 30
    return [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            f"testsrc2=size={resolution}:rate={float(fps):g}:"
            f"duration={float(duration):g}:alpha={alpha % 256}"
        ),
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={float(duration):g}",
        "-c:v",
        _CODEC_ENCODERS[codec],
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-shortest",
        str(output_path),
    ]


def _args_fingerprint(args: list[str]) -> str:
    payload = json.dumps(args, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _probe_fixture(path: Path) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(completed.stdout)
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
    ):
        return None


def _fraction(value: str) -> float:
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _fixture_matches(
    path: Path,
    metadata_path: Path,
    *,
    args: list[str],
    resolution: str,
    duration: float,
    fps: float,
    codec: str,
) -> bool:
    if not path.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, ValueError):
        return False
    if metadata.get("ffmpeg_args") != args:
        return False
    if metadata.get("ffmpeg_args_sha256") != _args_fingerprint(args):
        return False

    probe = _probe_fixture(path)
    if probe is None:
        return False
    stream = next(
        (item for item in probe.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if stream is None:
        return False
    width, height = (int(part) for part in resolution.split("x", maxsplit=1))
    actual_duration = float(probe.get("format", {}).get("duration", 0.0))
    actual_fps = _fraction(str(stream.get("avg_frame_rate", "0/1")))
    duration_tolerance = max(0.2, 2.0 / fps)
    return (
        stream.get("codec_name") == _PROBE_CODEC_NAMES[codec]
        and int(stream.get("width", 0)) == width
        and int(stream.get("height", 0)) == height
        and math.isclose(actual_fps, fps, abs_tol=0.01)
        and math.isclose(actual_duration, duration, abs_tol=duration_tolerance)
    )


def make_n_clips(
    fixtures_dir: Path,
    n: int,
    resolution: str = "1920x1080",
    duration: int | float = 5,
    fps: int | float = 30,
    codec: str = "h264",
) -> list[Path]:
    """Generate deterministic, probe-validated synthetic performance clips."""
    codec = codec.lower().replace("hevc", "h265")
    if codec not in _CODEC_ENCODERS:
        raise ValueError(f"Unsupported performance fixture codec: {codec}")
    if n < 1 or float(duration) <= 0 or float(fps) <= 0:
        raise ValueError("Fixture count, duration, and frame rate must be positive")
    width, height = (int(part) for part in resolution.split("x", maxsplit=1))
    if width <= 0 or height <= 0:
        raise ValueError("Fixture resolution must be positive")

    fixtures_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for i in range(n):
        filename = (
            f"perf_clip_{resolution}_{_number_token(duration)}s_"
            f"{_number_token(fps)}fps_{codec}_{i:02d}.mp4"
        )
        out = fixtures_dir / filename
        metadata_path = _fixture_metadata_path(out)
        temporary = out.with_name(f".{out.stem}.building{out.suffix}")
        args = _ffmpeg_args(
            temporary,
            resolution=resolution,
            duration=duration,
            fps=fps,
            codec=codec,
            index=i,
        )
        if not _fixture_matches(
            out,
            metadata_path,
            args=args,
            resolution=resolution,
            duration=float(duration),
            fps=float(fps),
            codec=codec,
        ):
            temporary.unlink(missing_ok=True)
            subprocess.run(
                args,
                check=True,
                capture_output=True,
                timeout=60,
            )
            temporary.replace(out)
            identity = {
                "codec": codec,
                "duration_seconds": float(duration),
                "frame_rate": float(fps),
                "height": height,
                "width": width,
            }
            metadata_path.write_text(
                json.dumps(
                    {
                        "ffmpeg_args": args,
                        "ffmpeg_args_sha256": _args_fingerprint(args),
                        "identity": identity,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        clips.append(out)
    return clips
