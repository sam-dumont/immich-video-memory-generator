"""A film prepares the pictures it can reach, not the library window around them (#1181).

A person film over a lifetime window once prepared every picture in that window: 90k for a
7.5k-picture pool. What the film can select is the person's pictures. What its exposure rule
reads around them is their capture runs (``editorial_exposure_chains``), and a Live Photo
family is rendered whole. Everything else in the window is read as metadata for the
grouping, and never prepared.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
from immich_memories.analysis.editorial_runtime import EditorialRunContext, build_editorial_planner
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.smart_pipeline import SmartPipeline
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.test_editorial_rule_reader import _distinct_preview
from tests.test_editorial_source_route import photo

WINDOW = DateRange(datetime(2019, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, 23, 59, tzinfo=UTC))
PARTY = datetime(2023, 6, 10, 15, 0, tzinfo=UTC)


class _Library:
    """Six years of other people's pictures, and one afternoon where the subject appears."""

    def __init__(self) -> None:
        self.others = [
            photo(f"library-{n:03}", at=WINDOW.start + timedelta(days=7 * n, hours=10))
            for n in range(300)
        ]
        self.hers = [
            photo("hers-1", at=PARTY),
            photo("hers-2", at=PARTY + timedelta(minutes=2)),
            photo("hers-3", at=PARTY + timedelta(minutes=40)),
        ]
        for asset in self.hers:
            asset.is_favorite = True
        # Two minutes after one of hers: the same capture run, so its exposure flag
        # decides whether that run holds her picture.
        self.same_run = photo("same-run", at=PARTY + timedelta(minutes=4))
        # Twenty minutes later: the same afternoon, a different capture run. A starred
        # picture the film has no way to select.
        self.same_afternoon = photo("same-afternoon", at=PARTY + timedelta(minutes=20))
        self.same_afternoon.is_favorite = True

    @property
    def window(self):
        return sorted(
            [*self.others, *self.hers, self.same_run, self.same_afternoon],
            key=lambda asset: asset.file_created_at,
        )


def _film(tmp_path, library: _Library):
    prepared: list[str] = []

    def prepare(**kwargs):
        prepared.extend(asset.id for asset in kwargs["assets"])
        return prepare_editorial_annotations(**kwargs)

    config = Config(
        cache={"directory": str(tmp_path / "cache")},
        editorial={"reader": "rules", "preparation": {"tier": "metadata_only"}},
        analysis={"min_source_short_side": 0},
    )
    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=ThumbnailCache(tmp_path / "thumbnails"),
        context=EditorialRunContext(
            "person", "Her years", "person_spotlight", (WINDOW,), 60, tmp_path / "artifacts"
        ),
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=lambda *_: library.window,
            fetch_preview=lambda _client, key: _distinct_preview(key),
            prepare_annotations=prepare,
        ),
    )
    _, result = SmartPipeline(planner=planner).run_editorial_source(list(library.hers))
    return set(prepared), {clip.asset.id for clip in result.selected_clips}, planner


def test_a_person_film_prepares_her_pictures_and_their_capture_runs_only(tmp_path):
    library = _Library()

    prepared, _selected, planner = _film(tmp_path, library)

    assert prepared == {"hers-1", "hers-2", "hers-3", "same-run"}
    report = json.loads((planner.last_attempt_directory / "preparation.private.json").read_text())
    assert report["requested"] == 4


def test_a_film_selects_only_pictures_it_prepared(tmp_path):
    library = _Library()

    prepared, selected, _planner = _film(tmp_path, library)

    assert selected
    assert selected <= prepared
    assert "same-afternoon" not in prepared
