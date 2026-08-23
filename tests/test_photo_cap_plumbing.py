"""The photo cap the user configured has to be the cap the pipeline enforces."""

from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.config_loader import Config


def _config_capping_photos_at(ratio: float) -> Config:
    config = Config()
    config.photos.max_ratio = ratio
    return config


def test_the_configured_photo_ratio_becomes_the_enforced_ratio() -> None:
    """photos.max_ratio is documented as the dial; it has to reach the enforcer.

    The cap is applied from PipelineConfig.photo_max_ratio, so a construction
    site that never sets it enforces the dataclass default however the YAML
    reads.
    """
    pipeline_config = PipelineConfig.from_app_config(_config_capping_photos_at(0.25))

    assert pipeline_config.photo_max_ratio == 0.25


def test_caller_supplied_fields_survive_the_app_config_defaults() -> None:
    pipeline_config = PipelineConfig.from_app_config(
        _config_capping_photos_at(0.25), hdr_only=True, target_clips=42
    )

    assert pipeline_config.photo_max_ratio == 0.25
    assert pipeline_config.hdr_only is True
    assert pipeline_config.target_clips == 42


def test_the_ui_hands_the_pipeline_the_configured_cap() -> None:
    """The wizard builds its own pipeline config; it has to read the dial too."""
    from immich_memories.ui.pages.clip_pipeline import _build_pipeline_config
    from immich_memories.ui.state import AppState

    state = AppState(config=_config_capping_photos_at(0.25), pipeline_config={})

    assert _build_pipeline_config(state, []).photo_max_ratio == 0.25


def test_the_cli_hands_the_pipeline_the_configured_cap(tmp_path) -> None:
    """The CLI builds its own pipeline config; it has to read the dial too."""
    from datetime import datetime
    from unittest.mock import MagicMock, patch

    import pytest

    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.timeperiod import DateRange

    config = _config_capping_photos_at(0.25)
    config.cache.database = str(tmp_path / "cap.db")
    config.cache.directory = str(tmp_path / "cache")
    clip = MagicMock()
    clip.asset.id = "asset-1"
    clip.width, clip.height = 1920, 1080

    with (
        # WHY: Immich is the external boundary — this stands in for the library read.
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: the pipeline is the boundary under inspection; stopping it in analysis
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as pipeline_type,
        pytest.raises(RuntimeError, match="stop here"),
    ):
        pipeline_type.return_value.run_analysis.side_effect = RuntimeError("stop here")
        run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=60,
            transition="cut",
            music=None,
            output_path=tmp_path / "memory.mp4",
            memory_type="trip",
            person_names=[],
            date_range=DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2)),
            upload_to_immich=False,
            album=None,
            source="auto",
        )

    assert pipeline_type.call_args.kwargs["config"].photo_max_ratio == 0.25
