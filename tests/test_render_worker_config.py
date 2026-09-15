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


def test_cleartext_http_to_a_non_loopback_worker_is_refused_until_explicit(tmp_path):
    """The request carries the Immich API key; cleartext transport must be opt-in."""
    source = tmp_path / "config.yaml"
    source.write_text("render:\n  worker_base_url: http://render.lan:8093\n")
    with pytest.raises(ValueError, match="allow_insecure_http"):
        Config.from_yaml(source)

    opted_in = tmp_path / "opted-in.yaml"
    opted_in.write_text(
        "render:\n  worker_base_url: http://render.lan:8093\n  allow_insecure_http: true\n"
    )
    config = Config.from_yaml(opted_in)
    assert config.render.allow_insecure_http is True


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_http_workers_stay_ergonomic(tmp_path, host):
    source = tmp_path / "config.yaml"
    source.write_text(f"render:\n  worker_base_url: http://{host}:8093\n")
    assert Config.from_yaml(source).render.enabled is True


def test_https_workers_need_no_opt_in(tmp_path):
    source = tmp_path / "config.yaml"
    source.write_text("render:\n  worker_base_url: https://render.example.com\n")
    config = Config.from_yaml(source)
    assert config.render.enabled is True
    assert config.render.allow_insecure_http is False
