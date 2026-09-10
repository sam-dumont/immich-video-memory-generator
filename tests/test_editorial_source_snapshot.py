"""An attempt retains the exact source DTOs without acquiring or selecting again.

The matrix replays stay on the probe branch.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_runtime import (
    EditorialRunContext,
    build_editorial_planner,
)
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_source_snapshot import (
    SNAPSHOT_NAME,
    AttemptSourceSnapshots,
    load_sources,
)
from immich_memories.analysis.selection_source import SourceScope
from immich_memories.analysis.selection_trace import Trace
from immich_memories.api.models import AssetType, Person, VideoClipInfo
from immich_memories.api.person_expression import PersonExpression
from immich_memories.config import Config
from immich_memories.security import write_secret_file
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset

NOW = datetime(2024, 6, 3, 17, 0, 20, 466000, tzinfo=UTC)
EXPRESSION = PersonExpression.parse('("Adult A" OR "Adult B") AND "Child"')


def _sources():
    companion = make_asset("companion", file_created_at=NOW).model_copy(
        update={"duration_seconds": 2.731, "width": 1440, "height": 1920}
    )
    photo = make_asset("photo", file_created_at=NOW).model_copy(
        update={
            "type": AssetType.IMAGE,
            "live_photo_video_id": companion.id,
            "width": 3024,
            "height": 4032,
            "duration_seconds": None,
            "people": [Person(id="p1", name="Adult A", birthDate="1962-04-02T00:00:00Z")],
            "checksum": "known-image-identity",
        }
    )
    clip = VideoClipInfo(
        asset=companion,
        duration_seconds=2.731,
        width=1440,
        height=1920,
        rotation=90,
        color_transfer="arib-std-b67",
        bit_depth=10,
        live_burst_material={"entries": [{"still_asset_id": photo.id, "end": 2.731}]},
        safe_cut_gaps=[(0.13, 2.42)],
        audio_categories=["speech"],
    )
    # Repeated source rows and a wrapper are meaningful input, not a set to normalize.
    return (photo, clip, companion, photo)


def test_snapshot_identity_covers_scope_expression_and_companion_duration(tmp_path):
    sources = _sources()
    scope = SourceScope(date_ranges=(DateRange(NOW, NOW),), asset_ids=("photo",))
    snapshots = AttemptSourceSnapshots()
    snapshots.capture(
        sources,
        directory=tmp_path,
        scope=scope,
        person_expression=EXPRESSION,
        people=EXPRESSION.leaf_values,
        person_match="or",
        owner_excluded_asset_ids=("excluded",),
    )
    path = tmp_path / SNAPSHOT_NAME
    data = json.loads(path.read_text())
    assert load_sources(path) == sources
    assert data["requested_scope"]["date_ranges"] == [[NOW.isoformat(), NOW.isoformat()]]
    assert data["requested_scope"]["asset_ids"] == ["photo"]
    assert data["person_expression"] == EXPRESSION.to_dict()
    assert data["people"] == list(EXPRESSION.leaf_values)
    assert data["person_match"] == "or"
    assert data["owner_excluded_asset_ids"] == ["excluded"]
    assert data["linked_companion_count"] == data["captured_companion_count"] == 1
    assert data["missing_companion_ids"] == []
    data["sources"][1]["value"]["asset"]["duration_seconds"] = 3.0
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="content SHA256"):
        load_sources(path)


def test_absent_companion_is_recorded_without_fabricating_a_source(tmp_path):
    photo = _sources()[0]
    AttemptSourceSnapshots().capture((photo,), directory=tmp_path, scope=SourceScope())
    data = json.loads((tmp_path / SNAPSHOT_NAME).read_text())
    assert data["count"] == 1
    assert data["captured_companion_count"] == 0
    assert data["missing_companion_ids"] == ["companion"]
    assert load_sources(tmp_path / SNAPSHOT_NAME) == (photo,)


@pytest.mark.parametrize("album", [False, True])
def test_runtime_captures_once_per_attempt_without_refetch_or_mutation(tmp_path, album):
    sources = _sources()
    original_dtos = [source.model_dump(mode="json") for source in sources]
    context = EditorialRunContext(
        key="snapshot",
        label="A request",
        product="album" if album else "multi_person",
        date_ranges=() if album else (DateRange(NOW, NOW),),
        target_seconds=60,
        artifact_dir=tmp_path / "runs",
        person_expression=EXPRESSION,
        album_ref="album-id" if album else None,
        album_sources=sources if album else (),
    )
    fetches = []

    def fetch(_client, scope):
        assert not album, "preloaded album sources must not be fetched again"
        fetches.append(scope)
        return sources

    planner = build_editorial_planner(
        client=object(),
        thumbnail_cache=object(),
        context=context,
        config=Config(
            llm={"model": "no-model-calls"}, cache={"directory": str(tmp_path / "cache")}
        ),
        ports=EditorialRuntimePorts(load_people=lambda: {}, fetch_full_source=fetch),
    )
    captured_paths = []

    def write(path, text):
        captured_paths.append(path)
        write_secret_file(path, text)

    def stop_after_source(*_args, **_kwargs):
        scope = planner._planner._selection_request.scope
        source_fetcher = planner._planner._source_dependencies.source_fetcher
        first = source_fetcher(scope)
        assert source_fetcher(scope) is first
        assert first == sources
        return SimpleNamespace(plan=EditorialPlan(), duration_realization=None)

    # WHY: stops the run at the source fetch, and watches the one filesystem write it makes.
    with (
        # WHY: planning past the source would need the model provider; this test ends at the fetch.
        patch.object(planner, "_plan_source", side_effect=stop_after_source),
        patch(
            "immich_memories.analysis.editorial_source_snapshot.write_secret_file",
            side_effect=write,
        ),
    ):
        for _ in range(2):
            planner.plan_source(sources, trace=Trace())
    assert len(fetches) == (0 if album else 1)
    assert len(captured_paths) == len(set(captured_paths)) == 2
    assert not (context.artifact_dir / SNAPSHOT_NAME).exists()
    for path in captured_paths:
        assert path.parent.parent.name == "attempts"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert load_sources(path) == sources
    assert [source.model_dump(mode="json") for source in sources] == original_dtos
    planner.close()


def test_failed_atomic_write_does_not_mark_attempt_as_captured(tmp_path):
    snapshots = AttemptSourceSnapshots()
    # WHY: the atomic snapshot write is the filesystem boundary; OSError is the failure to force.
    with (
        # WHY: makes the real 0600 file write fail without needing an unwritable directory.
        patch(
            "immich_memories.analysis.editorial_source_snapshot.write_secret_file",
            side_effect=OSError,
        ),
        pytest.raises(OSError),
    ):
        snapshots.capture(_sources(), directory=tmp_path, scope=SourceScope())
    snapshots.capture(_sources(), directory=tmp_path, scope=SourceScope())
    assert load_sources(tmp_path / SNAPSHOT_NAME) == _sources()
