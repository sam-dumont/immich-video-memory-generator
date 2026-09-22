"""A NAS install's config survives the advanced merge at every depth."""

import yaml

from immich_memories.config_loader import Config


def _write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_a_top_level_tier_keeps_the_advanced_block_beside_it(tmp_path):
    """The exact shape that broke: a hand-edited top-level `preparation.tier` next to an
    app-written `advanced.editorial.preparation` holding the detector interpreter."""
    config = Config.from_yaml(
        _write(
            tmp_path,
            {
                "editorial": {"reader": "rules", "preparation": {"tier": "no_captions"}},
                "advanced": {
                    "editorial": {
                        "annotation_database": "/banks/annotations.sqlite",
                        "preparation": {"detector_python": "/venv/bin/python"},
                    }
                },
            },
        )
    )
    assert config.editorial.preparation.tier == "no_captions"
    assert config.editorial.preparation.detector_python == "/venv/bin/python"
    assert config.editorial.annotation_database == "/banks/annotations.sqlite"


def test_the_flat_key_still_wins_where_both_state_it(tmp_path):
    config = Config.from_yaml(
        _write(
            tmp_path,
            {
                "editorial": {"preparation": {"tier": "metadata_only"}},
                "advanced": {"editorial": {"preparation": {"tier": "full"}}},
            },
        )
    )
    assert config.editorial.preparation.tier == "metadata_only"


def test_a_blank_install_prepares_at_the_documented_nas_tier(tmp_path):
    """No LLM and no caption seat resolves the reader to rules; `full` would then spend
    every batch on a caption server nobody configured."""
    config = Config.from_yaml(_write(tmp_path, {"immich": {"url": "http://nas:2283"}}))
    assert config.editorial.resolve_reader(config.llm.model) == "rules"
    assert config.editorial.preparation.tier == "no_captions"


def test_a_stated_caption_seat_keeps_the_full_tier(tmp_path):
    config = Config.from_yaml(
        _write(
            tmp_path,
            {
                "advanced": {
                    "editorial": {"preparation": {"caption_base_url": "http://box:8092/v1"}}
                }
            },
        )
    )
    assert config.editorial.preparation.tier == "full"


def test_a_stated_tier_is_never_redirected(tmp_path):
    config = Config.from_yaml(
        _write(tmp_path, {"advanced": {"editorial": {"preparation": {"tier": "full"}}}})
    )
    assert config.editorial.preparation.tier == "full"


def test_a_configured_model_keeps_the_full_tier(tmp_path):
    config = Config.from_yaml(_write(tmp_path, {"advanced": {"llm": {"model": "a-vision-model"}}}))
    assert config.editorial.resolve_reader(config.llm.model) == "model"
    assert config.editorial.preparation.tier == "full"


def test_a_stated_caption_artifact_keeps_the_full_tier(tmp_path):
    config = Config.from_yaml(
        _write(
            tmp_path,
            {"advanced": {"editorial": {"preparation": {"caption_artifact_id": "a-model@rev"}}}},
        )
    )
    assert config.editorial.preparation.tier == "full"
