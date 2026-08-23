"""Photos answer to the same rules about where footage came from.

The source filter lived on the video path only, so a collage somebody
forwarded through a messaging app walked into a year recap while a doorbell
clip beside it was turned away.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from immich_memories.analysis.source_filter import from_an_excluded_source
from immich_memories.api.models import AssetType
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


def test_a_score_cached_before_photos_could_describe_themselves_is_re_asked(tmp_path, monkeypatch):
    """Old rows hold a score and nothing else, and would hold it forever.

    The cache is keyed by the model that produced the row. What the row can
    answer is now a property of the prompt as well, so the key carries both —
    which invalidates the scores-only rows exactly once, and never again.
    """
    from immich_memories.photos import photo_pipeline
    from immich_memories.photos.scoring import PhotoLook

    asset = _photo("shot", "IMG_1375.HEIC")
    asset.exif_info = SimpleNamespace(make="Apple", model="iPhone 15 Pro")

    stale = {"shot": {"combined_score": 0.9, "llm_description": None, "llm_emotion": None}}
    cache = SimpleNamespace(
        get_asset_scores_batch=lambda _ids, model_version=None: (
            stale if model_version == "qwen-3.6" else {}
        ),
        save_asset_score=lambda **_kw: None,
    )
    monkeypatch.setattr(photo_pipeline, "_get_score_cache", lambda _db: cache)
    monkeypatch.setattr(
        photo_pipeline,
        "_llm_score_photo",
        lambda *_a, **_k: PhotoLook(score=0.42, payload={"description": "a whiteboard"}),
    )

    enhanced, payloads = photo_pipeline._enhance_with_llm(
        [(asset, 0.3)],
        config=SimpleNamespace(score_penalty=0.0),
        work_dir=tmp_path,
        download_fn=None,
        db_path=tmp_path / "scores.db",
        app_config=SimpleNamespace(
            content_analysis=SimpleNamespace(enabled=True),
            llm=SimpleNamespace(model="qwen-3.6"),
        ),
    )

    assert enhanced == [(asset, 0.42)], "the stale row must not stand in for a look"
    assert payloads["shot"]["description"] == "a whiteboard"


def test_a_pool_with_no_usable_photos_keeps_every_video(tmp_path) -> None:
    """An empty selection is not a verdict that nothing was worth keeping.

    The consumer filters videos to selection.kept_video_ids, so returning an
    empty selection when there is simply nothing to choose between discards
    every already-selected video and renders a memory with no content.
    """
    from immich_memories.analysis.unified_budget import BudgetCandidate

    videos = [
        BudgetCandidate(
            asset_id=f"video-{i}",
            duration=4.0,
            score=0.5,
            candidate_type="video",
            date=None,
            is_favorite=False,
        )
        for i in range(3)
    ]

    result = score_and_select_photos(
        photo_assets=[_photo("forwarded", "IMG-20190105-WA0006.jpg")],
        video_candidates=videos,
        config=Config(cache={"directory": str(tmp_path / "cache")}),
        target_duration=60.0,
        work_dir=tmp_path,
        download_fn=None,
    )

    assert result.selection.kept_video_ids == {"video-0", "video-1", "video-2"}


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


def test_the_pool_the_cli_and_ui_build_drops_what_the_camera_did_not_shoot(tmp_path) -> None:
    """The filter lived on the legacy path only.

    _merge_photos_into_pool is what both surfaces actually run, and it calls
    score_photos directly — so a forwarded still was fetched, VLM-scored and
    shipped on the two paths anybody uses.
    """
    from datetime import UTC, datetime

    from immich_memories.cli._pipeline_runner import _merge_photos_into_pool

    when = datetime(2019, 6, 12, 12, tzinfo=UTC)
    forwarded = make_asset(
        "forwarded", original_file_name="IMG-20190105-WA0006.jpg", file_created_at=when
    )
    forwarded.type = AssetType.IMAGE
    shot = make_asset("shot", original_file_name="IMG_1375.HEIC", file_created_at=when)
    shot.type = AssetType.IMAGE

    pool = _merge_photos_into_pool(
        [],
        photo_assets=[forwarded, shot],
        include_photos=True,
        config=Config(cache={"directory": str(tmp_path / "cache")}),
        client=None,
        work_dir=tmp_path,
        dry_run=True,
    )

    assert [c.clip.asset.id for c in pool] == ["shot"]
