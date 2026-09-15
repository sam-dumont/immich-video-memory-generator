"""Saving config must not bake environment-provided secrets into config.yaml.

Docker and Kubernetes users deliberately keep the Immich API key out of the
config file and supply it through the environment, either as `${VAR}` inside the
YAML or as `IMMICH_API_KEY`. Pressing Save in the UI dumped the *resolved*
values, writing the secret to disk permanently and silently undoing that choice
-- and the file then outlives the container that had the env var.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_loader import _CREDENTIAL_ENV_ALIASES, Config, _apply_env_overrides

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


def test_an_ace_step_key_from_the_env_survives_save_and_reload(tmp_path: Path, monkeypatch):
    """The ACE-Step alias was write-only: persisted as a template nothing expanded.

    Saving wrote `${ACE_STEP_API_KEY}` because the alias is a feeder for the
    field, but `ACEStepConfig` expanded `${VAR}` on `api_url` alone. The file
    then held a placeholder that read back as the literal string, so a working
    config lost its ACE-Step key the first time anyone pressed Save.
    """
    monkeypatch.setenv("ACE_STEP_API_KEY", FROM_ENV)
    config = Config()
    config.ace_step.api_key = FROM_ENV

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)
    assert "${ACE_STEP_API_KEY}" in out.read_text(), "the save path stopped writing the template"

    assert Config.from_yaml(out).ace_step.api_key == FROM_ENV


def test_the_ace_step_key_is_read_from_its_own_env_var(monkeypatch):
    """An alias the save path writes is one the load path has to answer to.

    `MUSICGEN_API_KEY` reaches its field with nothing in config.yaml; the
    ACE-Step block already read `ACE_STEP_ENABLED`, `_MODE` and `_API_URL` and
    skipped only the key, so the one variable that is a secret was the one
    variable the loader ignored.
    """
    monkeypatch.setenv("ACE_STEP_API_KEY", FROM_ENV)
    config = Config()

    _apply_env_overrides(config)

    assert config.ace_step.api_key == FROM_ENV


def test_the_anthropic_key_reaches_the_reader_from_its_own_env_var(monkeypatch):
    """The name on the tin: a Claude key is not called OPENAI_API_KEY."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FROM_ENV)
    config = Config()
    config.llm.provider = "anthropic"

    _apply_env_overrides(config)

    assert config.llm.api_key == FROM_ENV


def test_the_configured_provider_decides_which_key_wins(monkeypatch):
    """Two keys in one environment is normal; reading the wrong one is a 401."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "the-claude-key")
    monkeypatch.setenv("OPENAI_API_KEY", "the-openai-key")

    anthropic_side = Config()
    anthropic_side.llm.provider = "anthropic"
    openai_side = Config()
    openai_side.llm.provider = "openai"

    _apply_env_overrides(anthropic_side)
    _apply_env_overrides(openai_side)

    assert anthropic_side.llm.api_key == "the-claude-key"
    assert openai_side.llm.api_key == "the-openai-key"


@pytest.mark.parametrize(
    ("path", "alias"),
    [(path, alias) for path, aliases in _CREDENTIAL_ENV_ALIASES.items() for alias in aliases],
)
def test_every_credential_alias_reads_back_from_the_file_it_was_persisted_into(
    path: str, alias: str, tmp_path: Path, monkeypatch
):
    """Being a feeder for the save path obliges a variable to survive the load path.

    Membership in `_CREDENTIAL_ENV_ALIASES` is what makes `save_yaml` write
    `${ALIAS}` in place of the secret, so every member owes the reverse: the file
    it just produced has to resolve on its own. Asserted through `from_yaml`
    alone, because `_apply_env_overrides` would answer the same variable a second
    time and hide a field whose `${VAR}` expansion was never wired up -- which is
    exactly how the ACE-Step key stayed broken.
    """
    section, field = path.split(".")
    monkeypatch.setenv(alias, FROM_ENV)
    config = Config()
    setattr(getattr(config, section), field, FROM_ENV)

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)
    assert FROM_ENV not in out.read_text(), f"{alias} is not feeding the save path any more"

    assert getattr(getattr(Config.from_yaml(out), section), field) == FROM_ENV


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


def test_a_nested_key_supplied_by_an_env_var_is_not_written(tmp_path: Path, monkeypatch):
    """Preparation's caption key sits two levels down; Save must still leave it in the env."""
    monkeypatch.setenv("IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY", FROM_ENV)
    config = Config()
    config.editorial.preparation.caption_api_key = FROM_ENV

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    text = out.read_text()
    assert FROM_ENV not in text
    assert "${IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY}" in text


