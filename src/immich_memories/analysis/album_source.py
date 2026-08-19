"""Turn an Immich album into the pipeline's media pools.

Album mode replaces date-range discovery: the album *is* the candidate pool, so
nothing is searched for. A birthday album spanning 18 years would otherwise make
the date-range path fetch the whole library.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from immich_memories.api.models import AssetType
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.album_service import AlbumRef
    from immich_memories.api.models import Asset, VideoClipInfo
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


@dataclass
class AlbumMedia:
    """The album's assets, split into the pools the pipeline consumes."""

    videos: list[Asset] = field(default_factory=list)
    live_photo_clips: list[VideoClipInfo] = field(default_factory=list)
    photos: list[Asset] = field(default_factory=list)
    date_range: DateRange | None = None
    truncated: bool = False


def split_album_assets(
    assets: list[Asset],
    *,
    config: Config,
    use_live_photos: bool,
    use_photos: bool,
) -> AlbumMedia:
    """Split an album's assets into videos, merged Live Photo clips and stills.

    With Live Photo merging off, a Live Photo counts as a still rather than being
    dropped — the user picked these assets by hand, so nothing silently disappears.
    """
    if not assets:
        return AlbumMedia()

    videos = [a for a in assets if a.type == AssetType.VIDEO]
    images = [a for a in assets if a.type == AssetType.IMAGE]
    live_stills = [a for a in images if a.is_live_photo]
    plain_stills = [a for a in images if not a.is_live_photo]

    live_clips: list[VideoClipInfo] = []
    if use_live_photos and live_stills:
        from immich_memories.analysis.live_photo_pipeline import build_live_photo_clips

        live_clips, live_video_ids = build_live_photo_clips(live_stills, config=config)
        # The album may also list the Live Photo's video component as its own asset.
        videos = [v for v in videos if v.id not in live_video_ids]
    elif live_stills:
        plain_stills = sorted(plain_stills + live_stills, key=lambda a: a.file_created_at)

    photos = plain_stills if use_photos else []

    logger.info(
        "Album pool: %d videos, %d live photo clips, %d photos (from %d assets)",
        len(videos),
        len(live_clips),
        len(photos),
        len(assets),
    )
    return AlbumMedia(
        videos=videos,
        live_photo_clips=live_clips,
        photos=photos,
        date_range=DateRange(
            start=min(a.file_created_at for a in assets),
            end=max(a.file_created_at for a in assets),
        ),
    )


def fetch_album_media(
    client: SyncImmichClient,
    album: AlbumRef,
    *,
    config: Config,
    use_live_photos: bool,
    use_photos: bool,
) -> AlbumMedia:
    """Read an album's media, one bounded request stream per asset type.

    Images are skipped entirely when neither photos nor Live Photos are wanted —
    on a 34k-image smart album that request stream alone costs ~58 MB and ~20 s.
    """
    cap = config.analysis.max_album_assets

    videos = client.get_assets_for_album(album.id, asset_type=AssetType.VIDEO, limit=cap)
    images: list[Asset] = []
    if use_photos or use_live_photos:
        images = client.get_assets_for_album(album.id, asset_type=AssetType.IMAGE, limit=cap)

    truncated = len(videos) >= cap or len(images) >= cap
    if truncated:
        logger.warning(
            "Album %r holds more than %d assets per type — using the most recent %d. "
            "Narrow the album, raise advanced.analysis.max_album_assets, or use a date range.",
            album.name,
            cap,
            cap,
        )

    media = split_album_assets(
        videos + images,
        config=config,
        use_live_photos=use_live_photos,
        use_photos=use_photos,
    )
    media.truncated = truncated
    return media


def album_media_as_clips(media: AlbumMedia) -> tuple[list[VideoClipInfo], list[Asset]]:
    """Flatten an album's media into the clip and photo pools the wizard uses.

    Videos and merged Live Photo clips are one pool, ordered by capture time, so
    the album plays back in the order it was lived rather than by media type.
    """
    from immich_memories.generate import assets_to_clips

    clips = assets_to_clips(media.videos) + media.live_photo_clips
    clips.sort(key=lambda clip: clip.asset.file_created_at)
    return clips, media.photos.copy()


# An album is one curated event, so the pool is mostly keepers: a few seconds
# each reads as a highlight reel rather than a slideshow.
_SECONDS_PER_ITEM = 4.0
_MIN_TARGET_MINUTES = 0.5
_MAX_TARGET_MINUTES = 10.0


def album_target_minutes(clips: list[VideoClipInfo], photos: list[Asset]) -> float:
    """Target length for an album memory, scaled to how much is in the album.

    Albums are the one memory type with no preset behind them, so nothing else
    supplies a target: without this the wizard keeps whatever the last-clicked
    preset left in state.
    """
    items = len(clips) + len(photos)
    return min(_MAX_TARGET_MINUTES, max(_MIN_TARGET_MINUTES, items * _SECONDS_PER_ITEM / 60))
