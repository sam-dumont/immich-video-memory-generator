"""The store-backed editorial path is explicit and producer-versioned."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from immich_memories.config import EditorialConfig as PublicEditorialConfig
from immich_memories.config_loader import Config, _apply_env_overrides
from immich_memories.config_models_editorial import EditorialConfig
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig


def test_editorial_store_defaults_without_a_selection_switch(tmp_path) -> None:
    config = EditorialConfig()

    assert "enabled" not in EditorialConfig.model_fields
    assert "story_first" not in EditorialConfig.model_fields
    assert config.annotation_database_path is None
    assert config.resolve_annotation_database(tmp_path) == tmp_path / "annotations.sqlite"
    assert config.description_model == "smolvlm2-500m-base-public@envelope-v3-compact"
    assert config.pixel_producer_key == "pixel-facts-v1"  # gitleaks:allow
    assert config.head_versions == {
        "activity": "public-v1",
        "children": "public-v1",
        "doc_docling": "det-v2",
        "location": "public-v1",
        "nsfw_marqo": "det-v2",
        "people": "public-v1",
        "venue": "oi-v3",
    }


def test_a_config_that_still_names_a_retired_head_drops_it_and_keeps_the_rest(caplog) -> None:
    """An old config file is not a broken one: the name goes, one line says so, the run stands."""
    with caplog.at_level("INFO"):
        config = EditorialConfig(head_versions={"swim": "oi-v3", "activity": "public-v1"})

    assert config.head_versions == {"activity": "public-v1"}
    assert "swim" in caplog.text


def test_editorial_config_is_available_from_the_public_config_module() -> None:
    assert PublicEditorialConfig is EditorialConfig


def test_saved_marqo_version_moves_to_the_current_onnx_producer() -> None:
    saved = {"nsfw_marqo": "det-v1", "activity": "public-v1"}
    config = EditorialConfig(head_versions=saved)
    assert config.head_versions == {"nsfw_marqo": "det-v2", "activity": "public-v1"}
    assert saved["nsfw_marqo"] == "det-v1"


def test_obsolete_route_flags_cannot_choose_another_selector() -> None:
    config = EditorialConfig(enabled=False, story_first=False)
    assert config.model_dump() == EditorialConfig().model_dump()
    assert Config().editorial.annotation_database_path is None


def test_editorial_database_expands_environment_variables(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDITORIAL_STORE_ROOT", str(tmp_path))

    config = EditorialConfig(
        enabled=True,
        annotation_database="${EDITORIAL_STORE_ROOT}/annotations.sqlite",
    )

    assert config.annotation_database_path == tmp_path / "annotations.sqlite"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"description_model": " "},
        {"pixel_producer_key": " "},
        {"head_versions": {}},
        {"head_versions": {"activity": " "}},
        {"head_versions": {" ": "public-v1"}},
    ],
)
def test_editorial_producer_contract_rejects_blank_or_missing_versions(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        EditorialConfig(**kwargs)  # type: ignore[arg-type]


def test_config_loads_and_saves_editorial_as_an_advanced_section(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    source.write_text(
        "advanced:\n"
        "  editorial:\n"
        "    enabled: true\n"
        "    annotation_database: /library/annotations.sqlite\n"
    )

    config = Config.from_yaml(source)
    assert "enabled" not in config.editorial.model_dump()
    assert config.editorial.annotation_database_path == Path("/library/annotations.sqlite")

    saved = tmp_path / "saved.yaml"
    config.save_yaml(saved)
    payload = yaml.safe_load(saved.read_text())
    assert "editorial" not in payload
    assert "enabled" not in payload["advanced"]["editorial"]
    assert payload["advanced"]["editorial"]["annotation_database"] == (
        "/library/annotations.sqlite"
    )


def test_saved_legacy_detector_versions_upgrade_without_changing_custom_heads(tmp_path):
    source = tmp_path / "old-config.yaml"
    source.write_text(
        "advanced:\n"
        "  editorial:\n"
        "    head_versions:\n"
        "      doc_docling: det-v1\n"
        "      nsfw_marqo: det-v1\n"
        "      activity: custom-v3\n"
    )

    config = Config.from_yaml(source)
    saved = tmp_path / "saved-config.yaml"
    config.save_yaml(saved)

    assert yaml.safe_load(saved.read_text())["advanced"]["editorial"]["head_versions"] == {
        "doc_docling": "det-v2",
        "nsfw_marqo": "det-v2",
        "activity": "custom-v3",
    }


def test_caption_key_comes_from_its_own_nested_environment_variable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY", "the-captioners-key"
    )
    source = tmp_path / "config.yaml"
    source.write_text("advanced:\n  editorial:\n    preparation:\n      tier: full\n")

    config = Config.from_yaml(source)

    assert config.editorial.preparation.caption_api_key == "the-captioners-key"


def test_the_readers_key_never_becomes_the_captioners_key(tmp_path: Path, monkeypatch) -> None:
    """Same box, different endpoint: a token for one is not consent for the other."""
    monkeypatch.setenv("OPENAI_API_KEY", "the-readers-key")
    source = tmp_path / "config.yaml"
    source.write_text("advanced:\n  editorial:\n    preparation:\n      tier: full\n")

    config = Config.from_yaml(source)
    _apply_env_overrides(config)

    assert config.llm.api_key == "the-readers-key"
    assert config.editorial.preparation.caption_api_key == ""


def test_caption_key_expands_a_template_from_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_CAPTION_KEY", "expanded-caption-key")
    source = tmp_path / "config.yaml"
    source.write_text(
        "advanced:\n  editorial:\n    preparation:\n      caption_api_key: ${MY_CAPTION_KEY}\n"
    )

    config = Config.from_yaml(source)

    assert config.editorial.preparation.caption_api_key == "expanded-caption-key"


def test_an_unset_template_leaves_no_key_rather_than_the_literal(
    tmp_path: Path, monkeypatch
) -> None:
    """`Authorization: Bearer ${MY_CAPTION_KEY}` is a 401 that reads as a wrong key."""
    monkeypatch.delenv("MY_CAPTION_KEY", raising=False)
    source = tmp_path / "config.yaml"
    source.write_text(
        "advanced:\n  editorial:\n    preparation:\n      caption_api_key: ${MY_CAPTION_KEY}\n"
    )

    config = Config.from_yaml(source)

    assert config.editorial.preparation.caption_api_key == ""


def test_captions_are_asked_for_one_at_a_time_by_default() -> None:
    """Measured in #932 on 136 pictures, same llama.cpp server on CPU: 36 s at one
    request in flight, 461 s at four. Four image encodes share the threads of one
    and none of them finishes sooner, and `--parallel 4` server-side does not
    recover it. A GPU captioner is the setup that wants more, and the caption
    server page says to raise it there.
    """
    from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig

    assert EditorialPreparationConfig().caption_concurrency == 1


def test_the_picture_facts_reader_is_on_by_default():
    assert EditorialPreparationConfig().picture_facts.enabled is True


def test_an_enabled_picture_facts_reader_needs_an_endpoint_without_credentials():
    from immich_memories.config_models_editorial_preparation import PictureFactsConfig

    enabled = EditorialPreparationConfig(picture_facts=PictureFactsConfig(enabled=True))

    assert enabled.demands_picture_facts is True
    assert enabled.picture_facts.base_url == "http://127.0.0.1:8080/v1"
    with pytest.raises(ValidationError):
        PictureFactsConfig(base_url="http://user:secret@127.0.0.1:8080/v1")
