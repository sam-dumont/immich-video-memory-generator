"""Tests for PhotoConfig model and Config/AssemblyClip integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_models_render import PhotoConfig


class TestPhotoConfig:
    """Tests for PhotoConfig Pydantic model."""

    def test_defaults(self):
        """Sensible defaults: enabled, auto mode, 50% cap, 4s duration."""
        cfg = PhotoConfig()
        assert cfg.enabled is True
        assert cfg.max_ratio == 0.50
        assert cfg.duration == 4.0
        assert cfg.score_penalty == 0.2

    def test_boundary_values_accepted(self):
        """Boundary values at min/max are accepted."""
        cfg = PhotoConfig(max_ratio=0.0, duration=1.0, score_penalty=0.0)
        assert cfg.max_ratio == 0.0

        cfg = PhotoConfig(max_ratio=1.0, duration=10.0, score_penalty=1.0)
        assert cfg.max_ratio == 1.0

    @pytest.mark.parametrize(
        "field,value,match",
        [
            pytest.param("max_ratio", -0.1, "greater than", id="ratio-negative"),
            pytest.param("max_ratio", 1.1, "less than", id="ratio-over-1"),
            pytest.param("duration", 0.5, "greater than", id="duration-too-short"),
            pytest.param("duration", 20.0, "less than", id="duration-too-long"),
            pytest.param("score_penalty", -0.1, "greater than", id="penalty-negative"),
            pytest.param("score_penalty", 1.5, "less than", id="penalty-over-1"),
        ],
    )
    def test_validation_rejects_out_of_range(self, field, value, match):
        """Out-of-range values are rejected."""
        with pytest.raises(ValueError, match=match):
            PhotoConfig(**{field: value})


class TestPhotoConfigInConfig:
    """Tests for PhotoConfig integration with top-level Config."""

    def test_default_config_has_photos(self):
        """Config() includes a photos field with PhotoConfig defaults."""
        from immich_memories.config_loader import Config

        config = Config()
        assert config.photos.enabled is True
        assert config.photos.max_ratio == 0.50

    def test_yaml_roundtrip_with_photos(self, tmp_path):
        """PhotoConfig survives a YAML save → load cycle."""
        from immich_memories.config_loader import Config

        config_path = tmp_path / "config.yaml"
        original = Config(photos=PhotoConfig(enabled=True, duration=5.0, score_penalty=0.3))
        original.save_yaml(config_path)
        loaded = Config.from_yaml(config_path)
        assert loaded.photos.enabled is True
        assert loaded.photos.duration == 5.0
        assert loaded.photos.score_penalty == 0.3

    def test_photos_in_yaml_tier1(self, tmp_path):
        """Photos config loads from tier 1 (top-level YAML)."""
        from immich_memories.config_loader import Config

        config_path = tmp_path / "config.yaml"
        config_path.write_text("photos:\n  enabled: true\n  max_ratio: 0.5\n")
        loaded = Config.from_yaml(config_path)
        assert loaded.photos.enabled is True
        assert loaded.photos.max_ratio == 0.5


class TestAssemblyClipIsPhoto:
    """Tests for is_photo field on AssemblyClip."""

    def test_default_is_not_photo(self):
        """AssemblyClip defaults to is_photo=False (backwards compatible)."""
        from immich_memories.processing.assembly_config import AssemblyClip

        clip = AssemblyClip(path=Path("/tmp/v.mp4"), duration=5.0)
        assert clip.is_photo is False

    def test_is_photo_can_be_set(self):
        """AssemblyClip can be created with is_photo=True."""
        from immich_memories.processing.assembly_config import AssemblyClip

        clip = AssemblyClip(path=Path("/tmp/p.mp4"), duration=4.0, is_photo=True)
        assert clip.is_photo is True
