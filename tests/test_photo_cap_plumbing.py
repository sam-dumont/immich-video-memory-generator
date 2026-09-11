"""The configured photo ratio is forwarded into the shared pipeline configuration.

These construction tests do not establish enforcement by the story-first selector.
"""

from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.config_loader import Config
from tests.conftest import make_clip


def _config_capping_photos_at(ratio: float) -> Config:
    config = Config()
    config.photos.max_ratio = ratio
    return config


def test_the_configured_photo_ratio_reaches_pipeline_config() -> None:
    """Forward the user's value instead of silently keeping the dataclass default."""
    pipeline_config = PipelineConfig.from_app_config(_config_capping_photos_at(0.25))

    assert pipeline_config.photo_max_ratio == 0.25


def test_caller_supplied_fields_survive_the_app_config_defaults() -> None:
    pipeline_config = PipelineConfig.from_app_config(
        _config_capping_photos_at(0.25), hdr_only=True, target_clips=42
    )

    assert pipeline_config.photo_max_ratio == 0.25
    assert pipeline_config.hdr_only is True
    assert pipeline_config.target_clips == 42


def test_the_ui_forwards_the_configured_photo_ratio() -> None:
    """The wizard builds its own pipeline config; it has to read the dial too."""
    from immich_memories.ui.pages.clip_pipeline import _build_pipeline_config
    from immich_memories.ui.state import AppState

    state = AppState(config=_config_capping_photos_at(0.25), pipeline_config={})

    assert _build_pipeline_config(state, []).photo_max_ratio == 0.25


def test_the_cli_forwards_the_configured_photo_ratio(tmp_path) -> None:
    """The CLI builds its own pipeline config; it has to read the dial too."""
    from datetime import datetime
    from unittest.mock import MagicMock, patch

    import pytest

    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.timeperiod import DateRange

    config = _config_capping_photos_at(0.25)
    config.cache.database = str(tmp_path / "cap.db")
    config.cache.directory = str(tmp_path / "cache")
    clip = make_clip("asset-1", file_created_at=datetime(2026, 1, 1))

    # WHY: runtime construction opens stores and model clients; capture its config, then stop.
    with (
        patch("immich_memories.analysis.editorial_runtime.build_smart_pipeline") as build_pipeline,
        pytest.raises(RuntimeError, match="stop here"),
    ):
        build_pipeline.return_value.run_editorial_source.side_effect = RuntimeError("stop here")
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

    build_pipeline.assert_called_once()
    assert build_pipeline.call_args.kwargs["config"].photo_max_ratio == 0.25
    build_pipeline.return_value.run_editorial_source.assert_called_once()
