"""CLI album-mode helpers (#270)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from immich_memories.api.album_service import AlbumRef
from immich_memories.api.models import Asset, AssetType
from immich_memories.cli._album_generation import album_output_path, handle_album_generation
from immich_memories.config_loader import Config


def test_output_filename_is_built_from_the_album_name():
    path = album_output_path(Path("/out/all_memories_2025.mp4"), "Trip 2025", "mp4")

    assert path == Path("/out/album_trip_2025.mp4")


def test_accented_album_names_survive_as_a_usable_filename():
    """Sam's albums are French — 'Récentes' must not become an empty slug."""
    path = album_output_path(Path("/out/x.mp4"), "Val d'Aoste 2021", "mkv")

    assert path == Path("/out/album_val_d_aoste_2021.mkv")


def test_a_name_with_no_usable_characters_still_yields_a_filename():
    path = album_output_path(Path("/out/x.mp4"), "***", "mp4")

    assert path == Path("/out/album.mp4")


class _Progress:
    """WHY: replaces the terminal progress display."""

    def add_task(self, *_args, **_kwargs):
        return 1

    def update(self, *_args, **_kwargs):
        return None

    def stop(self):
        return None


class _Client:
    """WHY: replaces the Immich API."""

    def __init__(self, album: AlbumRef, videos, images):
        self._album = album
        self._by_type = {AssetType.VIDEO: videos, AssetType.IMAGE: images}

    def resolve_album(self, _name_or_id):
        return self._album

    def get_assets_for_album(self, _album_id, *, asset_type, limit=None, progress_callback=None):
        return self._by_type[asset_type]


def _asset(asset_id: str, asset_type: AssetType, created: datetime) -> Asset:
    return Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=created,
        fileModifiedAt=created,
        updatedAt=created,
        width=1920,
        height=1080,
    )


def _run_album(monkeypatch, videos, images, **overrides):
    """Drive handle_album_generation, capturing what it hands the pipeline."""
    captured: dict = {}

    def _fake_pipeline(**kwargs):
        captured.update(kwargs)
        return Path("/out/album_trip_2025.mp4"), False, None

    # WHY: replaces the whole analysis + assembly pipeline.
    monkeypatch.setattr(
        "immich_memories.cli._pipeline_runner.run_pipeline_and_generate", _fake_pipeline
    )
    album = AlbumRef(id="a-1", name="Trip 2025", asset_count=len(videos) + len(images))
    kwargs = {
        "client": _Client(album, videos, images),
        "config": Config(),
        "progress": _Progress(),
        "album_ref": "Trip 2025",
        "person_names": [],
        "output_path": Path("/out/all_memories.mp4"),
        "use_live_photos": False,
        "use_photos": True,
        "effective_analysis_depth": "auto",
        "transition": "fade",
        "music": None,
        "music_volume": 0.5,
        "no_music": True,
        "resolution": "4k",
        "scale_mode": None,
        "output_format": None,
        "add_date": False,
        "keep_intermediates": False,
        "privacy_mode": False,
        "title_override": None,
        "subtitle_override": None,
        "upload_to_immich": False,
        "album": None,
    }
    kwargs.update(overrides)
    handle_album_generation(**kwargs)
    return captured


def test_the_album_supplies_the_pool_the_title_and_the_span(monkeypatch):
    videos = [_asset("v1", AssetType.VIDEO, datetime(2025, 7, 1, tzinfo=UTC))]
    images = [_asset("p1", AssetType.IMAGE, datetime(2025, 7, 9, tzinfo=UTC))]

    captured = _run_album(monkeypatch, videos, images)

    assert [a.id for a in captured["assets"]] == ["v1"]
    assert [a.id for a in captured["photo_assets"]] == ["p1"]
    assert captured["memory_type"] == "album"
    assert captured["title_override"] == "Trip 2025"
    assert captured["date_range"].start == datetime(2025, 7, 1, tzinfo=UTC)
    assert captured["date_range"].end == datetime(2025, 7, 9, tzinfo=UTC)
    assert captured["output_path"] == Path("/out/album_trip_2025.mp4")


def test_an_explicit_title_still_wins_over_the_album_name(monkeypatch):
    videos = [_asset("v1", AssetType.VIDEO, datetime(2025, 7, 1, tzinfo=UTC))]

    captured = _run_album(monkeypatch, videos, [], title_override="Something Else")

    assert captured["title_override"] == "Something Else"


def test_an_album_with_no_usable_media_stops_the_run(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run_album(monkeypatch, [], [])

    assert exc.value.code == 1
