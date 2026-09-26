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
                "editorial": {"preparation": {"batch_size": 16}},
                "advanced": {"editorial": {"preparation": {"batch_size": 32}}},
            },
        )
    )
    assert config.editorial.preparation.batch_size == 16


def test_a_blank_install_prepares_at_the_documented_nas_tier(tmp_path):
    """No tier stated is the nas tier: the rules reader, and no caption server to wait on."""
    config = Config.from_yaml(_write(tmp_path, {"immich": {"url": "http://nas:2283"}}))
    assert config.editorial.resolve_reader(config.llm.model) == "rules"
    assert config.editorial.preparation.tier == "no_captions"


def test_a_stated_tier_is_never_redirected(tmp_path):
    config = Config.from_yaml(_write(tmp_path, {"tier": "gpu"}))
    assert config.editorial.preparation.tier == "full"
