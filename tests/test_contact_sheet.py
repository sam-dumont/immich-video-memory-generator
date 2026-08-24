"""The review sheet has to show the clips, or it cannot review them.

A grey rectangle is the one thing a reviewer cannot judge, and Immich has no
thumbnail for roughly one asset in twelve on a real library — all of them
standalone footage the pipeline picked on merit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import contact_sheet  # noqa: E402


def _clip(path: Path, seconds: float) -> Path:
    """A real decodable clip, so ffmpeg does real work on a real duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x120:rate=30:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],  # fmt: skip
        check=True,
        capture_output=True,
    )
    return path


def _cached(root: Path, asset_id: str, name: str) -> Path:
    sub = root / "video-cache" / asset_id[:2]
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / name
    path.write_bytes(b"stand-in for footage")
    return path


def _cache_at(monkeypatch, root: Path) -> None:
    # WHY: get_config reads the user's real config file; the sheet must look
    # where that file points rather than at a guess.
    monkeypatch.setattr(
        "immich_memories.config.get_config",
        lambda: SimpleNamespace(cache=SimpleNamespace(video_cache_path=root / "video-cache")),
    )


def test_the_frame_fallback_reads_the_configured_cache_directory(tmp_path, monkeypatch) -> None:
    """It hardcoded a home-relative path while already importing get_config."""
    wanted = _cached(tmp_path, "ab12cd34", "ab12cd34.mp4")
    _cache_at(monkeypatch, tmp_path)

    assert contact_sheet._cached_video("ab12cd34") == wanted


def test_an_unfinished_download_is_not_a_frame_source(tmp_path, monkeypatch) -> None:
    """A .part is a download in flight — the cache reader skips these too."""
    _cached(tmp_path, "ab12cd34", "ab12cd34.mp4.part")
    _cache_at(monkeypatch, tmp_path)

    assert contact_sheet._cached_video("ab12cd34") is None


def test_a_live_photos_footage_is_found_under_its_video_component(tmp_path, monkeypatch) -> None:
    """The still's id is never what the footage was cached as."""
    wanted = _cached(tmp_path, "99videoid", "99videoid.mov")
    _cache_at(monkeypatch, tmp_path)

    assert contact_sheet._cached_video("00stillid", "99videoid") == wanted


def test_a_clip_shorter_than_the_seek_still_renders_a_frame(tmp_path, monkeypatch) -> None:
    """The extractor seeked half a second in, so shorter footage decoded to nothing.

    A trimmed Live Photo burst is routinely under half a second. ffmpeg exits
    non-zero with no output file, which the tile-level except swallowed, and the
    clip came back blank on a sheet whose whole job is to show it.
    """
    _clip(tmp_path / "video-cache" / "aa" / "aashort.mp4", seconds=0.3)
    _cache_at(monkeypatch, tmp_path)

    assert contact_sheet._frame_from_cache("aashort") is not None


class _ServerWithoutThumbnails:
    """WHY: Immich is the external boundary — this is a server that holds no
    thumbnail for the asset, the 8% of a real library the fallback exists for."""

    def get_asset_thumbnail(self, asset_id: str, size: str) -> bytes:
        raise LookupError(f"no {size} for {asset_id}")


def test_a_clip_with_no_frame_anywhere_says_so(tmp_path, monkeypatch) -> None:
    """Nothing on the server and nothing cached is its own failure."""
    _cache_at(monkeypatch, tmp_path)

    image, reason = contact_sheet._thumbnail(_ServerWithoutThumbnails(), "aamissing")

    assert image is None
    assert reason == "no thumbnail"


def test_a_cached_clip_that_will_not_decode_is_named_apart(tmp_path, monkeypatch) -> None:
    """A tile can be blank two ways, and the reviewer has to be told which.

    Footage the server never thumbnailed is expected; footage sitting in the
    cache that ffmpeg cannot read is a broken download worth chasing.
    """
    broken = tmp_path / "video-cache" / "aa" / "aabroken.mp4"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"not a video at all")
    _cache_at(monkeypatch, tmp_path)

    image, reason = contact_sheet._thumbnail(_ServerWithoutThumbnails(), "aabroken")

    assert image is None
    assert reason == "would not decode"
