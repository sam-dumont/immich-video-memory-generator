"""`tier:` — the one setting that picks nas, gpu or full."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
import yaml

from immich_memories.config_loader import Config

GEMMA = {"base_url": "http://gpu-box:8080/v1", "model": "gemma-4-e4b"}


def _knobs(config: Config) -> tuple[str, str, bool]:
    editorial = config.editorial
    return editorial.reader, editorial.preparation.tier, editorial.laya_audience


def test_an_unstated_tier_is_the_nas_tier() -> None:
    config = Config()

    assert config.tier == "nas"
    assert _knobs(config) == ("rules", "no_captions", False)


def test_the_gpu_tier_turns_on_every_light_model_and_no_llm() -> None:
    assert _knobs(Config(tier="gpu")) == ("rules", "full", True)


def test_the_full_tier_adds_the_reader_to_the_gpu_tier() -> None:
    assert _knobs(Config(tier="full", llm=GEMMA)) == ("model", "full", True)


@pytest.mark.parametrize(
    "llm", [{}, {"model": "gemma-4-e4b"}, {"base_url": "", "model": "gemma-4-e4b"}]
)
def test_the_full_tier_refuses_to_load_without_an_llm_endpoint(llm: dict) -> None:
    with pytest.raises(ValueError, match="tier: full needs an LLM"):
        Config(tier="full", llm=llm)


def test_an_explicit_advanced_knob_wins_over_the_tier() -> None:
    config = Config(tier="gpu", editorial={"laya_audience": False})

    assert _knobs(config) == ("rules", "full", False)


@pytest.mark.parametrize("tier", ["nas", "gpu"])
@pytest.mark.parametrize("reader", ["model", "auto"])
def test_a_saved_reader_cannot_enable_an_llm_on_a_tier_without_one(tier, reader):
    config = Config(tier=tier, editorial={"reader": reader}, llm=GEMMA)

    assert config.editorial.resolve_reader(config.llm.model) == "rules"


def test_a_model_on_nas_explains_that_text_features_remain_available(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        config = Config(llm=GEMMA)

    assert config.editorial.reader == "rules"
    assert "titles and music mood" in caplog.text
    assert "selection stays on nas" in caplog.text


def test_saving_keeps_what_the_tier_decided_out_of_the_file(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    Config(tier="gpu").save_yaml(path)
    saved = yaml.safe_load(path.read_text())

    assert saved["tier"] == "gpu"
    editorial = saved["advanced"]["editorial"]
    assert "reader" not in editorial
    assert "laya_audience" not in editorial
    assert "tier" not in editorial["preparation"]
    path.write_text(path.read_text().replace("tier: gpu", "tier: nas"))
    assert _knobs(Config.from_yaml(path)) == ("rules", "no_captions", False)


def test_saving_preserves_a_preparation_choice_changed_after_loading(tmp_path):
    config = Config(tier="gpu")
    config.editorial.preparation.tier = "metadata_only"
    config.editorial.laya_audience = False
    path = tmp_path / "config.yaml"

    config.save_yaml(path)

    reloaded = Config.from_yaml(path)
    assert reloaded.editorial.preparation.tier == "metadata_only"
    assert reloaded.editorial.laya_audience is False


def test_the_tier_is_a_top_level_key_in_the_file(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"tier": "full", "advanced": {"llm": GEMMA}}))

    assert _knobs(Config.from_yaml(path)) == ("model", "full", True)


def test_each_tier_reports_what_it_runs_with() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from tier_settings import tier_settings

    assert [tier_settings(t) for t in ("nas", "gpu", "full")] == [
        {
            "tier": "nas",
            "reader": "rules",
            "preparation_tier": "no_captions",
            "laya_audience": False,
            "llm_endpoint_required": False,
        },
        {
            "tier": "gpu",
            "reader": "rules",
            "preparation_tier": "full",
            "laya_audience": True,
            "llm_endpoint_required": False,
        },
        {
            "tier": "full",
            "reader": "model",
            "preparation_tier": "full",
            "laya_audience": True,
            "llm_endpoint_required": True,
        },
    ]


def test_config_show_names_the_tier_and_what_it_set(tmp_path) -> None:
    from unittest.mock import patch

    from click.testing import CliRunner

    from immich_memories.cli import main

    with (
        # WHY: the CLI would otherwise create ~/.immich-memories and read the real file
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=Config(tier="gpu")),
    ):
        result = CliRunner().invoke(main, ["config", "--show"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "gpu (reader rules, preparation full, Laya on)" in result.output
