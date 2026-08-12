"""Assembly-specific fixtures: multi-clip sets at various resolutions."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

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


def _fixture_identity(source_args: list[str], encoder_args: list[str]) -> dict[str, object]:
    """Return the persisted identity for a synthetic benchmark fixture."""
    arguments = tuple(source_args + encoder_args)
    identity_hash = hashlib.sha256("\0".join(arguments).encode()).hexdigest()
    return {
        "identity_hash": identity_hash,
        "source_args": source_args,
        "encoder_args": encoder_args,
    }


def _fixture_matches(
    path: Path,
    metadata_path: Path,
    identity: dict[str, object],
    *,
    resolution: str,
    duration: int,
    fps: int,
    codec: str,
) -> bool:
    """Validate cached fixture identity and media properties before reuse."""
    if not path.exists() or not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("identity_hash") != identity["identity_hash"]:
            return False
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        data = json.loads(probe.stdout)
        video = next(stream for stream in data["streams"] if stream.get("codec_type") == "video")
        width, height = (int(value) for value in resolution.split("x", maxsplit=1))
        numerator, denominator = (
            int(value) for value in video["avg_frame_rate"].split("/", maxsplit=1)
        )
        expected_codec = {"h264": "h264", "h265": "hevc", "prores": "prores"}[codec]
        return (
            video["codec_name"] == expected_codec
            and int(video["width"]) == width
            and int(video["height"]) == height
            and abs(numerator / denominator - fps) < 0.01
            and abs(float(data["format"]["duration"]) - duration) <= 0.2
        )
    except (
        KeyError,
        StopIteration,
        ValueError,
        ZeroDivisionError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ):
        return False


def make_n_clips(
    fixtures_dir: Path,
    n: int,
    resolution: str = "1920x1080",
    duration: int = 5,
    fps: int = 30,
    codec: str = "h264",
) -> list[Path]:
    """Generate n validated synthetic clips at a given resolution. Deterministic."""
    encoders = {"h264": "libx264", "h265": "libx265"}
    try:
        encoder = encoders[codec]
    except KeyError as exc:
        raise ValueError("Benchmark fixtures support only h264 and h265 MP4 output") from exc
    clips = []
    for i in range(n):
        out = fixtures_dir / f"perf_clip_{resolution}_{duration}s_{fps}fps_{codec}_{i:02d}.mp4"
        metadata_path = out.with_suffix(".json")
        freq = 220 + i * 110  # Different audio per clip
        alpha = 40 + i * 30  # Different visual per clip
        source_args = [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={resolution}:rate={fps}:duration={duration}:alpha={alpha % 256}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
        ]
        encoder_args = [
            "-c:v",
            encoder,
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
        identity = _fixture_identity(source_args, encoder_args)
        if not _fixture_matches(
            out,
            metadata_path,
            identity,
            resolution=resolution,
            duration=duration,
            fps=fps,
            codec=codec,
        ):
            subprocess.run(
                ["ffmpeg", "-y", *source_args, *encoder_args, str(out)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            metadata_path.write_text(json.dumps(identity, indent=2) + "\n")
        clips.append(out)
    return clips
