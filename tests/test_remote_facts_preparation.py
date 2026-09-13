"""Offloaded preparation keeps the bank contract and never loads local models."""

import base64
import json
import sqlite3

import httpx

from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS
from immich_memories.config_models_editorial import EditorialConfig
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
from immich_memories.config_models_inference import InferenceConfig
from tests.test_editorial_preparation import preview, refusing_ports, run, successful_ports

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
    assert len(rows) == 16
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
