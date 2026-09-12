"""`preset: fast` — one switch for CPU-only / NAS boxes (#311)."""

from __future__ import annotations

import pytest

from immich_memories.config_loader import Config


class TestFastPreset:
    def test_fast_preset_fills_the_cpu_only_knobs(self) -> None:
        config = Config(preset="fast")

        assert config.output.resolution == "1080p"
        assert config.output.codec == "h264"
        assert config.output.quality == "fast"
        assert config.hardware.encoder_preset == "fast"
        assert config.title_screens.animated_background is False

    def test_explicit_values_win_over_the_preset(self) -> None:
        config = Config(
            preset="fast",
            output={"resolution": "720p"},
            title_screens={"animated_background": True},
        )

        assert config.output.resolution == "720p"
        assert config.title_screens.animated_background is True
        assert config.hardware.encoder_preset == "fast"  # untouched knobs still filled

    def test_no_preset_changes_nothing(self) -> None:
        config = Config()

        assert config.preset is None
        assert config.hardware.encoder_preset == "balanced"
        assert config.title_screens.animated_background is True

    def test_unknown_preset_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="preset"):
            Config(preset="turbo")

    def test_env_var_selects_the_preset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IMMICH_MEMORIES_PRESET", "fast")

        assert Config().hardware.encoder_preset == "fast"


class TestCliPresetFlag:
    def test_root_preset_flag_applies_to_the_loaded_config(self) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from immich_memories.cli import main

        config = Config()
        with (
            # WHY: the CLI would otherwise create ~/.immich-memories and read the real file
            patch("immich_memories.cli.init_config_dir"),
            patch("immich_memories.cli.get_config", return_value=config),
        ):
            result = CliRunner().invoke(
                main, ["--preset", "fast", "config", "--show"], catch_exceptions=False
            )

        assert result.exit_code == 0, result.output
        assert "fast" in result.output
        assert config.preset == "fast"
        assert config.hardware.encoder_preset == "fast"
        assert config.output.resolution == "1080p"


class TestUiDefaultsUnderPreset:
    def test_step3_resolution_defaults_to_the_preset_resolution(self) -> None:
        from immich_memories.ui.pages.step3_options import default_resolution_label

        assert default_resolution_label(Config(preset="fast")) == "1080p"
        assert default_resolution_label(Config()) == "Auto (match clips)"
        assert default_resolution_label(None) == "Auto (match clips)"
