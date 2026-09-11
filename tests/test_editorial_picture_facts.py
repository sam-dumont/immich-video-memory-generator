"""Selected-image evidence keeps exact provenance, bounded failures and honest costs.

The probe's comparison against scripts/fill_public_descriptions stays on the probe branch.
"""

import asyncio
import io
import json
import sqlite3
from copy import deepcopy

import httpx
import pytest
from PIL import Image

from immich_memories.analysis import editorial_picture_facts as module
from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_query import LLMIncompleteResponse, LLMTransportAttempt
from immich_memories.analysis.selection_trace import Trace
from immich_memories.config_models_llm import LLMConfig

FACTS = {
    "uncovered_person": "no",
    "subject_action": "A person holds a wrapped infant.",
    "clothing_exposure": "A shirt covers the adult torso; the infant has a blanket.",
    "composition": "One close photograph.",
    "visible_records": "No printed records are visible.",
}
RAW = json.dumps(FACTS)


def preview(color="blue"):
    image = Image.new("RGB", (920, 630), color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def config():
    return LLMConfig(
        model=module.EXPECTED_MODEL_ID,
        base_url="http://localhost:9999/v1",
        no_thinking_params={
            "chat_template_kwargs": {"enable_thinking": False},
            "repetition_penalty": 1.1,
            "repetition_context_size": 2048,
        },
    )


def provider(tmp_path, *, llm_config=None, read=None, trace=None):
    return module.PictureFactsProvider(
        llm_config=llm_config or config(),
        cache_path=tmp_path / "facts.sqlite",
        preview_bytes=read or (lambda _asset: preview()),
        trace=trace or Trace(),
    )


def fake_transport(monkeypatch, *, raw=RAW, failure=None, retry=False):
    calls = []

    async def answer(prompt, llm_config, **kwargs):
        calls.append((prompt, llm_config, kwargs))
        observer = kwargs["transport_observer"]
        if retry:
            observer(LLMTransportAttempt(1, "connection_error", None))
        incomplete = isinstance(failure, LLMIncompleteResponse)
        observer(
            LLMTransportAttempt(2 if retry else 1, "incomplete" if incomplete else "response", 200)
        )
        llm_metrics.record_reply(prompt_tokens=41, cached_prompt_tokens=3, completion_tokens=17)
        llm_metrics.record_wall(0.25)
        if failure is not None:
            raise failure
        return raw

    # WHY: the external provider transport is replaced; the real image cache,
    # exact request identity, parsing and request traces all remain exercised.
    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", answer)
    return calls


def test_cold_then_new_provider_warm_has_same_facts_and_zero_model_work(tmp_path, monkeypatch):
    calls = fake_transport(monkeypatch)
    reads = []

    def read(asset):
        reads.append(asset)
        return preview()

    cold = provider(tmp_path, read=read)
    with llm_metrics.collecting() as total:
        result = cold.observe("a")
        cold.observe("a")
    assert total.calls == 1 and total.prompt_tokens == 41
    assert total.cache_hits == 0  # Memoization is not a second persistent cache request.
    assert len(calls) == 1 and reads == ["a"]
    assert cold.metrics()["requested_members"] == 2
    assert cold.metrics()["unique_members"] == 1
    assert cold.metrics()["memo_hits"] == 1
    assert cold.metrics()["http_attempts"] == 1
    assert cold.metrics()["inference_calls"] == 1
    assert cold.metrics()["images_sent"] == 1
    assert cold.metrics()["prompt_tokens"] == 41
    assert cold.metrics()["completion_tokens"] == 17
    cold.close()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("warm facts must not reach the model")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", forbidden)
    warm = provider(tmp_path, read=read)
    try:
        with llm_metrics.collecting() as total:
            assert warm.observe("a") == result
            returned = warm.observe("a")
            returned["facts"]["subject_action"] = "caller mutation"
            assert warm.observe("a") == result
        assert total.calls == 0 and total.cache_hits == 1
        assert warm.metrics()["cache_hits"] == 1
        for field in (
            "http_attempts",
            "inference_calls",
            "images_sent",
            "prompt_tokens",
            "completion_tokens",
        ):
            assert warm.metrics()[field] == 0
        assert "cache_hit" not in result and "wall_seconds" not in result
        assert str(tmp_path) not in json.dumps(result)
    finally:
        warm.close()


def test_old_caption_sized_observation_cannot_clear_new_detail_request(tmp_path, monkeypatch):
    from immich_memories.analysis.editorial_description_contract import (
        TILE_VERSION as old_tile_version,
    )

    source = io.BytesIO()
    Image.new("RGB", (200, 140), "blue").save(source, "JPEG")
    calls = fake_transport(monkeypatch)
    with monkeypatch.context() as legacy:
        legacy.setattr(module, "TILE_VERSION", old_tile_version)
        reader = provider(tmp_path, read=lambda _asset: source.getvalue())
        try:
            old = reader.observe("same-asset")
        finally:
            reader.close()
    reader = provider(tmp_path, read=lambda _asset: source.getvalue())
    try:
        new = reader.observe("same-asset")
    finally:
        reader.close()
    # Small sources are not upscaled, so image bytes alone cannot identify the new policy.
    assert old["image_sha256"] == new["image_sha256"]
    assert old["identity"] != new["identity"]
    assert old["producer"]["tile_version"] != new["producer"]["tile_version"]
    assert len(calls) == 2
    replay = provider(tmp_path, read=lambda _asset: source.getvalue())
    try:
        assert replay.observe("same-asset") == new
        assert replay.metrics()["http_attempts"] == 0
        assert replay.metrics()["cache_hits"] == 1
    finally:
        replay.close()


@pytest.mark.parametrize("kind", ["incomplete", "blank"])
def test_typed_completion_failure_has_separate_exact_warm_reuse(tmp_path, monkeypatch, kind):
    calls = fake_transport(
        monkeypatch,
        raw="   " if kind == "blank" else RAW,
        failure=LLMIncompleteResponse('{"subject_action":') if kind == "incomplete" else None,
    )
    cold = provider(tmp_path)
    result = cold.observe("a")
    assert result["status"] == "completion_failure" and "description" not in result
    assert cold.observe("a") == result and len(calls) == 1
    cold.close()
    with sqlite3.connect(tmp_path / "facts.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM visual_judgments").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM visual_completion_failures").fetchone()[0] == 1
        )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("bounded output failure must replay without another call")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", forbidden)
    warm = provider(tmp_path)
    try:
        with llm_metrics.collecting() as total:
            assert warm.observe("a") == result
        assert total.calls == 0 and total.cache_hits == 1
        assert warm.metrics()["cache_hits"] == 1
        assert warm.metrics()["unavailable_members"] == 1
        assert all(
            warm.metrics()[key] == 0 for key in ("http_attempts", "inference_calls", "images_sent")
        )
    finally:
        warm.close()


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {**FACTS, "editorial_verdict": "keep"},
        {**FACTS, "composition": "  "},
        {**FACTS, "clothing_exposure": None},
        {**FACTS, "visible_records": False},
        {**FACTS, "subject_action": ["a person"]},
        {**FACTS, "uncovered_person": "maybe"},
        {**FACTS, "uncovered_person": True},
        {key: value for key, value in FACTS.items() if key != "uncovered_person"},
    ],
)
def test_bad_factual_shape_is_an_explicit_gap_reused_without_retry(tmp_path, monkeypatch, bad):
    calls = fake_transport(monkeypatch, raw=json.dumps(bad))
    cold = provider(tmp_path)
    result = cold.observe("a")
    assert result["status"] == "invalid" and "description" not in result
    assert result["facts"] == {}
    cold.close()
    warm = provider(tmp_path)
    try:
        assert warm.observe("a") == result
        assert len(calls) == 1
        assert warm.metrics()["cache_hits"] == 1 and warm.metrics()["http_attempts"] == 0
    finally:
        warm.close()


