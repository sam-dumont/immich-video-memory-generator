"""Real pair gateway and conserved image banks; all provider requests are controlled."""

from __future__ import annotations

import json
import socket
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO

import pytest
from PIL import Image

from immich_memories.analysis import editorial_gateway
from immich_memories.analysis.editorial_picture_facts import PROMPT, PictureFactsProvider
from immich_memories.analysis.editorial_sampled_pair_confirmation import CachedSampledPairConfirmer
from immich_memories.analysis.llm_wire import LLMTransportAttempt
from immich_memories.analysis.selection_same_picture import _PAIR_PROMPT
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.api.models import AssetType
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network is permitted in sampled pair adapter tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def jpeg(colour):
    output = BytesIO()
    Image.new("RGB", (80, 60), colour).save(output, "JPEG")
    return output.getvalue()


def answer(same=True):
    return json.dumps({"schema_version": "pair-v3", "same": same, "reason": "Observed pair."})


@pytest.fixture
def observed(tmp_path, monkeypatch):
    frames = {"one": jpeg("navy"), "two": jpeg("blue"), "three": jpeg("red")}
    assets = {
        asset_id: make_asset(asset_id, file_created_at=datetime(2024, 1, 1, tzinfo=UTC)).model_copy(
            update={"type": AssetType.IMAGE, "is_favorite": asset_id == "one"}
        )
        for asset_id in frames
    }
    config = LLMConfig(model="controlled-vision")
    bank = tmp_path / "judgments.sqlite"
    trace = Trace()

    async def describe(prompt, _config, **kwargs):
        assert prompt == PROMPT
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "uncovered_person": "no",
                "subject_action": "A person stands indoors.",
                "clothing_exposure": "Clothed.",
                "composition": "One photograph.",
                "visible_records": "None visible.",
            }
        )

    monkeypatch.setattr(editorial_gateway, "query_llm", describe)
    provider = PictureFactsProvider(
        llm_config=config,
        cache_path=bank,
        preview_bytes=frames.get,
        trace=trace,
    )
    records = {asset_id: provider.observe(asset_id) for asset_id in assets}
    provider.close()
    assert all(record["status"] == "available" for record in records.values())
    return {
        "assets": assets,
        "records": records,
        "config": config,
        "bank": bank,
        "trace": trace,
        "image_dir": tmp_path / "picture-facts-images",
        "root": tmp_path,
    }


def confirmer(observed, **changes):
    arguments = {
        "assets": observed["assets"],
        "allowed_ids": set(observed["assets"]),
        "llm_config": observed["config"],
        "cache_path": observed["bank"],
        "image_dir": observed["image_dir"],
        "trace": observed["trace"],
        "sheet_dir": observed["root"] / "pair-sheets",
        "limits": VisionRequestLimits(max_output_tokens=500, timeout_seconds=18),
    }
    return CachedSampledPairConfirmer(**(arguments | changes))


def replies(monkeypatch, values):
    calls, remaining = [], iter(values)

    async def respond(prompt, _config, **kwargs):
        calls.append({"prompt": prompt, "images": kwargs["images"], "kwargs": kwargs})
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        value = next(remaining)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(editorial_gateway, "query_llm", respond)
    return calls


def test_real_provider_to_existing_pair_primitive_then_exact_warm(observed, monkeypatch):
    calls = replies(monkeypatch, [answer(), answer()])
    before = deepcopy(observed["records"])
    adapter = confirmer(observed)
    first, audit = adapter((("one", "two"),), observed["records"])
    assert first[0].same is True and len(calls) == 2
    assert all(call["prompt"] == _PAIR_PROMPT for call in calls)
    assert all(call["kwargs"]["max_tokens"] == 500 for call in calls)
    assert all(call["kwargs"]["image_detail"] == "high" for call in calls)
    assert audit["logical_requests"] == audit["actual_http_attempts"] == 2
    assert audit["cache_hits"] == audit["preview_downloads"] == audit["motion_decodes"] == 0
    assert calls[0]["images"] != calls[1]["images"]
    first_requests = observed["trace"].requests[-2:]
    assert [r.provenance.input_ids for r in first_requests] == [("one", "two"), ("two", "one")]
    assert [r.provenance.pass_name for r in first_requests] == ["pass-2-selects"] * 2
    adapter.close()

    replies(monkeypatch, [])  # Any actual re-ask would fail the pair and this assertion.
    adapter = confirmer(observed)
    warm, second = adapter((("one", "two"),), observed["records"])
    assert warm == first and second["cache_hits"] == 2 and second["actual_http_attempts"] == 0
    assert observed["records"] == before
    adapter.close()