def test_a_templated_nested_key_is_saved_as_its_template(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MY_CAPTION_KEY", FROM_ENV)
    source = tmp_path / "config.yaml"
    source.write_text(
        "advanced:\n  editorial:\n    preparation:\n      caption_api_key: ${MY_CAPTION_KEY}\n"
    )
    config = Config.from_yaml(source)
    assert config.editorial.preparation.caption_api_key == FROM_ENV

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    text = out.read_text()
    assert FROM_ENV not in text
    assert "${MY_CAPTION_KEY}" in text


def test_an_unset_template_survives_a_save(tmp_path: Path, monkeypatch):
    """The caption key resolves to empty when its variable is missing; Save must not
    take that as permission to delete the reference the user wrote."""
    monkeypatch.delenv("MY_CAPTION_KEY", raising=False)
    source = tmp_path / "config.yaml"
    source.write_text(
        "advanced:\n  editorial:\n    preparation:\n      caption_api_key: ${MY_CAPTION_KEY}\n"
    )
    config = Config.from_yaml(source)
    assert config.editorial.preparation.caption_api_key == ""

    out = tmp_path / "saved.yaml"
    config.save_yaml(out)

    assert "${MY_CAPTION_KEY}" in out.read_text()


@pytest.mark.parametrize(
    ("provider", "other_alias", "header_name"),
    [
        ("anthropic", "OPENAI_API_KEY", "x-api-key"),
        ("zai", "OPENAI_API_KEY", "x-api-key"),
        ("openai", "ANTHROPIC_API_KEY", "Authorization"),
        ("openai-compatible", "ANTHROPIC_API_KEY", "Authorization"),
    ],
)
def test_an_unrelated_providers_key_never_reaches_the_request_header(
    monkeypatch, provider: str, other_alias: str, header_name: str
):
    """A key belonging to one host must not be posted to another host.

    Asserted on the header rather than on the field, because the header is what
    actually leaves the machine: an explicit key the operator configured was
    being replaced by whichever alias happened to be exported.
    """
    from immich_memories.analysis.llm_wire import anthropic_headers, openai_headers

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv(other_alias, "someone-elses-key")
    config = Config()
    config.llm.provider = provider
    config.llm.api_key = "the-key-the-operator-configured"

    _apply_env_overrides(config)

    headers = (
        anthropic_headers(config.llm) if header_name == "x-api-key" else openai_headers(config.llm)
    )
    assert "someone-elses-key" not in headers.get(header_name, "")
    assert "the-key-the-operator-configured" in headers[header_name]


@pytest.mark.parametrize(
    ("provider", "unrelated_alias"),
    [
        ("anthropic", "OPENAI_API_KEY"),
        ("zai", "OPENAI_API_KEY"),
        ("openai", "ANTHROPIC_API_KEY"),
        ("openai-compatible", "ANTHROPIC_API_KEY"),
    ],
)
@pytest.mark.parametrize("configured_key", ["", "explicit-provider-key"])
def test_the_full_load_does_not_borrow_another_providers_key(
    tmp_path: Path, monkeypatch, provider: str, unrelated_alias: str, configured_key: str
):
    """The whole load path, where `IMMICH_MEMORIES_LLM__API_KEY` is the explicit route."""
    from immich_memories import config_loader

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv(unrelated_alias, "someone-elses-key")
    monkeypatch.setenv("IMMICH_MEMORIES_LLM__PROVIDER", provider)
    monkeypatch.setenv("IMMICH_MEMORIES_LLM__API_KEY", configured_key)
    monkeypatch.setattr(config_loader, "_config", None)

    config = config_loader.get_config(reload=True)

    assert config.llm.api_key == configured_key