@pytest.mark.parametrize("source", [None, b"not an image", preview()[:-20]])
def test_missing_or_unreadable_image_is_unavailable_without_a_model_call(
    tmp_path, monkeypatch, source
):
    calls = fake_transport(monkeypatch)
    reader = provider(tmp_path, read=lambda _asset: source)
    try:
        result = reader.observe("a")
        assert result["status"] == "unavailable" and "description" not in result
        assert calls == []
        assert reader.metrics()["unavailable_members"] == 1
        assert reader.metrics()["images_sent"] == 0
    finally:
        reader.close()


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("code defect"),
        ValueError("invalid provider envelope"),
        KeyError("bad field"),
        httpx.ConnectError("disconnected"),
    ],
)
def test_unexpected_model_or_network_failures_propagate(tmp_path, monkeypatch, failure):
    fake_transport(monkeypatch, failure=failure)
    reader = provider(tmp_path)
    try:
        with pytest.raises(type(failure)):
            reader.observe("a")
        assert reader.metrics()["http_attempts"] == 1
        with sqlite3.connect(tmp_path / "facts.sqlite") as connection:
            assert (
                connection.execute("SELECT count(*) FROM visual_completion_failures").fetchone()[0]
                == 0
            )
    finally:
        reader.close()


@pytest.mark.parametrize(
    "changed",
    ["image", "prompt", "schema", "settings", "model", "budget", "artifact", "image_detail"],
)
def test_changed_evidence_or_request_contract_cannot_reuse_old_facts(
    tmp_path, monkeypatch, changed
):
    calls = fake_transport(monkeypatch)
    first = provider(tmp_path)
    before = first.observe("a")
    first.close()
    updated, read = config(), None
    if changed == "image":

        def read(_asset):
            return preview("red")

    elif changed == "prompt":
        monkeypatch.setattr(module, "PROMPT", module.PROMPT + " Describe visible texture too.")
    elif changed == "schema":
        schema = deepcopy(module.RESPONSE_SCHEMA)
        schema["properties"]["composition"]["maxLength"] = 300
        monkeypatch.setattr(module, "RESPONSE_SCHEMA", schema)
    elif changed == "settings":
        updated.no_thinking_params = {"repetition_penalty": 1.05}
    elif changed == "model":
        updated.model = "different-model"
    elif changed == "budget":
        monkeypatch.setattr(module, "MAX_OUTPUT_TOKENS", 280)
    elif changed == "image_detail":
        updated.send_image_detail = False
    else:
        monkeypatch.setattr(module, "EXPECTED_REVISION", "new-artifact-revision")
    second = provider(tmp_path, llm_config=updated, read=read)
    try:
        after = second.observe("a")
        assert len(calls) == 2 and before["identity"] != after["identity"]
    finally:
        second.close()


