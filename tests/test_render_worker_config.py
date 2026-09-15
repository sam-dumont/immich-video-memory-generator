"""Worker configuration follows the existing private config-file lifecycle."""

from uuid import uuid4

import pytest

from immich_memories.config_loader import Config
from immich_memories.security import configured_secret_values


def test_worker_token_from_environment_survives_save_without_becoming_plaintext(
    tmp_path, monkeypatch
):
    token = uuid4().hex
    monkeypatch.setenv("IMMICH_MEMORIES_RENDER__WORKER_TOKEN", token)
    source = tmp_path / "config.yaml"
    source.write_text("render:\n  worker_base_url: https://render.example.com/\n")
    config = Config.from_yaml(source)
    assert token in configured_secret_values(config)
    saved = tmp_path / "saved.yaml"
    config.save_yaml(saved)
    assert token not in saved.read_text()
    restored = Config.from_yaml(saved)
    assert restored.render.worker_token == token
    assert restored.render.worker_base_url == "https://render.example.com"


@pytest.mark.parametrize(
    "url", ["file:///tmp/worker", "https://user:password@worker", "https://worker?token=value"]
)
def test_worker_urls_cannot_hide_credentials_or_use_non_http_transports(tmp_path, url):
    source = tmp_path / "config.yaml"
    source.write_text(f"render:\n  worker_base_url: {url}\n")
    with pytest.raises(ValueError, match="HTTP"):
        Config.from_yaml(source)
