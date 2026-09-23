"""Offloaded preparation keeps the bank contract and never loads local models."""

import base64
import io
import json
import logging
import re
import sqlite3
import threading
import time

import httpx
import pytest
from PIL import Image

from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS
from immich_memories.analysis.editorial_preparation_remote import prepare_remote_facts
from immich_memories.analysis.remote_facts import RemoteFactsClient, RemoteFactsError
from immich_memories.config_models_editorial import EditorialConfig
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
from immich_memories.config_models_inference import InferenceConfig
from immich_memories.operations.cancellation import PipelineCancelled
from tests.test_editorial_preparation import (
    preview,
    refusing_ports,
    run,
    successful_ports,
)

ENDPOINT = "http://inference.test:8092"


def answer(producers):
    versions = EditorialConfig().head_versions
    return {
        "producers": {
            producer: {
                "encoder_key": f"pinned-{producer}",
                "facts": [
                    {"head": head, "version": versions[head], "label": "no", "confidence": 0.2}
                    for head in (PUBLIC_HEAD_VERSIONS if producer == "heads" else [producer])
                ],
            }
            for producer in producers
        }
    }


def remote_run(tmp_path, *, inference=None, ports=None, **kwargs):
    return run(
        tmp_path,
        preparation_config=EditorialPreparationConfig(tier="no_captions"),
        inference_config=inference or InferenceConfig(facts_base_url=ENDPOINT),
        ports=ports or refusing_ports("heads", "detectors", "captions")[0],
        fetch_preview=lambda _: preview(),
        **kwargs,
    )


def transport(monkeypatch, handler):
    # WHY: the HTTP boundary is replaced; preparation, validation and SQLite are real.
    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )


def banked_rows(tmp_path):
    with sqlite3.connect(tmp_path / "annotations.sqlite") as connection:
        return connection.execute("SELECT head, version, encoder_key FROM head_facts").fetchall()


def test_cold_offload_then_warm_reuses_all_facts_without_network(monkeypatch, tmp_path):
    calls = []

    def handle(request):
        assert request.url == f"{ENDPOINT}/facts"
        payload = json.loads(request.content)
        assert set(payload) == {"image", "producers"}
        assert base64.b64decode(payload["image"]) == preview()
        calls.append(payload["producers"])
        return httpx.Response(200, json=answer(payload["producers"]))

    transport(monkeypatch, handle)
    assert remote_run(tmp_path).complete
    assert len(calls) == 2
    assert all(set(names) == {"heads", "nsfw_marqo", "doc_docling"} for names in calls)
    rows = banked_rows(tmp_path)
    assert len(rows) == 20
    for head, version, key in rows:
        assert version == EditorialConfig().head_versions[head]
        assert key == f"pinned-{'heads' if head in PUBLIC_HEAD_VERSIONS else head}"
    calls.clear()
    assert remote_run(tmp_path).complete
    assert calls == []


def test_only_the_configured_producers_are_offloaded_and_the_rest_stay_local(monkeypatch, tmp_path):
    asked = []

    def handle(request):
        payload = json.loads(request.content)
        asked.append(tuple(payload["producers"]))
        return httpx.Response(200, json=answer(payload["producers"]))

    transport(monkeypatch, handle)
    ports, local_calls = refusing_ports("captions")
    result = remote_run(
        tmp_path,
        inference=InferenceConfig(facts_base_url=ENDPOINT, producers=["heads"]),
        ports=ports,
    )
    assert result.complete
    assert asked == [("heads",), ("heads",)]
    assert [set(pending) for name, pending in local_calls if name == "detectors"] == [
        {"nsfw_marqo", "doc_docling"}
    ]
    assert not any(name == "heads" for name, _ in local_calls)


def test_an_unreachable_service_is_named_and_the_local_producers_take_over(monkeypatch, tmp_path):
    def handle(request):
        raise httpx.ConnectError("connection refused")

    transport(monkeypatch, handle)
    ports, local_calls = refusing_ports("captions")
    result = remote_run(tmp_path, ports=ports)
    assert result.complete is False
    assert ENDPOINT in result.failures["remote_facts"]
    assert "local" in result.failures["remote_facts"]
    assert {name for name, _ in local_calls} == {"heads", "detectors"}
    assert result.missing_by_producer == {}


