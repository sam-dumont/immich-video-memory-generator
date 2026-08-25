"""Photo scoring plumbing that outlived the two-budget chain.

The budget arithmetic this file was named for is gone: photographs and video
compete in one pool, so nothing computes a photo budget against a video one.
What is left are the pieces that still have a caller.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from immich_memories.analysis.asset_merit import ranking_key
from immich_memories.api.models import Asset
from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.photo_pipeline import score_photos


class TestDetectPhotoResolution:
    """Photo resolution must match the dominant clip orientation."""

    def test_portrait_clips_swap_resolution(self):
        from immich_memories.generate import GenerationParams, _detect_photo_resolution

        # WHY: mock VideoClipInfo — we're testing orientation detection, not Immich
        portrait_clip = MagicMock(width=1080, height=1920)
        landscape_clip = MagicMock(width=1920, height=1080)

        config = MagicMock()
        config.output.resolution_tuple = (1920, 1080)

        params = GenerationParams(
            clips=[portrait_clip, portrait_clip, landscape_clip],
            output_path=MagicMock(),
            config=config,
        )
        w, h = _detect_photo_resolution(params)
        # 2/3 portrait → swap
        assert w == 1080
        assert h == 1920

    def test_landscape_clips_keep_resolution(self):
        from immich_memories.generate import GenerationParams, _detect_photo_resolution

        landscape_clip = MagicMock(width=1920, height=1080)

        config = MagicMock()
        config.output.resolution_tuple = (1920, 1080)

        params = GenerationParams(
            clips=[landscape_clip, landscape_clip],
            output_path=MagicMock(),
            config=config,
        )
        w, h = _detect_photo_resolution(params)
        assert w == 1920
        assert h == 1080


class TestGenerationParamsTargetDuration:
    """GenerationParams has target_duration_seconds field."""

    def test_generate_params_has_target_duration(self):
        from immich_memories.generate import GenerationParams

        params = GenerationParams(
            clips=[],
            output_path=MagicMock(),
            config=MagicMock(),
            target_duration_seconds=60.0,
        )
        assert params.target_duration_seconds == 60.0

    def test_generate_params_target_duration_defaults_none(self):
        from immich_memories.generate import GenerationParams

        params = GenerationParams(
            clips=[],
            output_path=MagicMock(),
            config=MagicMock(),
        )
        assert params.target_duration_seconds is None


class TestScorePhotos:
    """Tests for the extracted score_photos() function."""

    def test_score_photos_returns_scored_list(self, tmp_path):
        now = datetime.now(tz=UTC)
        assets = [
            Asset(
                id="photo1",
                type="IMAGE",
                fileCreatedAt=now,
                fileModifiedAt=now,
                updatedAt=now,
                isFavorite=True,
            ),
            Asset(
                id="photo2",
                type="IMAGE",
                fileCreatedAt=now,
                fileModifiedAt=now,
                updatedAt=now,
                isFavorite=False,
            ),
        ]
        config = PhotoConfig()
        # WHY: mock download_fn — we're testing scoring, not I/O
        download_fn = MagicMock()
        result = score_photos(
            assets=assets,
            config=config,
            work_dir=tmp_path,
            download_fn=download_fn,
        )
        assert len(result) == 2
        # Each entry is (Asset, float)
        assert all(isinstance(score, float) for _, score in result)
        # A favourite orders above a non-favourite rather than scoring above
        # it: the star is a sort key, not a term (asset_merit.ranking_key).
        by_id = {a.id: (a, s) for a, s in result}
        assert ranking_key(*by_id["photo1"]) > ranking_key(*by_id["photo2"])

    def test_vlm_shortlist_does_not_delete_metadata_scored_photo_fallbacks(self, tmp_path):
        """Only semantic scoring is capped; every eligible photo remains selectable."""
        now = datetime.now(tz=UTC)
        assets = [
            Asset(
                id=f"photo-{index}",
                type="IMAGE",
                fileCreatedAt=now + timedelta(hours=index),
                fileModifiedAt=now + timedelta(hours=index),
                updatedAt=now + timedelta(hours=index),
            )
            for index in range(20)
        ]

        with patch(
            "immich_memories.photos.photo_pipeline._enhance_with_llm",
            side_effect=lambda shortlist, *_args, **_kwargs: (shortlist, {}),
        ) as enhance:
            result = score_photos(
                assets=assets,
                config=PhotoConfig(max_ratio=0.5),
                work_dir=tmp_path,
                download_fn=MagicMock(),
            )

        # One look per moment, and these 20 photos are an hour apart, so all
        # 20 are moments. The number is incidental; what this test guards is
        # the line below — capping the LOOK must never shrink what selection
        # can choose from.
        assert len(enhance.call_args.args[0]) == 20
        assert {asset.id for asset, _score in result} == {asset.id for asset in assets}
