"""Splitting an Immich album into the pipeline's media pools (#270)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.album_source import fetch_album_media, split_album_assets
from immich_memories.api.album_service import AlbumRef
from immich_memories.api.models import Asset, AssetType
from immich_memories.config import Config


def _asset(
    asset_id: str,
    asset_type: AssetType,
    created: datetime,
    *,
    live_video_id: str | None = None,
) -> Asset:
    return Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=created,
        fileModifiedAt=created,
        updatedAt=created,
        livePhotoVideoId=live_video_id,
        width=1920,
        height=1080,
    )


def test_splits_videos_stills_and_live_photos_into_their_own_pools():
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    assets = [
        _asset("vid-1", AssetType.VIDEO, base),
        _asset("still-1", AssetType.IMAGE, base + timedelta(minutes=5)),
        _asset("live-1", AssetType.IMAGE, base + timedelta(minutes=10), live_video_id="lv-1"),
    ]

    media = split_album_assets(assets, config=Config(), use_live_photos=True, use_photos=True)

    assert [a.id for a in media.videos] == ["vid-1"]
    assert [a.id for a in media.photos] == ["still-1"]
    assert [c.asset.id for c in media.live_photo_clips] == ["live-1"]


def test_date_range_spans_the_albums_oldest_and_newest_asset():
    oldest = datetime(2007, 5, 4, 9, 0, tzinfo=UTC)
    newest = datetime(2025, 5, 4, 21, 0, tzinfo=UTC)
    assets = [
        _asset("vid-2", AssetType.VIDEO, newest),
        _asset("vid-1", AssetType.VIDEO, oldest),
    ]

    media = split_album_assets(assets, config=Config(), use_live_photos=False, use_photos=False)

    assert media.date_range.start == oldest
    assert media.date_range.end == newest


def test_live_photos_become_stills_when_merging_is_disabled():
    """The user hand-picked these assets — none should silently vanish."""
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    assets = [
        _asset("still-1", AssetType.IMAGE, base),
        _asset("live-1", AssetType.IMAGE, base + timedelta(minutes=1), live_video_id="lv-1"),
    ]

    media = split_album_assets(assets, config=Config(), use_live_photos=False, use_photos=True)

    assert media.live_photo_clips == []
    assert [a.id for a in media.photos] == ["still-1", "live-1"]


def test_a_live_photos_video_component_is_not_also_offered_as_a_video():
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    assets = [
        _asset("live-1", AssetType.IMAGE, base, live_video_id="lv-1"),
        _asset("lv-1", AssetType.VIDEO, base),
        _asset("vid-real", AssetType.VIDEO, base + timedelta(hours=1)),
    ]

    media = split_album_assets(assets, config=Config(), use_live_photos=True, use_photos=True)

    assert [a.id for a in media.videos] == ["vid-real"]


def test_an_empty_album_yields_no_media_and_no_date_range():
    media = split_album_assets([], config=Config(), use_live_photos=True, use_photos=True)

    assert media.videos == []
    assert media.photos == []
    assert media.date_range is None


class _FakeClient:
    """WHY: replaces the Immich API; per-type album fetches are the boundary."""

    def __init__(self, videos: list[Asset], images: list[Asset]) -> None:
        self._by_type = {AssetType.VIDEO: videos, AssetType.IMAGE: images}
        self.limits: dict[AssetType, int | None] = {}

    def get_assets_for_album(self, album_id, *, asset_type, limit=None, progress_callback=None):
        self.limits[asset_type] = limit
        assets = self._by_type[asset_type]
        return assets[:limit] if limit is not None else assets


def test_fetch_reads_each_media_type_under_the_configured_cap():
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    videos = [_asset(f"v{i}", AssetType.VIDEO, base + timedelta(minutes=i)) for i in range(5)]
    images = [_asset(f"p{i}", AssetType.IMAGE, base + timedelta(hours=i)) for i in range(5)]
    client = _FakeClient(videos, images)
    config = Config()
    config.analysis.max_album_assets = 3

    media = fetch_album_media(
        client,
        AlbumRef(id="a-1", name="Trip 2025", asset_count=10),
        config=config,
        use_live_photos=True,
        use_photos=True,
    )

    assert client.limits == {AssetType.VIDEO: 3, AssetType.IMAGE: 3}
    assert len(media.videos) == 3
    assert len(media.photos) == 3
    assert media.truncated is True


def test_fetch_skips_images_entirely_when_photos_and_live_photos_are_off():
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    client = _FakeClient(
        [_asset("v0", AssetType.VIDEO, base)], [_asset("p0", AssetType.IMAGE, base)]
    )

    media = fetch_album_media(
        client,
        AlbumRef(id="a-1", name="Trip 2025", asset_count=2),
        config=Config(),
        use_live_photos=False,
        use_photos=False,
    )

    assert AssetType.IMAGE not in client.limits
    assert media.photos == []
    assert media.truncated is False
