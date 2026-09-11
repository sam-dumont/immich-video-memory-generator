"""Photos answer to the same rules about where footage came from.

The source filter lived on the video path only, so a collage somebody
forwarded through a messaging app walked into a year recap while a doorbell
clip beside it was turned away.
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.source_filter import from_an_excluded_source
from immich_memories.api.models import AssetType
from immich_memories.config_models_analysis import AnalysisConfig
from tests.conftest import make_asset


def _photo(asset_id: str, name: str):
    from immich_memories.api.models import AssetType

    asset = make_asset(asset_id, original_file_name=name)
    asset.type = AssetType.IMAGE
    return asset


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


def test_pre_smartphone_media_is_considered_despite_modern_source_signals() -> None:
    """An old scan/export is evidence; the editor can decide whether it belongs."""
    from immich_memories.analysis.source_filter import not_shot_here

    historical = make_asset(
        "historical",
        original_file_name="IMG-20030812-WA0001.jpg",
        exif_make=None,
        file_created_at=datetime(2003, 8, 12, tzinfo=UTC),
    )
    historical.type = AssetType.IMAGE

    assert not not_shot_here(
        historical,
        patterns=AnalysisConfig().exclude_filename_patterns,
        stills_need_a_camera=True,
    )


def test_a_starred_photo_passes_whatever_its_filename_says() -> None:
    """Every other hard gate in the pipeline subordinates itself to a star.

    A photo somebody was sent and then went and starred is a photo they chose
    to keep. Dropping it before the favorites guarantee can see it contradicts
    the rule the rest of selection is built on.
    """
    from immich_memories.analysis.source_filter import not_shot_here

    forwarded = make_asset("forwarded", original_file_name="IMG-20190105-WA0006.jpg")
    forwarded.type = AssetType.IMAGE
    forwarded.is_favorite = True

    doorbell = make_asset("doorbell", original_file_name="RingVideo_1.mp4")
    doorbell.is_favorite = True

    patterns = AnalysisConfig().exclude_filename_patterns
    assert not not_shot_here(forwarded, patterns=patterns, stills_need_a_camera=True)
    assert not not_shot_here(doorbell, patterns=patterns, stills_need_a_camera=True)


def test_the_messaging_glob_does_not_match_a_place_called_wa() -> None:
    """WA is a state abbreviation as well as a messaging app's marker.

    '*-WA[0-9]*' matched 'Olympia-WA2019.jpg' — a photograph of Washington,
    dropped for its filename.
    """
    from immich_memories.analysis.source_filter import from_an_excluded_source

    patterns = AnalysisConfig().exclude_filename_patterns

    assert from_an_excluded_source("IMG-20190105-WA0006.jpg", patterns)
    assert from_an_excluded_source("VID-20190701-WA0000.mp4", patterns)
    assert not from_an_excluded_source("Olympia-WA2019.jpg", patterns)
    assert not from_an_excluded_source("Seattle-WA98101.jpg", patterns)
