"""The review sheet has to show the clips, or it cannot review them.

A grey rectangle is the one thing a reviewer cannot judge, and Immich has no
thumbnail for roughly one asset in twelve on a real library — all of them
standalone footage the pipeline picked on merit.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import contact_sheet  # noqa: E402


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
