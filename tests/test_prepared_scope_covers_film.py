"""`prepare` over a scope banks exactly the pictures a film over that scope reads (#1152)."""

from __future__ import annotations

from datetime import timedelta

import click
from click.testing import CliRunner

from immich_memories.analysis import editorial_preparation
from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
from immich_memories.analysis.editorial_runtime import EditorialRunContext, build_editorial_planner
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.selection_trace import Trace
from immich_memories.cli.prepare_cmd import register_prepare_commands
from immich_memories.config_loader import Config
from tests.conftest import make_asset
from tests.test_editorial_preparation import preview, successful_ports
from tests.test_editorial_runtime import _window
from tests.test_editorial_source_route import photo

WINDOW = _window(2020, 5, 2)


class _Library:
    """An Immich library of one day: two camera pictures and a film this app uploaded."""

    def __init__(self) -> None:
        start = WINDOW.start
        self.photos = [
            photo("breakfast", at=start + timedelta(hours=9)),
            photo("walk", at=start + timedelta(hours=11)),
        ]
        self.videos = [make_asset("our-film", file_created_at=start + timedelta(hours=20))]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_photos_for_date_range(self, _window, progress_callback=None, **_people):
        return list(self.photos)

    def get_videos_for_date_range(self, _window):
        return list(self.videos)

    def generated_asset_ids(self):
        return frozenset({"our-film"})

    def get_asset_thumbnail(self, _asset_id, size="preview"):
        return preview()

    def get_asset_faces(self, _asset_id):
        return []

    def get_video_playback_range(self, _asset_id, _start, _length):
        raise OSError("no playback in this library")


def _config(tmp_path) -> Config:
    return Config(
        immich={"url": "http://immich.test", "api_key": "key"},
        llm={"model": "offline-editor"},
        cache={"directory": str(tmp_path / "cache")},
        analysis={"min_source_short_side": 0},
    )


def _prepare(library, config, monkeypatch, produced) -> None:
    # WHY: `prepare` opens an HTTP client on Immich; the fake library stands in for the server.
    monkeypatch.setattr(
        "immich_memories.api.sync_client.SyncImmichClient", lambda **_kwargs: library
    )
    # WHY: the real producers load vision models; these bank a row per picture and log the call.
    monkeypatch.setattr(
        editorial_preparation, "PreparationPorts", lambda: successful_ports(produced)
    )
    group = click.Group()
    register_prepare_commands(group)
    result = CliRunner().invoke(
        group,
        ["prepare", "--start", "2020-05-02", "--end", "2020-05-02"],
        obj={"config": config},
    )
    assert result.exit_code == 0, result.output


def _film(library, config, tmp_path, produced, monkeypatch) -> None:
    planner = build_editorial_planner(
        client=library,
        config=config,
        thumbnail_cache=config.cache.cache_path / "thumbnails",
        context=EditorialRunContext(
            "day", "A day", "monthly_highlights", (WINDOW,), 60, tmp_path / "runs"
        ),
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            prepare_annotations=lambda **kwargs: prepare_editorial_annotations(
                **kwargs, ports=successful_ports(produced)
            ),
        ),
    )
    monkeypatch.setattr(planner._planner, "plan_prepared", lambda *_a, **_k: EditorialPlan())
    planner.plan_source([*library.photos, *library.videos], trace=Trace())


def test_a_film_over_a_prepared_scope_makes_no_preparation_call(tmp_path, monkeypatch):
    library, config, produced = _Library(), _config(tmp_path), []
    _prepare(library, config, monkeypatch, produced)
    assert produced, "prepare produced nothing to reuse"
    produced.clear()

    _film(library, config, tmp_path, produced, monkeypatch)

    assert produced == []


def test_prepare_leaves_out_the_films_this_app_uploaded_as_a_film_does(tmp_path, monkeypatch):
    library, config, produced = _Library(), _config(tmp_path), []
    _prepare(library, config, monkeypatch, produced)

    prepared = {a for producer, ids in produced if producer != "detectors" for a in ids}
    assert prepared == {"breakfast", "walk"}
