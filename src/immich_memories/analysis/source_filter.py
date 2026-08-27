"""Source files a memory must never use, decided by their name alone.

Doorbells, security cameras, screen recorders and messaging apps all upload
into the same timeline as the camera roll. None of it was shot to be kept, and
some of it scores well: a doorbell is a perfectly stable camera pointed at a
place people walk through, and the analysis rated one 0.7 for interest and
called it "people" — correctly, since a person really did arrive at a door.

The filename is the only thing that settles this before analysis has looked at
anything, which is what keeps these out of the analysis budget and what makes
the rule work for anyone with no LLM configured at all. The holistic review is
the second line, for the cameras whose filenames give nothing away.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def is_editorial_source_asset(
    asset: Any,
    *,
    start_at: datetime | None,
    end_at: datetime | None,
) -> bool:
    """Whether an asset is in the requested editorial scope before Pass 0."""
    from immich_memories.api.models import AssetType

    if getattr(asset, "type", None) not in (AssetType.IMAGE, AssetType.VIDEO):
        return False
    taken_at = getattr(asset, "file_created_at", None)
    if taken_at is None:
        return False
    return (start_at is None or taken_at >= start_at) and (end_at is None or taken_at <= end_at)


def live_photo_component_ids(sources: Iterable[Any]) -> frozenset[str]:
    """The video halves claimed by the stills in this pool.

    Immich lists a Live Photo's motion component as its own video asset, so one
    query returns both halves of the same photograph. The still is the
    photograph; the component is how it can be rendered. Reading the pool for
    the components its own stills claim is what `drop_live_photo_components`
    does for every other pool builder, expressed as a set the editorial path
    can put on the record instead of dropping silently.
    """
    return frozenset(
        component
        for source in sources
        if (component := getattr(source, "live_photo_video_id", None))
    )


def from_an_excluded_source(name: str | None, patterns: Sequence[str]) -> bool:
    """True when a source filename matches one of the excluded glob patterns.

    Case-insensitive: the same export is written `RPReplay_Final` on one
    platform and lower case on another.
    """
    if not name:
        return False
    lowered = name.casefold()
    return any(fnmatch.fnmatch(lowered, pattern.casefold()) for pattern in patterns)


def is_a_still(asset: Any) -> bool:
    """A photograph, and not a Live Photo.

    A Live Photo carries a video component and is rendered from it, so every
    rule about footage applies to it. Reading the asset type alone calls it a
    still and exempts it from exactly those rules.
    """
    from immich_memories.api.models import AssetType

    return getattr(asset, "type", None) == AssetType.IMAGE and not getattr(
        asset, "live_photo_video_id", None
    )


def _a_still_with_no_camera(asset: Any) -> bool:
    """A photograph whose EXIF names no camera at all.

    Measured across four months of a real library, 5260 assets. Of the 1541
    stills with no EXIF make, 1498 were .jpg named for a messaging app, 34
    were .png downloads, and 9 were camera originals that had lost their make
    somewhere. Of 224 make-less videos, 25 were genuine phone clips — so this
    reads stills only, and video is left to the filename rule.
    """
    if not is_a_still(asset):
        return False
    exif = getattr(asset, "exif_info", None)
    return not (exif and getattr(exif, "make", None))


def not_on_the_timeline(asset: Any) -> bool:
    """True for anything Immich does not show on the timeline.

    A privacy gate rather than a provenance one, which is why -- alone among
    the rules here -- a star does not lift it. `not_shot_here` asks whether
    anybody wanted this asset, and a favourite answers that directly. This asks
    whether we may look at the asset at all, which a favourite cannot speak to:
    a picture in the locked folder is likely to be BOTH starred and flattering
    to any score, exactly the combination that must never ship.

    The server already refuses `visibility=locked` to an API key with 401
    "Elevated permission is required", so locked assets do not reach this code
    today. It is asserted here anyway, because a remote default we do not
    control is not a gate.

    `hidden` is the live one. The default metadata search returns it unasked --
    3,303 assets on a real library, all of them Live Photo video components
    Immich hides so the timeline does not show one shot twice. `isArchived` is
    false on every one of them, so the older boolean does not cover this.
    """
    visibility = getattr(asset, "visibility", None)
    if visibility is not None and visibility != "timeline":
        return True
    return bool(getattr(asset, "is_archived", False))


def not_shot_here(
    asset: Any,
    *,
    patterns: Sequence[str],
    stills_need_a_camera: bool,
) -> bool:
    """True when nothing about this asset says the library's own camera made it.

    Cheap and metadata-only on purpose. Anything that will be thrown away
    later should be gone before it is judged — before it counts toward a day's
    volume, before it is sampled into a prompt, and before analysis pays for
    it. Detection and generation import this from here so they cannot end up
    disagreeing about what the library contains.

    A star settles it, as it settles every other hard gate in selection. Where
    this asset came from is a guess about whether anybody wanted it; a
    favourite is the answer, given directly.
    """
    if getattr(asset, "is_favorite", False):
        return False
    if from_an_excluded_source(getattr(asset, "original_file_name", None), patterns):
        return True
    return stills_need_a_camera and _a_still_with_no_camera(asset)


def from_the_camera_roll(photo_assets: list[Any], config: Any) -> list[Any]:
    """Drop the photos nothing says the library's own camera made.

    Videos are filtered on the same rule before analysis; photos reached
    selection without ever being asked, so a collage forwarded through a
    messaging app walked into a year recap while a doorbell clip beside it was
    turned away. Dropped here rather than later because there is no sense
    paying a VLM to score something that cannot ship.
    """
    analysis = getattr(config, "analysis", None)
    patterns = getattr(analysis, "exclude_filename_patterns", ())
    stills_need_a_camera = getattr(analysis, "exclude_stills_without_camera_exif", False)
    # Read only to say out loud that it is being ignored. The setting lets
    # analysis be pointed at the archive on purpose; generation refuses it,
    # because a gate that depends on remembering a setting is not a gate. A
    # silent refusal would be worse than no setting at all -- someone would
    # turn it on and never learn it did nothing here.
    if getattr(analysis, "include_off_timeline_assets", False):
        logger.warning(
            "include_off_timeline_assets is on, and generation ignores it: "
            "archived, hidden and locked assets stay out of the video."
        )
    on_the_timeline = [asset for asset in photo_assets if not not_on_the_timeline(asset)]
    if len(on_the_timeline) < len(photo_assets):
        logger.info(
            "Source filter: %d photo(s) Immich keeps off the timeline",
            len(photo_assets) - len(on_the_timeline),
        )
    photo_assets = on_the_timeline
    if not patterns and not stills_need_a_camera:
        return photo_assets
    kept = [
        asset
        for asset in photo_assets
        if not not_shot_here(asset, patterns=patterns, stills_need_a_camera=stills_need_a_camera)
    ]
    if len(kept) < len(photo_assets):
        logger.info(
            "Source filter: %d photo(s) from excluded sources", len(photo_assets) - len(kept)
        )
    return kept