def test_timeout_and_credentials_do_not_change_picture_facts(tmp_path, monkeypatch):
    calls = fake_transport(monkeypatch)
    first = provider(tmp_path)
    result = first.observe("a")
    first.close()
    updated = config().model_copy(update={"timeout_seconds": 9, "api_key": "test-private-key"})
    second = provider(tmp_path, llm_config=updated)
    try:
        assert second.observe("a") == result and len(calls) == 1
        assert "test-private-key" not in json.dumps(result)
    finally:
        second.close()


def test_active_event_loop_preserves_outer_usage_context_without_double_counting(
    tmp_path, monkeypatch
):
    fake_transport(monkeypatch, retry=True)
    reader = provider(tmp_path)

    async def run():
        with llm_metrics.collecting() as total:
            assert reader.observe("a")["status"] == "available"
            return total.snapshot()

    try:
        total = asyncio.run(run())
        assert total.calls == 1 and total.prompt_tokens == 41 and total.completion_tokens == 17
        assert reader.metrics()["http_attempts"] == 2
        assert reader.metrics()["inference_calls"] == 1
        assert reader.metrics()["images_sent"] == 2
        assert reader.metrics()["prompt_tokens"] == 41
    finally:
        reader.close()


def test_preview_acquisition_error_is_not_treated_as_an_unreadable_image(tmp_path):
    def unavailable_store(_asset):
        raise OSError("preview acquisition failed")

    reader = provider(tmp_path, read=unavailable_store)
    try:
        with pytest.raises(OSError, match="preview acquisition failed"):
            reader.observe("a")
    finally:
        reader.close()
