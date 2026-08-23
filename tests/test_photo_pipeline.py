"""Photos answer to the same rules about where footage came from.

The source filter lived on the video path only, so a collage somebody
forwarded through a messaging app walked into a year recap while a doorbell
clip beside it was turned away.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.analysis.source_filter import from_an_excluded_source
from immich_memories.config import Config
from immich_memories.config_models import AnalysisConfig
from immich_memories.photos.photo_pipeline import score_and_select_photos
from tests.conftest import make_asset


def _photo(asset_id: str, name: str):
    from immich_memories.api.models import AssetType

    asset = make_asset(asset_id, original_file_name=name)
    asset.type = AssetType.IMAGE
    return asset


def test_a_forwarded_photo_never_reaches_scoring(tmp_path: Path) -> None:
    """Turning it away before scoring is also what keeps it out of the VLM."""
    result = score_and_select_photos(
        photo_assets=[_photo("forwarded", "IMG-20190105-WA0006.jpg")],
        video_candidates=[],
        config=Config(cache={"directory": str(tmp_path / "cache")}),
        target_duration=60.0,
        work_dir=tmp_path,
        download_fn=None,
    )

    assert result.scored_photos == []


def test_a_photo_from_the_camera_roll_is_kept() -> None:
    """The filter has to be a scalpel, not a broom."""
    patterns = AnalysisConfig().exclude_filename_patterns

    assert not from_an_excluded_source("IMG_0809.HEIC", patterns)
    assert not from_an_excluded_source("DSC_4471.JPG", patterns)


def test_the_default_patterns_catch_what_the_camera_roll_did_not_shoot() -> None:
    patterns = AnalysisConfig().exclude_filename_patterns

    assert from_an_excluded_source("RingVideo_6763648097558121116.mp4", patterns)
    assert from_an_excluded_source("IMG-20190105-WA0006.jpg", patterns)
    assert from_an_excluded_source("rpreplay_final1560343200.mp4", patterns)
