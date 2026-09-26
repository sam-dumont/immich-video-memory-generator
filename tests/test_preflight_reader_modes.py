"""Preflight must check only the producers the selected route demands."""

import pytest

from immich_memories.config_loader import Config
from immich_memories.preflight import (
    CheckStatus,
    check_caption_endpoint,
    check_llm,
)
from immich_memories.preflight_run import check_detector_export, check_encoder


@pytest.mark.parametrize("reader", ["auto", "rules"])
def test_metadata_rules_preflight_without_a_model_never_contacts_endpoints(monkeypatch, reader):
    def unexpected_network(*args, **kwargs):
        pytest.fail("metadata rules preflight contacted a model endpoint")

    monkeypatch.setattr("httpx.Client", unexpected_network)
    monkeypatch.setattr("httpx.get", unexpected_network)
    config = Config(
        editorial={"reader": reader, "preparation": {"tier": "metadata_only"}},
    )
    checks = (check_llm, check_encoder, check_detector_export, check_caption_endpoint)
    assert [check(config).status for check in checks] == [CheckStatus.SKIPPED] * 4


def test_nas_preflight_checks_a_configured_llm_for_text_features(monkeypatch):
    import httpx

    calls = []

    def answer(_client, url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    # WHY: test the provider HTTP boundary without running or billing a text model.
    monkeypatch.setattr(httpx.Client, "post", answer)
    config = Config(tier="nas", llm={"base_url": "http://text.test/v1", "model": "reader"})

    assert check_llm(config).status is CheckStatus.OK
    assert calls == ["http://text.test/v1/chat/completions"]


def test_no_captions_still_requires_model_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "httpx.get", lambda *_args, **_kwargs: pytest.fail("caption endpoint contacted")
    )
    config = Config(editorial={"preparation": {"tier": "no_captions"}})
    config.triage.encoder = str(tmp_path / "missing-encoder.onnx")
    config.editorial.preparation.marqo_onnx = str(tmp_path / "missing-detector.onnx")
    assert check_caption_endpoint(config).status is CheckStatus.SKIPPED
    assert check_encoder(config).status is CheckStatus.ERROR
    assert check_detector_export(config).status is CheckStatus.ERROR


def test_explicit_model_reader_without_a_model_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(
        "httpx.Client", lambda *_args, **_kwargs: pytest.fail("model endpoint contacted")
    )
    config = Config(tier="full", llm={"base_url": "http://llm.test/v1", "model": "reader"})
    config.llm.model = ""
    assert check_llm(config).status is CheckStatus.ERROR