def test_episode_similarity_is_separate_from_strict_matching_and_reused(observed, monkeypatch):
    calls = replies(monkeypatch, [answer(False), answer(), answer()])
    adapter = confirmer(observed)
    strict, _ = adapter((("one", "two"),), observed["records"])
    assert not strict[0].same
    episode, audit = adapter.confirm_episode_pairs((("one", "two"),), observed["records"])
    assert episode[0].same and audit["cache_hits"] == 0
    assert calls[0]["prompt"] == _PAIR_PROMPT
    assert calls[1]["prompt"] == calls[2]["prompt"] != _PAIR_PROMPT
    assert calls[1]["images"] != calls[2]["images"]
    warm, repeated = adapter.confirm_episode_pairs((("one", "two"),), observed["records"])
    assert warm == episode and repeated["cache_hits"] == 2
    assert repeated["actual_http_attempts"] == 0 and len(calls) == 3
    adapter.close()


@pytest.mark.parametrize(
    "values,expected_calls", [([answer(False)], 1), ([answer(), answer(False)], 2)]
)
def test_false_or_disagreement_retains_both_without_text_fallback(
    observed, monkeypatch, values, expected_calls
):
    calls = replies(monkeypatch, values)
    adapter = confirmer(observed)
    result, audit = adapter((("one", "two"),), observed["records"])
    assert result[0].same is False and len(calls) == expected_calls
    assert audit["actual_http_attempts"] == expected_calls
    adapter.close()


@pytest.mark.parametrize(
    "response",
    ["not json", answer().replace("pair-v3", "wrong-schema"), RuntimeError("unavailable")],
)
def test_unreadable_pair_never_authorizes_a_cut(observed, monkeypatch, response):
    calls = replies(monkeypatch, [response])
    adapter = confirmer(observed)
    result, audit = adapter((("one", "two"),), observed["records"])
    assert not result[0].same and result[0].warning and len(calls) == 1
    assert audit["rows"][0]["status"] == "unavailable"
    adapter.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_record",
        "unavailable",
        "swapped_record",
        "changed_hash",
        "bad_hash",
        "missing_file",
        "corrupted_file",
        "missing_bank",
        "wrong_producer",
    ],
)
def test_missing_or_unbound_cached_pixels_make_no_model_request(observed, monkeypatch, mutation):
    records = deepcopy(observed["records"])
    target = records["one"]
    path = observed["image_dir"] / f"{target['image_sha256']}.jpg"
    if mutation == "missing_record":
        records.pop("one")
    elif mutation == "unavailable":
        target["status"] = "unavailable"
    elif mutation == "swapped_record":
        records["one"] = records["two"]
    elif mutation == "changed_hash":
        target["image_sha256"] = records["two"]["image_sha256"]
    elif mutation == "bad_hash":
        target["image_sha256"] = "../../escaped"
    elif mutation == "missing_file":
        path.unlink()
    elif mutation == "corrupted_file":
        path.write_bytes(b"corrupt")
    elif mutation == "missing_bank":
        target["identity"] = "0" * 64
    elif mutation == "wrong_producer":
        target["producer"]["schema_version"] = "different-schema"
    calls = replies(monkeypatch, [])
    adapter = confirmer(observed)
    result, audit = adapter((("one", "two"),), records)
    assert not result[0].same and result[0].warning
    assert audit["routed_pairs"] == audit["actual_http_attempts"] == 0 and calls == []
    adapter.close()


