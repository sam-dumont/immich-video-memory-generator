"""The pool that selection actually chooses from.

Analysis hands over video candidates; this merges the memory's still photos in
so both compete on one scale, then drops what should never have been in the
running: messaging re-encodes, stills a clip from the same moment already
shows, and more animals or objects than the runtime can spare.

The CLI runner and the UI's clip pipeline both build their pool here. That is
the point — a rule added on one path used to miss the other.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config


def _merge_photos_into_pool(
    analyzed_videos: list,
    *,
    photo_assets: list | None,
    include_photos: bool,
    use_live_photos: bool = True,
    config: Config,
    client: SyncImmichClient,
    work_dir: Path,
    provider_circuit=None,
    dry_run: bool = False,
    thumbnail_cache=None,
) -> list:
    """Score photos and merge them as ClipWithSegment into the video pool.

    Returns the combined list of video + photo candidates for unified selection.
    When photos are disabled or absent, returns the video list unchanged.
    """
    if not include_photos or not photo_assets:
        return analyzed_videos

    import logging

    from immich_memories.analysis.source_filter import from_the_camera_roll
    from immich_memories.photos.photo_pipeline import score_photos
    from immich_memories.photos.scoring import score_photo

    _logger = logging.getLogger(__name__)

    # Before anything is fetched or scored: this is the pool both the CLI and
    # the UI actually build, and the rule used to live only on the path
    # neither of them takes.
    photo_assets = from_the_camera_roll(photo_assets, config)
    if not photo_assets:
        return analyzed_videos

    photo_assets = _drop_photos_already_shown_as_motion(
        photo_assets,
        analyzed_videos,
        config=config,
        client=client,
        thumbnail_cache=thumbnail_cache,
    )
    if not photo_assets:
        return analyzed_videos

    photo_duration = config.photos.duration
    include_live_photos = use_live_photos and config.analysis.include_live_photos
    if dry_run:
        scored = [(asset, score_photo(asset, config.photos)) for asset in photo_assets]
    else:
        photo_dir = work_dir / "photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        # The sheet must show the whole moment: the videos and the Live
        # Photos already claimed as motion go alongside the candidates, or a
        # day that lives mostly in footage reads as three photographs.
        seen_ids = {asset.id for asset in photo_assets}
        alongside = []
        for clip in analyzed_videos:
            asset = getattr(clip, "asset", None)
            if asset is not None and asset.id not in seen_ids:
                seen_ids.add(asset.id)
                alongside.append(asset)
        scored = score_photos(
            assets=photo_assets,
            config=config.photos,
            work_dir=photo_dir,
            download_fn=client.download_asset,
            db_path=config.cache.database_path,
            app_config=config,
            thumbnail_fn=client.get_asset_thumbnail,
            provider_circuit=provider_circuit,
            thumbnail_cache=thumbnail_cache,
            alongside=alongside,
        )

    # What the VLM said about each photo, so the holistic review can read a
    # photograph the way it reads a video. Without it every still arrived as a
    # bare line and survived on the rule that protects unanalysed material.
    from immich_memories.photos.scoring import semantic_payloads_for

    payloads = semantic_payloads_for(
        config.cache.database_path, [asset.id for asset, _ in scored], config.llm.model
    )

    motion = _motion_by_carrier(scored, config) if include_live_photos else {}
    photo_candidates = _photo_candidates(scored, motion, payloads, photo_duration)

    _logger.info(
        f"Unified pool: {len(analyzed_videos)} video + {len(photo_candidates)} photo candidates"
    )

    return analyzed_videos + photo_candidates


def _drop_reencoded_sources(candidates: list, *, config: Config) -> list:
    """Drop messaging re-encodes: small, and with no camera EXIF to vouch for them."""
    from immich_memories.analysis.source_quality import is_usable_source

    floor = config.analysis.min_source_short_side
    if floor <= 0:
        return candidates

    kept = [
        c
        for c in candidates
        if is_usable_source(
            width=c.clip.width or c.clip.asset.width or 0,
            height=c.clip.height or c.clip.asset.height or 0,
            has_camera_exif=_has_camera_exif(c.clip.asset),
            min_short_side=floor,
        )
    ]
    if len(kept) < len(candidates):
        logger.info(
            "Source quality: dropped %d clips under %dp with no camera EXIF",
            len(candidates) - len(kept),
            floor,
        )
    return kept


def _has_camera_exif(asset) -> bool:
    exif = getattr(asset, "exif_info", None)
    return bool(exif and (exif.make or exif.model))


def _apply_subject_policy(
    candidates: list,
    *,
    config: Config,
    content_budget_seconds: float,
    photo_assets: list | None = None,
) -> list:
    """Prefer clips of people, and ration animals and objects by share of runtime."""
    if not config.analysis.subject_policy_enabled:
        return candidates

    from immich_memories.analysis.subject_policy import filter_candidates_by_subject

    return filter_candidates_by_subject(
        candidates,
        animal_ratio=config.analysis.max_animal_ratio,
        object_ratio=config.analysis.max_object_ratio,
        content_budget_seconds=content_budget_seconds,
        photo_asset_ids={a.id for a in photo_assets or []},
    )


def _drop_photos_already_shown_as_motion(
    photo_assets: list,
    analyzed_videos: list,
    *,
    config: Config,
    client: SyncImmichClient,
    thumbnail_cache=None,
) -> list:
    """Remove photos a selected clip from the same moment already shows.

    Runs before scoring so the removed photos never reach the LLM.
    """
    from immich_memories.photos.moment_suppression import filter_photos_covered_by_motion

    motion_clips = [c.clip for c in analyzed_videos if getattr(c, "clip", None) is not None]
    if not motion_clips:
        return photo_assets

    return filter_photos_covered_by_motion(
        photo_assets,
        motion_clips,
        config=config.photos,
        thumbnail_cache=thumbnail_cache,
        thumbnail_fn=client.get_asset_thumbnail,
    )


def _motion_by_carrier(scored: list, config: Config) -> dict:
    """The motion each burst will show, attached to one photograph of it.

    motion_renderings describes a burst against every still in it, because any
    of them may be the one selection keeps. Letting all of them carry it would
    let one burst ship twice, so exactly one photograph carries the motion and
    its siblings stay plain photographs.

    Which one is the favourites law again: the owner's mark first, then merit
    (asset_merit.ranking_key). A burst the owner starred shows its motion
    against the frame they starred.

    A burst that does not stitch past the threshold carries nothing: the
    photograph is the default, and the motion has to be worth the one it would
    replace.
    """
    from immich_memories.analysis.asset_merit import ranking_key
    from immich_memories.analysis.motion_rendering import motion_renderings

    renderings = motion_renderings([asset for asset, _score in scored], config)
    if not renderings:
        return {}

    by_burst: dict[tuple, list] = {}
    for asset, score in scored:
        rendering = renderings.get(asset.id)
        if rendering is None or not rendering.beats_a_still:
            continue
        by_burst.setdefault(rendering.still_ids, []).append((asset, score, rendering))

    carriers = {}
    for members in by_burst.values():
        asset, _score, rendering = max(members, key=lambda m: ranking_key(m[0], m[1]))
        carriers[asset.id] = rendering
    if carriers:
        logger.info(
            "Live Photos: %d burst(s) will render as motion, %d photographs carry them",
            len(by_burst),
            len(carriers),
        )
    return carriers


def _photo_candidates(scored: list, motion: dict, payloads: dict, still_seconds: float) -> list:
    """One candidate per photograph, carrying whatever motion it will show.

    The duration is the rendering's, so a burst that will stitch costs what the
    stitch costs rather than what a still would have.
    """
    from immich_memories.analysis.cache_projection import apply_semantic_payload
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.api.models import VideoClipInfo

    candidates = []
    for asset, score in scored:
        shows = motion.get(asset.id)
        seconds = shows.duration_seconds if shows else still_seconds
        clip = VideoClipInfo(
            asset=asset,
            duration_seconds=seconds,
            width=asset.width,
            height=asset.height,
            live_burst_video_ids=list(shows.video_ids) if shows else None,
            live_burst_trim_points=list(shows.trim_points) if shows else None,
            live_burst_shutter_timestamps=list(shows.shutter_timestamps) if shows else None,
            live_burst_still_ids=list(shows.still_ids) if shows else None,
        )
        apply_semantic_payload(clip, payloads.get(asset.id))
        candidates.append(ClipWithSegment(clip=clip, start_time=0.0, end_time=seconds, score=score))
    return candidates
