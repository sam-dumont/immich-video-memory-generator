"""Cached hashes measure exact banked pictures, never asset-only or remote previews."""

import json
import sqlite3
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from immich_memories.analysis import editorial_gateway
from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash
from immich_memories.analysis.editorial_picture_facts import PROMPT, PictureFactsProvider
from immich_memories.analysis.editorial_thumbnail_hashes import METHOD
from immich_memories.analysis.selection_trace import Trace
from tests.test_editorial_sampled_pair_confirmation import confirmer, replies
from tests.test_editorial_sampled_pair_confirmation import observed as observed


def test_exact_hash_method_is_cached_without_pair_calls_or_warm_computation(observed, monkeypatch):
    calls = replies(monkeypatch, [])
    original = deepcopy(observed["records"])
    adapter = confirmer(observed)
    result = adapter.preview_hashes(("one", "two", "one"), observed["records"])
    expected = {
        asset: compute_thumbnail_hash(
            (
                observed["image_dir"] / f"{observed['records'][asset]['image_sha256']}.jpg"
            ).read_bytes()
        )
        for asset in ("one", "two")
    }
    assert result == expected
    assert all(len(value) == 16 for value in result.values())
    metrics = adapter.metrics()
    assert metrics["preview_hash_method"] == METHOD
    assert metrics["preview_hashes_requested"] == metrics["preview_hashes_computed"] == 2
    assert metrics["preview_hashes_cache_hits"] == metrics["nominated_pairs"] == 0
    assert (
        metrics["actual_http_attempts"]
        == metrics["motion_decodes"]
        == metrics["preview_downloads"]
        == 0
    )
    assert (observed["root"] / "sampled-preview-hashes.sqlite").is_file()
    assert not (observed["root"] / "pair-sheets").exists()
    adapter.close()

    def forbidden(*_args, **_kwargs):
        pytest.fail("cached sampled hash decoded its pixels again")

    monkeypatch.setattr(
        "immich_memories.analysis.editorial_thumbnail_hashes.compute_thumbnail_hash", forbidden
    )
    replay = confirmer(observed)
    assert replay.preview_hashes(("two", "one"), observed["records"]) == result
    assert replay.metrics()["preview_hashes_computed"] == 0
    assert replay.metrics()["preview_hashes_cache_hits"] == 2
    assert calls == [] and observed["records"] == original
    replay.close()


def test_changed_banked_image_for_same_asset_has_a_new_key_and_hash(observed, monkeypatch):
    adapter = confirmer(observed)
    old = adapter.preview_hashes(("one",), observed["records"])
    cache = observed["root"] / "sampled-preview-hashes.sqlite"
    with sqlite3.connect(cache) as connection:
        old_keys = connection.execute("SELECT source_key FROM thumbnail_hashes").fetchall()

    image = Image.new("RGB", (80, 60), "white")
    image.paste("black", (0, 0, 40, 60))
    buffer = BytesIO()
    image.save(buffer, "JPEG")

    async def describe(prompt, _config, **_kwargs):
        assert prompt == PROMPT
        return json.dumps(
            {
                "uncovered_person": "no",
                "subject_action": "A changed sampled view.",
                "clothing_exposure": "Clothed.",
                "composition": "One photograph.",
                "visible_records": "None visible.",
            }
        )

    monkeypatch.setattr(editorial_gateway, "query_llm", describe)
    provider = PictureFactsProvider(
        llm_config=observed["config"],
        cache_path=observed["bank"],
        preview_bytes=lambda _asset: buffer.getvalue(),
        trace=Trace(),
    )
    changed = provider.observe("one")
    provider.close()
    assert changed["status"] == "available"
    assert changed["image_sha256"] != observed["records"]["one"]["image_sha256"]
    no_calls = replies(monkeypatch, [])
    new = adapter.preview_hashes(("one",), {"one": changed})
    assert new["one"] != old["one"]
    assert adapter.metrics()["preview_hashes_computed"] == 2
    with sqlite3.connect(cache) as connection:
        new_keys = connection.execute("SELECT source_key FROM thumbnail_hashes").fetchall()
    assert len(old_keys) == 1 and len(new_keys) == 2 and set(old_keys) < set(new_keys)
    adapter.close()
    replay = confirmer(observed)
    assert replay.preview_hashes(("one",), {"one": changed}) == new
    assert replay.metrics()["preview_hashes_computed"] == 0
    assert replay.metrics()["preview_hashes_cache_hits"] == 1
    assert no_calls == []
    replay.close()


@pytest.mark.parametrize("mutation", ["missing", "unbound", "swapped", "changed_bytes", "outside"])
def test_missing_or_unbound_material_is_omitted_without_hashing_or_inference(
    observed, monkeypatch, mutation
):
    records = deepcopy(observed["records"])
    options = {}
    if mutation == "missing":
        records.pop("one")
    elif mutation == "unbound":
        records["one"] = {"status": "available"}
    elif mutation == "swapped":
        records["one"] = records["two"]
    elif mutation == "changed_bytes":
        (observed["image_dir"] / f"{records['one']['image_sha256']}.jpg").write_bytes(b"changed")
    else:
        options["allowed_ids"] = {"two", "three"}
    calls = replies(monkeypatch, [])
    adapter = confirmer(observed, **options)
    assert adapter.preview_hashes(("one",), records) == {}
    metrics = adapter.metrics()
    assert metrics["preview_hashes_unavailable"] == 1
    assert metrics["preview_hashes_computed"] == metrics["actual_http_attempts"] == 0
    assert calls == []
    adapter.close()
