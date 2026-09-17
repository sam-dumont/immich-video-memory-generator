"""A few keyframes of a playback, read by byte range: the index and the keyframes, not the file."""

from __future__ import annotations

import io
import shutil
import struct
import subprocess

import pytest
from PIL import Image

from immich_memories.processing.playback_keyframes import keyframes_of, sample_keyframes


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full(kind: bytes, payload: bytes, version: int = 0) -> bytes:
    return box(kind, bytes([version, 0, 0, 0]) + payload)


def track(handler: bytes, *, sync: tuple[int, ...] | None, codec: bytes = b"avc1") -> bytes:
    """Six samples at 30 ticks a second, ten ticks apart, three to a chunk."""
    table = [
        full(b"stsd", struct.pack(">I", 1) + box(codec, b"\0" * 16)),
        full(b"stts", struct.pack(">III", 1, 6, 10)),
        full(b"stsc", struct.pack(">IIII", 1, 1, 3, 1)),
        full(b"stsz", struct.pack(">II6I", 0, 6, 10, 11, 12, 13, 14, 15)),
        full(b"stco", struct.pack(">III", 2, 100, 400)),
    ]
    if sync is not None:
        table.append(full(b"stss", struct.pack(f">I{len(sync)}I", len(sync), *sync)))
    media = (
        full(b"mdhd", struct.pack(">IIIIHH", 0, 0, 30, 60, 0, 0))
        + full(b"hdlr", b"\0" * 4 + handler + b"\0" * 13)
        + box(b"minf", box(b"stbl", b"".join(table)))
    )
    return box(b"trak", box(b"mdia", media))


def test_the_video_track_names_its_keyframes_with_their_time_offset_and_size():
    moov = track(b"soun", sync=None) + track(b"vide", sync=(1, 4))

    index = keyframes_of(moov)

    assert [(k.seconds, k.offset, k.size) for k in index.keyframes] == [
        (0.0, 100, 10),
        (1.0, 400, 13),
    ]
    assert (index.duration, index.codec) == (2.0, "avc1")


def test_a_track_without_a_sync_table_is_all_keyframes():
    index = keyframes_of(track(b"vide", sync=None, codec=b"mp4v"))

    assert [k.offset for k in index.keyframes] == [100, 110, 121, 400, 413, 427]
    assert index.codec == "mp4v"


def test_an_index_without_a_video_track_is_refused():
    with pytest.raises(ValueError, match="video track"):
        keyframes_of(track(b"soun", sync=None))


requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def encoder_available(name: str) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    listing = subprocess.run(  # noqa: S603 - fixed argv in a test
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    )
    return f" {name} " in listing.stdout


# Immich serves H.264 by default, with B-frames and an edit list; its other targets are HEVC,
# VP9 and AV1. MPEG-4 Part 2 is not one of them, and is the case the first-sample fetch covers.
CODECS = [
    pytest.param(["-c:v", "libx264", "-bf", "2", "-pix_fmt", "yuv420p"], id="h264"),
    pytest.param(["-c:v", "mpeg4", "-b:v", "3M"], id="mpeg4"),
]


def encode(path, *, gop: int, codec: list[str] | None = None, audio: bool = False) -> bytes:
    sound = ["-f", "lavfi", "-i", "sine=duration=6", "-c:a", "aac", "-shortest"] if audio else []
    subprocess.run(  # noqa: S603 - fixed argv in a test
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=640x360:rate=30:duration=6", *sound,
            *(codec or ["-c:v", "mpeg4", "-b:v", "3M"]), "-g", str(gop),
            "-movflags", "+faststart", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path.read_bytes()


def ranged(data: bytes, log: list[int]):
    def read(start: int, length: int) -> tuple[bytes, int]:
        log.append(length)
        return data[start : start + length], len(data)

    return read


@requires_ffmpeg
@pytest.mark.parametrize("codec", CODECS)
def test_three_keyframes_cost_a_fraction_of_the_playback(tmp_path, codec):
    if not encoder_available(codec[1]):
        pytest.skip(f"this ffmpeg has no {codec[1]} encoder")
    data = encode(tmp_path / "clip.mp4", gop=30, codec=codec, audio=True)
    log: list[int] = []

    sampled = sample_keyframes(ranged(data, log), count=3, width=320, workdir=tmp_path / "work")

    assert len(sampled.frames) == 3
    assert list(sampled.seconds) == sorted(set(sampled.seconds))
    for frame in sampled.frames:
        with Image.open(io.BytesIO(frame)) as image:
            assert image.format == "JPEG" and max(image.size) <= 320
    assert sampled.bytes_read == sum(log) < len(data) / 4
    assert sampled.requests == len(log)


@requires_ffmpeg
def test_a_clip_with_one_keyframe_is_read_whole_and_sampled_across_its_length(tmp_path):
    data = encode(tmp_path / "clip.mp4", gop=1000)
    log: list[int] = []

    sampled = sample_keyframes(ranged(data, log), count=3, width=320, workdir=tmp_path / "work")

    assert len(sampled.frames) == 3
    assert sampled.bytes_read >= len(data)