def test_without_the_fallback_a_dead_service_leaves_the_facts_missing(monkeypatch, tmp_path):
    def handle(request):
        return httpx.Response(503, json={"detail": "heads unavailable"})

    transport(monkeypatch, handle)
    ports, local_calls = refusing_ports("heads", "detectors", "captions")
    result = remote_run(
        tmp_path,
        inference=InferenceConfig(facts_base_url=ENDPOINT, fallback_to_local=False),
        ports=ports,
    )
    assert result.complete is False
    assert "HTTP 503" in result.failures["remote_facts"]
    assert ENDPOINT in result.failures["remote_facts"]
    assert local_calls == []
    assert set(result.missing_by_producer) == {
        f"head:{head}@{version}" for head, version in EditorialConfig().head_versions.items()
    }


def test_a_malformed_answer_banks_nothing(monkeypatch, tmp_path):
    def handle(request):
        payload = json.loads(request.content)
        body = answer(payload["producers"])
        body["producers"]["heads"]["facts"][0]["version"] = "stale-v0"
        return httpx.Response(200, json=body)

    transport(monkeypatch, handle)
    result = remote_run(
        tmp_path, inference=InferenceConfig(facts_base_url=ENDPOINT, fallback_to_local=False)
    )
    assert result.complete is False
    assert banked_rows(tmp_path) == []
    assert "incompatible" in result.failures["remote_facts"]


def test_the_local_path_is_untouched_when_no_endpoint_is_configured(tmp_path):
    calls = []
    result = run(
        tmp_path,
        preparation_config=EditorialPreparationConfig(tier="no_captions"),
        inference_config=InferenceConfig(),
        ports=successful_ports(calls),
        fetch_preview=lambda _: preview(),
    )
    assert result.complete
    assert {name for name, _ in calls} == {"heads", "detectors"}


def fake_service(response: httpx.Response) -> RemoteFactsClient:
    # WHY: the HTTP boundary only. Nothing else about the client is replaced.
    return RemoteFactsClient(
        InferenceConfig(facts_base_url=ENDPOINT),
        httpx.Client(transport=httpx.MockTransport(lambda _request: response)),
    )


def test_a_503_carries_the_service_detail_into_the_failure():
    """The detail was dropped for "check service logs", and the service logged
    nothing: whichever model was missing, the operator could not find out."""
    detail = "doc_docling: DetectorModelUnavailable: model.onnx is not in /cache/huggingface"

    with (
        fake_service(httpx.Response(503, json={"detail": detail})) as client,
        pytest.raises(RemoteFactsError, match=re.escape(f"HTTP 503: {detail}")),
    ):
        client.facts(preview(), EditorialConfig().head_versions)


def test_an_answer_with_no_detail_still_points_at_the_service():
    with (
        fake_service(httpx.Response(500, text="upstream said no")) as client,
        pytest.raises(RemoteFactsError, match="HTTP 500: check service logs"),
    ):
        client.facts(preview(), EditorialConfig().head_versions)


def coloured_preview(index: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), (7 * index % 256, 83, 66)).save(buffer, "JPEG")
    return buffer.getvalue()


def fake_server(handler, **settings) -> RemoteFactsClient:
    # WHY: the HTTP boundary only. The pool, the ordering and SQLite are all real.
    return RemoteFactsClient(
        InferenceConfig(facts_base_url=ENDPOINT, **settings),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )


def bank_pass(tmp_path, client, ids, *, concurrency, previews, banked):
    prepare_remote_facts(
        pending={asset_id: dict(EditorialConfig().head_versions) for asset_id in ids},
        store_path=tmp_path / "annotations.sqlite",
        client=client,
        concurrency=concurrency,
        preview_for=lambda asset_id: previews[asset_id],
        check_cancelled=lambda: None,
        progress=lambda *_: None,
        on_asset=banked.append,
    )


def test_the_pass_keeps_the_configured_number_of_requests_in_flight(tmp_path):
    """One POST at a time is the whole defect: 0.69 s a picture on a GPU service."""
    ids = tuple(f"id{index:02d}" for index in range(12))
    previews = {asset_id: coloured_preview(index) for index, asset_id in enumerate(ids)}
    lock = threading.Lock()
    live = peak = 0

    def handle(request):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02)
        with lock:
            live -= 1
        return httpx.Response(200, json=answer(json.loads(request.content)["producers"]))

    banked = []
    with fake_server(handle) as client:
        bank_pass(tmp_path, client, ids, concurrency=4, previews=previews, banked=banked)

    assert 2 <= peak <= 4
    assert tuple(banked) == ids


