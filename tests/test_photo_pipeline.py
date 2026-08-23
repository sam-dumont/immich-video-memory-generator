"""Photos answer to the same rules about where footage came from.

The source filter lived on the video path only, so a collage somebody
forwarded through a messaging app walked into a year recap while a doorbell
clip beside it was turned away.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_a_still_with_no_camera_in_its_exif_was_not_shot_here() -> None:
    """Measured across four months of a real library, 5260 assets.

    Stills with no EXIF make: 1498 .jpg named for a messaging app, 34 .png
    downloads, and 9 camera originals that had lost their make. Videos are a
    different story and are left alone — 25 of 224 make-less videos there are
    genuine phone clips, and the filename rule already catches the rest.
    """
    from immich_memories.analysis.source_filter import not_shot_here
    from immich_memories.api.models import AssetType

    received = make_asset("received", original_file_name="IMG_2841.jpg", exif_make=None)
    received.type = AssetType.IMAGE
    shot = make_asset("shot", original_file_name="IMG_1375.HEIC", exif_make="Apple")
    shot.type = AssetType.IMAGE
    clip = make_asset("clip", original_file_name="IMG_1365.MOV", exif_make=None)

    assert not_shot_here(received, patterns=(), stills_need_a_camera=True)
    assert not not_shot_here(shot, patterns=(), stills_need_a_camera=True)
    assert not not_shot_here(clip, patterns=(), stills_need_a_camera=True), (
        "a phone clip loses its make often enough that this rule cannot judge video"
    )


def test_the_camera_rule_can_be_turned_off() -> None:
    """A library of exported or edited originals would lose them to this."""
    from immich_memories.analysis.source_filter import not_shot_here
    from immich_memories.api.models import AssetType

    received = make_asset("received", original_file_name="IMG_2841.jpg", exif_make=None)
    received.type = AssetType.IMAGE

    assert not not_shot_here(received, patterns=(), stills_need_a_camera=False)


def test_the_description_survives_scoring_and_the_score_stays_a_number(tmp_path, monkeypatch):
    """What the model said has to reach the clip, and the score must stay a score.

    The review reads a clip's description. A photo never had one, so it was
    handed a bare line and protected by the rule that says never to drop a
    clip for missing information.
    """
    from immich_memories.photos import photo_pipeline
    from immich_memories.photos.scoring import PhotoLook

    asset = _photo("shot", "IMG_1375.HEIC")
    asset.exif_info = SimpleNamespace(make="Apple", model="iPhone 15 Pro")

    # WHY: the VLM is the network boundary; this stands in for its answer.
    monkeypatch.setattr(
        photo_pipeline,
        "_llm_score_photo",
        lambda *_a, **_k: PhotoLook(
            score=0.42,
            payload={"description": "a whiteboard covered in sticky notes", "category": "object"},
        ),
    )

    enhanced, payloads = photo_pipeline._enhance_with_llm(
        [(asset, 0.3)],
        config=SimpleNamespace(score_penalty=0.0),
        work_dir=tmp_path,
        download_fn=None,
        app_config=SimpleNamespace(
            content_analysis=SimpleNamespace(enabled=True),
            llm=SimpleNamespace(model="qwen-3.6"),
        ),
    )

    assert enhanced == [(asset, 0.42)], "the score must still be a number"
    assert payloads["shot"]["description"] == "a whiteboard covered in sticky notes"
