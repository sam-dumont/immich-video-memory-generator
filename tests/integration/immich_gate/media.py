"""The files the gate uploads into a real Immich: the CC0 fixture month and a paging album.

Two sets, both public and synthetic, never anyone's library:

* The June 2024 household of ``tests/e2e/fake_library.py``: every still becomes a
  1920x1280 JPEG carrying a camera and its capture time in EXIF (selection drops
  a still that names no camera), every video scene a 1080p pan across its
  photograph with a tone under it, stamped with the same time.
* ``BULK_COUNT`` tiny, distinct JPEGs in March 2019 for the album that has to
  page: Immich answers a metadata search at most 1000 items at a time.

Building takes about fifteen seconds of FFmpeg, so the result is kept under ``root`` with a
fingerprint of its inputs and reused until those change (CI caches the folder).
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps

from tests.e2e.fake_library import ALL_PICTURES, Picture

VIDEO_SIZE = (1920, 1080)
PHOTO_SIZE = (1920, 1280)
CAMERA = ("FakeCam", "Hermetic One")

# One more than a full page of Immich's metadata search, plus a margin, so the
# album and its year can only be read whole by asking for a second page.
BULK_COUNT = 1010
BULK_START = datetime(2019, 3, 1, 9, 0, tzinfo=UTC)
BULK_ALBUM = "Gate paging album"

_EXIF_MAKE = 0x010F
_EXIF_MODEL = 0x0110
_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 0x9003
_OFFSET_TIME_ORIGINAL = 0x9011
_PAN_HEADROOM = 1.25


@dataclass(frozen=True, slots=True)
class GateFile:
    """One file to upload, and when its content happened."""

    path: Path
    taken_at: datetime
    picture: Picture | None = None


def taken_at(picture: Picture) -> datetime:
    return datetime.fromisoformat(picture.taken_at.replace("Z", "+00:00"))


def _fingerprint() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for picture in ALL_PICTURES:
        digest.update(f"{picture.asset_id}|{picture.filename}|{picture.taken_at}".encode())
        digest.update(picture.source.read_bytes())
    return digest.hexdigest()


def _exif(moment: datetime, *, camera: bool) -> Image.Exif:
    exif = Image.Exif()
    if camera:
        exif[_EXIF_MAKE], exif[_EXIF_MODEL] = CAMERA
    details = exif.get_ifd(_EXIF_IFD)
    details[_DATETIME_ORIGINAL] = moment.strftime("%Y:%m:%d %H:%M:%S")
    details[_OFFSET_TIME_ORIGINAL] = "+00:00"
    return exif


def _write_photo(picture: Picture, target: Path) -> None:
    with Image.open(picture.source) as source:
        fitted = ImageOps.fit(source.convert("RGB"), PHOTO_SIZE)
    fitted.save(target, "JPEG", quality=85, exif=_exif(taken_at(picture), camera=True))


def _write_video(picture: Picture, target: Path, tone: int) -> None:
    width, height = VIDEO_SIZE
    stage_w, stage_h = round(width * _PAN_HEADROOM), round(height * _PAN_HEADROOM)
    seconds = str(picture.seconds)
    subprocess.run(  # noqa: S603 -- fixed argv, fixture paths only
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(picture.source),
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000:duration={seconds}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            seconds,
            "-vf",
            (
                f"scale={stage_w}:{stage_h}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:x='(in_w-out_w)*t/{seconds}',"
                "scale=in_range=full:out_range=tv,format=yuv420p"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-color_range",
            "tv",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-c:a",
            "aac",
            "-shortest",
            "-metadata",
            f"creation_time={picture.taken_at}",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_bulk(index: int, target: Path) -> None:
    # WHY distinct pixels: Immich refuses a second upload of the same checksum,
    # so each of the thousand needs content of its own.
    colour = (index % 256, (index // 256) * 40 % 256, (index * 7) % 256)
    Image.new("RGB", (32, 32), colour).save(
        target, "JPEG", quality=95, exif=_exif(bulk_taken_at(index), camera=False)
    )


def bulk_taken_at(index: int) -> datetime:
    return BULK_START + timedelta(minutes=index)


def library_files(root: Path) -> list[GateFile]:
    return [
        GateFile(root / "library" / picture.filename, taken_at(picture), picture)
        for picture in ALL_PICTURES
    ]


def bulk_files(root: Path) -> list[GateFile]:
    return [
        GateFile(root / "bulk" / f"GATE_BULK_{index:04d}.jpg", bulk_taken_at(index))
        for index in range(BULK_COUNT)
    ]


def build(root: Path) -> None:
    """Write every gate file under ``root`` unless an identical build is already there."""
    stamp = root / "fingerprint"
    fingerprint = _fingerprint()
    if stamp.exists() and stamp.read_text() == fingerprint:
        return
    (root / "library").mkdir(parents=True, exist_ok=True)
    (root / "bulk").mkdir(parents=True, exist_ok=True)
    videos = 0
    for gate_file in library_files(root):
        picture = gate_file.picture
        assert picture is not None
        if picture.is_video:
            _write_video(picture, gate_file.path, tone=440 + videos * 110)
            videos += 1
        else:
            _write_photo(picture, gate_file.path)
    for index, gate_file in enumerate(bulk_files(root)):
        _write_bulk(index, gate_file.path)
    stamp.write_text(fingerprint)
