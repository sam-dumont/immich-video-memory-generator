"""A remote facts endpoint is an explicit, portable configuration choice."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from immich_memories.config_loader import Config


def test_inference_defaults_to_the_existing_local_path() -> None:
    inference = Config().inference
    assert inference.facts_base_url == ""
    assert inference.enabled is False
    assert inference.producers == ["heads", "nsfw_marqo", "doc_docling"]
    assert inference.fallback_to_local is True


def test_inference_endpoint_survives_tiered_yaml_and_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL", "http://facts.example:8092/")
    config = Config()
    assert config.inference.enabled is True
    destination = tmp_path / "config.yaml"
    config.save_yaml(destination)
    saved = yaml.safe_load(destination.read_text())

    assert saved["advanced"]["inference"]["facts_base_url"] == "http://facts.example:8092"
    monkeypatch.delenv("IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL")
    assert Config.from_yaml(destination).inference.facts_base_url == "http://facts.example:8092"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://facts.example",
        "http://user:password@facts.example",
        "http://facts.example?key=secret",
        "http://facts.example/#fragment",
        "not-a-url",
    ],
)
def test_inference_endpoint_refuses_ambiguous_or_embedded_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        Config(inference={"facts_base_url": url})


def test_inference_refuses_a_producer_the_service_does_not_serve() -> None:
    with pytest.raises(ValidationError):
        Config(inference={"producers": ["heads", "captions"]})
