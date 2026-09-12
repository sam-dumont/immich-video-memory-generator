"""The store-backed editorial path is explicit and producer-versioned."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from immich_memories.config import EditorialConfig as PublicEditorialConfig
from immich_memories.config_loader import Config
from immich_memories.config_models_editorial import EditorialConfig


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
        "doc_docling": "det-v1",
        "location": "public-v1",
        "nsfw_marqo": "det-v2",
        "people": "public-v1",
        "swim": "oi-v3",
        "venue": "oi-v3",
    }


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
