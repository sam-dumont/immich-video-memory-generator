"""Saving config must not bake environment-provided secrets into config.yaml.

Docker and Kubernetes users deliberately keep the Immich API key out of the
config file and supply it through the environment, either as `${VAR}` inside the
YAML or as `IMMICH_API_KEY`. Pressing Save in the UI dumped the *resolved*
values, writing the secret to disk permanently and silently undoing that choice
-- and the file then outlives the container that had the env var.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.config_loader import Config

FROM_ENV = "sk-do-not-write-me-to-disk"


def test_a_templated_key_is_saved_as_its_template(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MY_IMMICH_KEY", FROM_ENV)
    source = tmp_path / "config.yaml"
    source.write_text("immich:\n  url: http://immich.invalid\n  api_key: ${MY_IMMICH_KEY}\n")
    config = Config.from_yaml(source)
    assert config.immich.api_key == FROM_ENV, "expansion should still work in memory"

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    text = out.read_text()
    assert FROM_ENV not in text, "the resolved secret was written to disk"
    assert "${MY_IMMICH_KEY}" in text


def test_a_key_supplied_by_an_env_var_is_not_written(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IMMICH_API_KEY", FROM_ENV)
    config = Config()
    config.immich.api_key = FROM_ENV

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    text = out.read_text()
    assert FROM_ENV not in text
    assert "${IMMICH_API_KEY}" in text


def test_a_key_typed_by_hand_is_preserved(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("IMMICH_API_KEY", raising=False)
    config = Config()
    config.immich.api_key = "typed-by-a-human"

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    assert "typed-by-a-human" in out.read_text()
    assert Config.from_yaml(out).immich.api_key == "typed-by-a-human"


def test_a_templated_secret_round_trips(tmp_path: Path, monkeypatch):
    """Saving then loading must not lose the value."""
    monkeypatch.setenv("MY_IMMICH_KEY", FROM_ENV)
    source = tmp_path / "config.yaml"
    source.write_text("immich:\n  api_key: ${MY_IMMICH_KEY}\n")

    out = tmp_path / "saved.yaml"
    Config.from_yaml(source).save_yaml(out)

    assert Config.from_yaml(out).immich.api_key == FROM_ENV


def test_other_credential_fields_are_covered_too(tmp_path: Path, monkeypatch):
    """auth.password and the tier-2 api_keys are secrets by the same rule."""
    monkeypatch.setenv("IMMICH_MEMORIES_AUTH_PASSWORD", FROM_ENV)
    monkeypatch.setenv("OPENAI_API_KEY", FROM_ENV)
    config = Config()
    config.auth.password = FROM_ENV
    config.llm.api_key = FROM_ENV

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    assert FROM_ENV not in out.read_text()
