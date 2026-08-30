"""One pool, one ranking: the invariants that must survive deleting the split.

The two-budget chain reconciled a photo budget against a video budget after the
fact. Both live entry points already bypass it. These pin the behaviour the
surviving path owes, so deleting the chain cannot quietly take it along.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from immich_memories.api.models import AssetType
from immich_memories.config import Config
from tests.conftest import make_asset


class TestNoPhotoCostsAVideo:
    """A photo pool that yields nothing must never shrink the video pool."""

    def test_a_pool_of_only_rejected_photos_drops_no_video(self, tmp_path):
        """Every photo filtered out is not a reason to ship fewer videos.

        The rule used to be pinned on score_and_select_photos, which reconciled
        a photo budget against a video one and could return a shrunken set of
        kept videos. The surviving path never computes a video budget at all,
        so the guarantee has to be stated where it now lives.
        """
        from immich_memories.analysis.smart_pipeline import ClipWithSegment
        from immich_memories.api.models import VideoClipInfo
        from immich_memories.cli._candidate_pool import _merge_photos_into_pool

        videos = []
        for index in range(3):
            asset = make_asset(f"video-{index}")
            videos.append(
                ClipWithSegment(
                    clip=VideoClipInfo(asset=asset, duration_seconds=6.0),
                    start_time=0.0,
                    end_time=6.0,
                    score=0.5,
                )
            )

        # A messaging forward: from_the_camera_roll rejects it, so nothing is
        # left to merge.
        forwarded = make_asset("forwarded", original_file_name="IMG-20190105-WA0006.jpg")
        forwarded.type = AssetType.IMAGE

        result = _merge_photos_into_pool(
            videos,
            photo_assets=[forwarded],
            include_photos=True,
            config=Config(cache={"directory": str(tmp_path / "cache")}),
            client=MagicMock(),
            work_dir=tmp_path,
            dry_run=True,
        )

        assert [c.clip.asset.id for c in result] == ["video-0", "video-1", "video-2"]


class TestTheLockedFolderNeverShips:
    """Visibility is the one gate neither a star nor a config key may open."""

    def _assets(self):
        timeline = make_asset("on-timeline", is_favorite=True)
        timeline.type = AssetType.IMAGE
        locked = make_asset("locked", is_favorite=True).model_copy(update={"visibility": "locked"})
        locked.type = AssetType.IMAGE
        archived = make_asset("archived").model_copy(update={"visibility": "archive"})
        archived.type = AssetType.IMAGE
        return timeline, locked, archived

    def test_generation_drops_them_even_when_the_config_asks_to_keep_them(self):
        """The config key exists, and generation refuses to read it.

        Asked for by the owner in exactly those terms: the setting defaults to
        off, and generation overrides it, so a flag turned on once for an
        experiment cannot quietly put the locked folder into next month's video.
        A setting that has to be remembered is not a safety gate.
        """
        from immich_memories.analysis.source_filter import from_the_camera_roll

        config = MagicMock()
        config.analysis.exclude_filename_patterns = []
        config.analysis.exclude_stills_without_camera_exif = False
        config.analysis.include_off_timeline_assets = True

        kept = from_the_camera_roll(list(self._assets()), config)

        assert [asset.id for asset in kept] == ["on-timeline"]

    def test_the_config_default_is_off(self):
        """A fresh install must not need the owner to know this setting exists."""
        from immich_memories.config_models_analysis import AnalysisConfig

        assert AnalysisConfig().include_off_timeline_assets is False

    def test_it_says_out_loud_that_it_ignored_the_setting(self, caplog):
        """A silent refusal would leave the owner believing the flag worked."""
        from immich_memories.analysis.source_filter import from_the_camera_roll

        config = MagicMock()
        config.analysis.exclude_filename_patterns = []
        config.analysis.exclude_stills_without_camera_exif = False
        config.analysis.include_off_timeline_assets = True

        with caplog.at_level("WARNING"):
            from_the_camera_roll(list(self._assets()), config)

        assert "generation ignores it" in caplog.text
