"""The runtime fetches only the previews a cut demands, and never sends one to a model."""

import dataclasses
import io

import pytest
from PIL import Image

from immich_memories.analysis.editorial_demanded_previews import DemandedPreviewReader
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningResult,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from tests.test_editorial_source_route_integration import setup_runtime


def preview(color="blue"):
    image = Image.new("RGB", (920, 630), color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def test_default_runtime_hashes_demanded_previews_then_replays_without_http(tmp_path, monkeypatch):
    cache = ThumbnailCache(tmp_path / "previews")
    fetched, returned = [], []
    payload = preview("blue")

    def fetch(_client, asset_id):
        fetched.append(asset_id)
        return payload

    def native(source, effects):
        ids = sorted({key for values in source.moment_asset_ids.values() for key in values})
        measured = effects.thumbnail_hash(ids[0])
        assert measured is not None
        assert effects.thumbnail_hash(ids[1]) == measured
        returned.append({"hash": measured, "metrics": effects.thumbnail_metrics()})
        return StructurePlanningResult(
            {
                "carriers": [
                    {
                        "asset_id": ids[0],
                        "kind": "still",
                        "taken": source.assets[ids[0]].file_created_at.isoformat(),
                        "seconds": 4,
                    }
                ]
            },
            "",
            "",
            {},
        )

    sources, _, build, _, _, image_calls, warm = setup_runtime(
        tmp_path, monkeypatch, default_structure=native, thumbnail_cache=cache, fetch_preview=fetch
    )
    first = build().plan_source(sources, trace=Trace())
    assert fetched == ["p-00", "p-01"]
    metrics = returned[0]["metrics"]["preview_acquisition"]
    assert metrics["fetch_attempts"] == 2 and metrics["download_bytes"] == 2 * len(payload)
    assert metrics["fetch_seconds"] >= 0 and metrics["unavailable"] == 0
    assert cache.get("p-02", "preview") is None
    warm[0] = True

    second = build().plan_source(sources, trace=Trace())
    assert fetched == ["p-00", "p-01"]
    assert second.plan == first.plan
    assert returned[1]["hash"] == returned[0]["hash"]
    metrics = returned[1]["metrics"]["preview_acquisition"]
    assert metrics["fetch_attempts"] == metrics["download_bytes"] == 0
    assert metrics["cache_hits"] == 2
    assert not any(image_calls)


@pytest.mark.parametrize("failure", [None, b"not an image", OSError("offline")])
def test_default_runtime_missing_preview_is_unavailable_and_retries_only_in_fresh_run(
    tmp_path, monkeypatch, failure
):
    cache = ThumbnailCache(tmp_path / "previews")
    fetched, seen = [], []

    def fetch(_client, asset_id):
        fetched.append(asset_id)
        if isinstance(failure, Exception):
            raise failure
        return failure

    def native(source, effects):
        key = next(iter(source.moment_asset_ids.values()))[0]
        assert effects.thumbnail_hash(key) is None
        assert effects.thumbnail_hash(key) is None
        seen.append(effects.thumbnail_metrics())
        return StructurePlanningResult(
            {"carriers": [], "status": "insufficient_material"}, "", "", {}
        )

    sources, _, build, _, _, image_calls, _ = setup_runtime(
        tmp_path, monkeypatch, default_structure=native, thumbnail_cache=cache, fetch_preview=fetch
    )
    result = build().plan_source(sources, trace=Trace())
    assert not result.plan.selections
    assert len(fetched) == 1 and not any(image_calls)
    assert seen[0]["preview_acquisition"]["unavailable"] == 1
    assert cache.get(fetched[0], "preview") is None
    build().plan_source(sources, trace=Trace())
    assert len(fetched) == 2  # No permanent negative media fact is fabricated.


def test_reader_rejects_uncaptured_source_before_fetch(tmp_path):
    fetched = []
    reader = DemandedPreviewReader(
        ThumbnailCache(tmp_path / "previews"),
        lambda key: fetched.append(key),
        allowed_ids={"captured"},
    )
    with pytest.raises(ValueError, match="outside"):
        reader("foreign")
    assert not fetched


def test_no_planner_port_can_carry_a_picture_to_a_model():
    """The ports a film is planned through hold hashes, prints and text readers, nothing that
    observes a picture: pictures are read once, at ingest."""
    names = {field.name for field in dataclasses.fields(StructurePlannerPorts)}

    assert not {name for name in names if "observe_picture" in name or "attached" in name}
    assert not {name for name in names if "picture_facts" in name or "pairs" in name}


def test_a_model_film_sends_no_picture_to_any_model_cold_or_warm(tmp_path, monkeypatch):
    """The production model-tier route, end to end over real previews: every request that
    reaches the wire is checked, and none carries a picture."""
    import random

    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.editorial_story_fixtures import ControlledStoryJudge
    from tests.test_editorial_duration_planner_integration import semantic_plan

    cache = ThumbnailCache(tmp_path / "previews")
    fetched, native = [], []
    judgments = {}
    replay = [False]

    def fetch(_client, key):
        assert not replay[0], "positive warm preview escaped persistent cache"
        fetched.append(key)
        # Deterministic distinct pictures exercise actual aHash instead of
        # disabling its caller or giving every source the same duplicate tile.
        image = Image.frombytes("RGB", (64, 64), random.Random(key).randbytes(64 * 64 * 3))
        output = io.BytesIO()
        image.save(output, "JPEG")
        return output.getvalue()

    # WHY: the text reader's provider; it answers from a script and is text by construction.
    monkeypatch.setattr(
        "immich_memories.analysis.editorial_runtime_backend.StructureTextJudge",
        lambda *_args, **_kwargs: ControlledStoryJudge(judgments, require_hits=replay[0]),
    )

    def run_native(source, effects):
        result = plan_structure(source, effects)
        native.append(result.plan)
        return result

    sources, _, build, _, _, images, warm = setup_runtime(
        tmp_path,
        monkeypatch,
        default_structure=run_native,
        thumbnail_cache=cache,
        fetch_preview=fetch,
    )
    cold = build().plan_source(sources, trace=Trace())
    assert cold.plan.selected_asset_ids
    assert len(fetched) == len(set(fetched)) == len(sources)
    assert not any(images)
    assert "picture_facts" not in native[0]
    warm[0] = replay[0] = True

    repeated = build().plan_source(sources, trace=Trace())
    assert repeated.plan == cold.plan
    assert semantic_plan(native[1]) == semantic_plan(native[0])
    assert not any(images)
    assert len(fetched) == len(sources)
