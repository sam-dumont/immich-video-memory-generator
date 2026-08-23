"""A look that cannot change a rank only changes the caption.

Measured on a real hobby-project day, ten assets, six never looked at. The
still that shipped was described as "a clear plastic container holding dried,
flaky green leaves, possibly herbs or tea" — the model did not recognise the
material — at interest 0.4. The same day's evening still was described as "a
white plastic fermentation bucket, equipped with an airlock and spigot" at
interest 0.6: understood, and a better representative of the day.

Both were looked at. The better one scored higher. The worse one shipped,
because selection had ranked on metadata and nothing reconsidered.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.analysis.smart_pipeline import (
    ClipWithSegment,
    PipelineConfig,
    SmartPipeline,
)
from immich_memories.api.models import AssetType
from immich_memories.config import Config
from immich_memories.config_models_analysis import AnalysisConfig
from tests.conftest import make_clip


def _pipeline(tmp_path: Path) -> SmartPipeline:
    analysis_cache = MagicMock()
    analysis_cache.get_analysis.return_value = None
    return SmartPipeline(
        client=MagicMock(),
        analysis_cache=analysis_cache,
        thumbnail_cache=MagicMock(),
        config=PipelineConfig(),
        analysis_config=AnalysisConfig(),
        app_config=Config(
            cache={"directory": str(tmp_path / "cache")},
            llm={"model": "qwen-3.6"},
            content_analysis={"enabled": True},
        ),
    )


def _still(asset_id: str, hour: int, score: float) -> ClipWithSegment:
    clip = make_clip(
        asset_id, duration=4.0, file_created_at=datetime(2019, 3, 16, hour, tzinfo=UTC)
    )
    clip.asset.type = AssetType.IMAGE
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=score, analyzed=False)


def test_the_look_moves_the_score_it_produced(tmp_path: Path) -> None:
    """The caption and the rank come from the same look; only one was kept."""
    shipped = _still("misread", 12, 0.310)

    from immich_memories.analysis import photo_look

    # WHY: the VLM is the network boundary; this stands in for its look.
    with patch(
        "immich_memories.photos.photo_pipeline.look_at_selected_photos",
        return_value={"misread": (0.22, {"description": "a container of dried leaves"})},
    ):
        photo_look.look_at_stills([shipped], config=_pipeline(tmp_path)._app_config, client=None)

    assert shipped.score == 0.22
    assert shipped.clip.llm_description == "a container of dried leaves"


def test_the_day_repicks_the_frame_the_model_understood(tmp_path: Path) -> None:
    """The acceptance case: the fermentation bucket, not the hops."""
    misread = _still("hops-misread", 12, 0.310)
    understood = _still("fermentation-bucket", 18, 0.334)
    elsewhere = _still("another-day", 9, 0.6)
    elsewhere.clip.asset.file_created_at = datetime(2019, 6, 1, 9, tzinfo=UTC)

    from immich_memories.analysis import photo_look

    pipeline = _pipeline(tmp_path)
    looks = {
        "hops-misread": (0.22, {"description": "a container of dried leaves"}),
        "fermentation-bucket": (0.41, {"description": "a fermentation bucket with an airlock"}),
    }
    # WHY: the VLM is the network boundary; this stands in for its looks.
    with patch("immich_memories.photos.photo_pipeline.look_at_selected_photos", return_value=looks):
        swapped = photo_look.repick_days(
            selected=[misread, elsewhere],
            pool=[misread, understood, elsewhere],
            config=pipeline._app_config,
            client=None,
        )

    assert {m.clip.asset.id for m in swapped} == {"fermentation-bucket", "another-day"}


def test_a_day_whose_selected_frame_is_already_best_is_left_alone(tmp_path: Path) -> None:
    """Re-picking must not churn a day that was already right."""
    best = _still("already-best", 12, 0.5)
    worse = _still("worse", 18, 0.2)

    from immich_memories.analysis import photo_look

    pipeline = _pipeline(tmp_path)
    looks = {
        "already-best": (0.5, {"description": "a family at the table"}),
        "worse": (0.2, {"description": "an empty worktop"}),
    }
    # WHY: the VLM is the network boundary; this stands in for its looks.
    with patch("immich_memories.photos.photo_pipeline.look_at_selected_photos", return_value=looks):
        swapped = photo_look.repick_days(
            selected=[best], pool=[best, worse], config=pipeline._app_config, client=None
        )

    assert [m.clip.asset.id for m in swapped] == ["already-best"]
