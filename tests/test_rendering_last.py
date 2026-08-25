"""Rendering is a question about an asset that already won its place.

A Live Photo is a photograph that MAY also render as motion. Modelling it as a
video instead needed a separate clips pool, suppression of the stills that pool
claimed, a way to hand back the ones it refused, and an invariant proving none
fell between the two.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from immich_memories.api.models import Asset, AssetType
from immich_memories.config_models_analysis import AnalysisConfig


def _image(asset_id: str, *, live: bool = False) -> Asset:
    now = datetime.now(tz=UTC)
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        livePhotoVideoId="live-vid" if live else None,
    )


class TestTheThresholdIsConfigurable:
    """The measured 3.5s boundary must live in the schema, not in a getattr."""

    def test_the_minimum_is_a_real_setting(self):
        """motion_rendering reads live_photo_min_clip_seconds off the config.

        It read it through getattr(analysis, ..., 3.5) against a field that did
        not exist, so the measured boundary was a default nothing would flag if
        it drifted, and no user could change it.

        The value divides a lone Live Photo, which stitches to exactly the raw
        3.0s with nothing merged, from the smallest genuine merge of two, which
        reaches 4.0s.
        """
        assert AnalysisConfig().live_photo_min_clip_seconds == 3.5


class TestALivePhotoIsAPhoto:
    """The photo pool holds every photograph, however it was captured."""

    @pytest.mark.asyncio
    async def test_the_photo_pool_holds_live_photo_stills(self):
        """A Live Photo still is a candidate photograph like any other.

        It used to be dropped at the API layer, so the pool selection saw
        excluded most of the library: on a real month 17 of 18 favourites are
        Live Photos. They were fetched separately and turned into clips in
        their own pool, which is the split this removes -- whether the burst is
        worth showing as motion is a rendering question, asked later, about an
        asset that has already won its place.
        """
        from unittest.mock import AsyncMock

        from immich_memories.api.models import MetadataSearchResult
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        plain = _image("photo-1")
        live = _image("live-1", live=True)

        service = SearchService(AsyncMock())
        # WHY: mock the HTTP search so the pool contents are what is tested.
        service.search_metadata = AsyncMock(
            return_value=MetadataSearchResult(
                assets={"items": [plain.model_dump(by_alias=True), live.model_dump(by_alias=True)]},
                nextPage=None,
            )
        )

        found = await service.get_photos_for_date_range(
            DateRange(
                start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 12, 31, tzinfo=UTC)
            )
        )

        assert {a.id for a in found} == {"photo-1", "live-1"}


def _live_still(index: int, *, seconds: float = 0.0) -> Asset:
    from datetime import timedelta

    from tests.conftest import make_asset

    asset = make_asset(
        f"still-{index}",
        file_created_at=datetime(2024, 6, 4, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        duration=None,
        exif_make="Apple",
    )
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = f"video-{index}"
    return asset


def _pool(tmp_path, photos):
    """The candidate pool both the CLI and the wizard build."""
    from unittest.mock import MagicMock

    from immich_memories.cli._candidate_pool import _merge_photos_into_pool
    from immich_memories.config import Config

    return _merge_photos_into_pool(
        [],
        photo_assets=photos,
        include_photos=True,
        config=Config(cache={"directory": str(tmp_path / "cache")}),
        client=MagicMock(),
        work_dir=tmp_path,
        dry_run=True,
    )


class TestRenderingIsChosenAfterRanking:
    """One candidate per photograph, carrying the motion it could show."""

    def test_a_burst_worth_merging_carries_its_motion(self, tmp_path):
        """A burst that stitches past the threshold ships as motion.

        The candidate is the photograph either way; what it carries decides how
        it is rendered, so it can neither ship twice nor ship as neither.
        """
        burst = [_live_still(i, seconds=i * 2.0) for i in range(3)]

        pool = _pool(tmp_path, burst)

        assert pool
        winner = pool[0]
        assert winner.clip.live_burst_video_ids
        assert winner.end_time - winner.start_time >= 4.0

    def test_a_lone_live_photo_ships_as_a_photograph(self, tmp_path):
        """It stitches to exactly the raw 3.0s, which is not worth a still."""
        pool = _pool(tmp_path, [_live_still(1)])

        assert pool
        candidate = pool[0]
        assert not candidate.clip.live_burst_video_ids
        assert candidate.end_time - candidate.start_time == 4.0


class TestTheRendererAsksWhatTheCandidateCarries:
    """Not what kind of asset it is: a Live Photo still is a photograph."""

    def test_a_live_photo_still_with_no_motion_renders_as_a_photograph(self, tmp_path):
        """Carrying no burst, it is a photograph and must be rendered as one.

        The renderer used to branch on whether the asset had a video component
        at all, so every Live Photo still went down the video path and tried to
        download a video for a photograph that had already lost its motion.
        """
        from unittest.mock import MagicMock, patch

        from immich_memories.api.models import VideoClipInfo
        from immich_memories.generate import GenerationParams
        from immich_memories.processing.assembly_config import AssemblyClip

        still = _live_still(7)
        clip = VideoClipInfo(asset=still, duration_seconds=4.0)

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=MagicMock(),
            client=MagicMock(),
        )
        rendered = AssemblyClip(
            path=tmp_path / "p.mp4", duration=4.0, date=None, asset_id="still-7"
        )

        # WHY: the photo renderer shells out to FFmpeg; this asks which branch
        # was taken, not what it produced.
        with (
            patch(
                "immich_memories.generate_photos._render_photo_as_clip", return_value=rendered
            ) as render_photo,
            patch("immich_memories.generate_downloads.download_clip") as download,
        ):
            from immich_memories.generate_clips import _extract_clips

            _extract_clips(params, None, tmp_path)

        render_photo.assert_called_once()
        download.assert_not_called()

    def test_the_rest_of_a_burst_stay_photographs(self, tmp_path):
        """Exactly one photograph of a burst carries the motion.

        Keyed by every still, a burst would render once per still that won, so
        one moment could ship several times. Attaching it to one carrier makes
        that impossible rather than filtered out afterwards -- and the siblings
        are still offered, as the photographs they are, which is what the old
        suppression pass removed them from being.
        """
        burst = [_live_still(i, seconds=i * 2.0) for i in range(3)]

        pool = _pool(tmp_path, burst)

        assert len(pool) == 3
        carriers = [c for c in pool if c.clip.live_burst_video_ids]
        assert len(carriers) == 1
        assert all(c.end_time - c.start_time == 4.0 for c in pool if c not in carriers)

    def test_the_owners_mark_carries_its_bursts_motion(self, tmp_path):
        """Which photograph of a burst shows the motion is the favourites law.

        A burst the owner starred shows its motion against the frame they
        starred, not against whichever came back from the API first.
        """
        burst = [_live_still(i, seconds=i * 2.0) for i in range(3)]
        burst[2].is_favorite = True

        pool = _pool(tmp_path, burst)

        carriers = [c for c in pool if c.clip.live_burst_video_ids]
        assert [c.clip.asset.id for c in carriers] == ["still-2"]