def test_pair_outside_selectable_membership_is_not_opened(observed, monkeypatch):
    calls = replies(monkeypatch, [])
    adapter = confirmer(observed, allowed_ids={"two"})
    result, audit = adapter((("one", "two"),), observed["records"])
    assert not result[0].same and audit["routed_pairs"] == 0 and calls == []
    adapter.close()


@pytest.mark.parametrize(
    "pairs", [(("one", "one"),), (("one", "two"), ("two", "one")), (("one", ""),)]
)
def test_invalid_nominations_fail_before_requests(observed, monkeypatch, pairs):
    calls = replies(monkeypatch, [])
    adapter = confirmer(observed)
    with pytest.raises(ValueError):
        adapter(pairs, observed["records"])
    assert calls == []
    adapter.close()


def test_two_links_do_not_create_a_transitive_pair_or_choose_a_keeper(observed, monkeypatch):
    calls = replies(monkeypatch, [answer(), answer(), answer(), answer()])
    adapter = confirmer(observed)
    original = deepcopy(observed["assets"])
    results, audit = adapter((("one", "two"), ("two", "three")), observed["records"])
    assert [(x.earlier_asset_id, x.later_asset_id) for x in results] == [
        ("one", "two"),
        ("two", "three"),
    ]
    assert all(result.same for result in results) and len(calls) == 4
    assert all(
        set(asdict(result)) == {"earlier_asset_id", "later_asset_id", "same", "warning"}
        for result in results
    )
    assert "cut authority" in audit["scope"] and observed["assets"] == original
    adapter.close()


def test_video_source_uses_only_the_saved_single_preview(observed, monkeypatch):
    observed["assets"]["one"] = observed["assets"]["one"].model_copy(
        update={"type": AssetType.VIDEO}
    )
    calls = replies(monkeypatch, [answer(), answer()])
    adapter = confirmer(observed)
    result, audit = adapter((("one", "two"),), observed["records"])
    assert result[0].same and len(calls) == 2
    assert "no whole-video equality" in audit["scope"] and audit["motion_decodes"] == 0
    assert audit["rows"][0]["picture_sha256"] == [
        observed["records"][x]["image_sha256"] for x in ("one", "two")
    ]
    adapter.close()


def test_missing_pair_does_not_prevent_an_independent_valid_pair(observed, monkeypatch):
    calls = replies(monkeypatch, [answer(), answer()])
    adapter = confirmer(observed)
    records = dict(observed["records"])
    records.pop("one")
    results, audit = adapter((("one", "two"), ("two", "three")), records)
    assert not results[0].same and results[1].same and len(calls) == 2
    assert audit["routed_pairs"] == 1
    adapter.close()


def test_empty_pairs_do_no_work(observed, monkeypatch):
    calls = replies(monkeypatch, [])
    adapter = confirmer(observed)
    result, audit = adapter((), {})
    assert result == () and audit["logical_requests"] == 0 and calls == []
    adapter.close()


def test_metrics_count_only_this_adapters_requests_and_cannot_be_mutated(observed, monkeypatch):
    replies(monkeypatch, [answer(), answer()])
    adapter = confirmer(observed)
    assert adapter.metrics()["logical_requests"] == 0  # Three earlier own-picture requests exist.
    adapter((("one", "two"),), observed["records"])
    adapter((("one", "two"),), observed["records"])
    totals = adapter.metrics()
    assert totals["nominated_pairs"] == totals["routed_pairs"] == 2
    assert totals["logical_requests"] == 4
    assert totals["actual_http_attempts"] == totals["cache_hits"] == 2
    assert totals["wall_seconds"] >= 0
    totals["actual_http_attempts"] = 999
    assert adapter.metrics()["actual_http_attempts"] == 2
    adapter.close()
