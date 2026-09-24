"""The real runtime preview/hash/facts path fetches only actual native demands."""

import pytest

from immich_memories.analysis.editorial_demanded_previews import DemandedPreviewReader
from immich_memories.analysis.editorial_structure_contract import StructurePlanningResult
from immich_memories.analysis.selection_trace import Trace
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from tests.test_editorial_picture_facts import preview
from tests.test_editorial_source_route_integration import setup_runtime


def test_default_runtime_shares_hash_and_picture_preview_then_replays_without_http(
    tmp_path, monkeypatch
):
    cache = ThumbnailCache(tmp_path / "previews")
    fetched, returned = [], []
    payload = preview("blue")

    def fetch(_client, asset_id):
        fetched.append(asset_id)
        return payload

    def native(source, effects):
        # Native hash and factual acquisition are independent demands. Both
        # reach the actual production callbacks; no effects factory override.
        ids = sorted({key for values in source.moment_asset_ids.values() for key in values})
        measured = effects.thumbnail_hash(ids[0])
        assert measured is not None
        facts = effects.observe_picture(ids[0])
        other = effects.observe_picture(ids[1])
        assert effects.thumbnail_hash(ids[1]) == measured
        assert effects.observe_picture(ids[0]) == facts
        returned.append(
            {
                "hash": measured,
                "facts": facts,
                "other": other,
                "metrics": effects.picture_facts_metrics(),
            }
        )
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

    sources, _, build, _, _, _, image_calls, warm = setup_runtime(
        tmp_path, monkeypatch, default_structure=native, thumbnail_cache=cache, fetch_preview=fetch
    )
    first = build().plan_source(sources, trace=Trace())
    assert fetched == ["p-00", "p-01"]
    assert len(image_calls) == 2  # Native per-source observation identities remain separate.
    assert returned[0]["facts"]["status"] == returned[0]["other"]["status"] == "available"
    metrics = returned[0]["metrics"]["preview_acquisition"]
    assert metrics["fetch_attempts"] == 2 and metrics["download_bytes"] == 2 * len(payload)
    assert metrics["fetch_seconds"] >= 0 and metrics["unavailable"] == 0
    assert cache.get("p-02", "preview") is None
    warm[0] = True

    def forbidden(*_args, **_kwargs):
        pytest.fail("warm acquisition reached external transport")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", forbidden)
    # A new runtime uses persistent positive preview and exact producer banks.
    second = build().plan_source(sources, trace=Trace())
    assert fetched == ["p-00", "p-01"]
    assert second.plan == first.plan
    assert {key: returned[0][key] for key in ("hash", "facts", "other")} == {
        key: returned[1][key] for key in ("hash", "facts", "other")
    }
    metrics = returned[1]["metrics"]["preview_acquisition"]
    assert metrics["fetch_attempts"] == metrics["download_bytes"] == 0
    assert metrics["cache_hits"] == 2
    assert returned[1]["metrics"]["http_attempts"] == 0


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
        facts = effects.observe_picture(key)
        assert facts["status"] == "unavailable"
        assert effects.observe_picture(key) == facts
        assert effects.thumbnail_hash(key) is None
        seen.append(effects.picture_facts_metrics())
        return StructurePlanningResult(
            {"carriers": [], "status": "insufficient_material"}, "", "", {}
        )

    sources, _, build, _, _, _, image_calls, _ = setup_runtime(
        tmp_path, monkeypatch, default_structure=native, thumbnail_cache=cache, fetch_preview=fetch
    )
    result = build().plan_source(sources, trace=Trace())
    assert not result.plan.selections
    assert len(fetched) == 1 and not image_calls
    assert seen[0]["preview_acquisition"]["unavailable"] == 1
    assert cache.get(fetched[0], "preview") is None
    build().plan_source(sources, trace=Trace())
    assert len(fetched) == 2  # No permanent negative media fact is fabricated.
    assert not image_calls


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


def test_actual_native_planner_empty_thumbnail_cache_then_exact_positive_warm(
    tmp_path, monkeypatch
):
    import io
    import random

    from PIL import Image

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

    monkeypatch.setattr(
        "immich_memories.analysis.editorial_runtime_backend.StructureTextJudge",
        lambda *_args, **_kwargs: ControlledStoryJudge(judgments, require_hits=replay[0]),
    )

    def run_native(source, effects):
        result = plan_structure(source, effects)
        native.append(result.plan)
        return result

    sources, _, build, _, _, _, images, warm = setup_runtime(
        tmp_path,
        monkeypatch,
        default_structure=run_native,
        thumbnail_cache=cache,
        fetch_preview=fetch,
    )
    cold = build().plan_source(sources, trace=Trace())
    assert cold.plan.selected_asset_ids
    assert len(fetched) == len(set(fetched)) == len(sources)
    assert len(images) >= len(cold.plan.selected_asset_ids)
    before = len(images)
    warm[0] = replay[0] = True

    def forbidden(*_args, **_kwargs):
        pytest.fail("exact native warm reached picture transport")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", forbidden)
    repeated = build().plan_source(sources, trace=Trace())
    assert repeated.plan == cold.plan
    # Cache transport counters differ; sampled duplicate decisions must not.
    cold_semantics = semantic_plan(native[0])
    warm_semantics = semantic_plan(native[1])
    assert warm_semantics.pop("sampled_pair_metrics")["actual_http_attempts"] == 0
    assert cold_semantics.pop("sampled_pair_metrics")["actual_http_attempts"] > 0
    assert warm_semantics == cold_semantics
    assert len(images) == before
    assert len(fetched) == len(sources)