def test_answers_that_come_back_out_of_order_are_banked_in_the_pending_order(tmp_path):
    """Byte-identical replay depends on the bank seeing one order, not the wire's."""
    ids = tuple(f"id{index:02d}" for index in range(8))
    previews = {asset_id: coloured_preview(index) for index, asset_id in enumerate(ids)}
    delay = {previews[asset_id]: 0.05 - 0.005 * index for index, asset_id in enumerate(ids)}

    def handle(request):
        payload = json.loads(request.content)
        time.sleep(delay[base64.b64decode(payload["image"])])
        return httpx.Response(200, json=answer(payload["producers"]))

    banked = []
    with fake_server(handle) as client:
        bank_pass(tmp_path, client, ids, concurrency=8, previews=previews, banked=banked)

    assert tuple(banked) == ids
    assert {row[0] for row in banked_rows(tmp_path)} == set(EditorialConfig().head_versions)


def test_the_pass_says_how_many_requests_it_is_about_to_keep_in_flight(tmp_path, caplog):
    """An operator reading a slow pass has to be able to see the number it ran at."""
    ids = ("id00", "id01")
    previews = {asset_id: coloured_preview(index) for index, asset_id in enumerate(ids)}

    def handle(request):
        return httpx.Response(200, json=answer(json.loads(request.content)["producers"]))

    with caplog.at_level(logging.INFO), fake_server(handle) as client:
        bank_pass(tmp_path, client, ids, concurrency=6, previews=previews, banked=[])

    assert "remote facts: 2 pictures, 6 requests in flight" in caplog.text


def test_preparation_reports_what_the_service_charged_itself_apart_from_the_wait(
    monkeypatch, tmp_path
):
    def handle(request):
        return httpx.Response(
            200,
            json=answer(json.loads(request.content)["producers"]),
            headers={"X-Facts-Seconds": "0.0300"},
        )

    transport(monkeypatch, handle)
    result = remote_run(tmp_path)

    assert result.complete
    assert result.service_rates()["remote_facts"] == 0.03


def test_a_service_too_old_to_say_leaves_the_column_out_rather_than_reporting_zero(
    monkeypatch, tmp_path
):
    def handle(request):
        return httpx.Response(200, json=answer(json.loads(request.content)["producers"]))

    transport(monkeypatch, handle)
    result = remote_run(tmp_path)

    assert result.complete
    assert result.service_rates() == {}


def test_the_configured_concurrency_is_the_one_preparation_runs_at(monkeypatch, tmp_path, caplog):
    def handle(request):
        return httpx.Response(200, json=answer(json.loads(request.content)["producers"]))

    transport(monkeypatch, handle)
    with caplog.at_level(logging.INFO):
        result = remote_run(
            tmp_path, inference=InferenceConfig(facts_base_url=ENDPOINT, facts_concurrency=3)
        )

    assert result.complete
    assert "2 pictures, 3 requests in flight" in caplog.text


def test_a_stop_mid_pass_keeps_what_was_banked_and_asks_for_nothing_more(tmp_path):
    ids = tuple(f"id{index:02d}" for index in range(12))
    previews = {asset_id: coloured_preview(index) for index, asset_id in enumerate(ids)}
    asked = []
    lock = threading.Lock()

    def handle(request):
        with lock:
            asked.append(request)
        return httpx.Response(200, json=answer(json.loads(request.content)["producers"]))

    banked = []

    def stop_after_two():
        if len(banked) >= 2:
            raise PipelineCancelled

    with fake_server(handle) as client, pytest.raises(PipelineCancelled):
        prepare_remote_facts(
            pending={asset_id: dict(EditorialConfig().head_versions) for asset_id in ids},
            store_path=tmp_path / "annotations.sqlite",
            client=client,
            concurrency=4,
            preview_for=lambda asset_id: previews[asset_id],
            check_cancelled=stop_after_two,
            progress=lambda *_: None,
            on_asset=banked.append,
        )

    assert tuple(banked) == ids[:2]
    # The window already in the air is paid for; the ones behind it never left.
    assert len(asked) <= 4
