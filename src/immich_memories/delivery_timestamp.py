"""When a finished memory happened, and how that instant is written down.

A memory is a stretch of days that ends somewhere. The render day says nothing
about it, so both the container tags and the Immich upload fields are stamped
with the memory's own last moment instead.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from immich_memories.api.models import Asset

__all__ = [
    "CARRY_CONTAINER_METADATA",
    "capture_metadata_args",
    "film_capture_instant",
]

_QUARTER_HOUR = timedelta(minutes=15)

# A later FFmpeg pass over a finished film (the music mix) rebuilds the
# container, and a complex filtergraph switches off FFmpeg's own default
# metadata mapping. Without these two the film's capture date is thrown away
# between the render and the upload.
CARRY_CONTAINER_METADATA: tuple[str, ...] = (
    "-map_metadata",
    "0",
    "-movflags",
    "+use_metadata_tags",
)


def _asset_offset(asset: Asset) -> timedelta | None:
    """The UTC offset the picture was shot at, or None when nothing says."""
    local = asset.local_date_time
    if local is not None:
        naive_local = local.replace(tzinfo=UTC) if local.tzinfo is None else local
        raw = naive_local - asset.file_created_at
        return _QUARTER_HOUR * round(raw / _QUARTER_HOUR)

    original = asset.exif_info.date_time_original if asset.exif_info else None
    if original is not None:
        return original.utcoffset()
    return None


def film_capture_instant(assets: Iterable[Asset]) -> datetime | None:
    """The capture instant a finished memory should be filed under.

    The memory is filed on the day of its **last** picture, told in the timezone
    **most** of its pictures were shot in — so a trip abroad still lands in the
    timeline where the rest of the memory sits.

    A picture's offset is ``localDateTime - fileCreatedAt`` rounded to the
    quarter hour (every real zone is a multiple of one), or the offset EXIF's
    ``dateTimeOriginal`` carries when Immich reports no local clock. Offsets are
    counted; the most common one wins, ties going to the one whose own pictures
    run latest. "Last" is by local wall clock.

    Returns None when no picture carries a usable time, which leaves the caller
    on its previous behaviour — for an upload, the render day.
    """
    dated = [
        (asset.file_created_at + offset, asset.file_created_at, offset)
        for asset in assets
        if (offset := _asset_offset(asset)) is not None
    ]
    if not dated:
        return None

    counts = Counter(offset for _, _, offset in dated)
    latest_per_offset = {
        offset: max(wall for wall, _, own in dated if own == offset) for offset in counts
    }
    modal_offset = max(counts, key=lambda offset: (counts[offset], latest_per_offset[offset]))

    last_instant = max(dated)[1]
    return last_instant.astimezone(timezone(modal_offset))


def capture_metadata_args(captured_at: datetime | None) -> list[str]:
    """FFmpeg ``-metadata`` arguments stamping a film with its capture instant.

    ``creation_time`` is the instant in UTC. The QuickTime key beside it keeps
    the offset, which is what Immich's exiftool pass reads to recover the local
    time and zone; FFmpeg only writes it with ``-movflags +use_metadata_tags``.
    """
    if captured_at is None:
        return []
    utc = captured_at.astimezone(UTC).replace(microsecond=0)
    local = captured_at.replace(microsecond=0)
    return [
        "-metadata",
        f"creation_time={utc.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "-metadata",
        f"com.apple.quicktime.creationdate={local.isoformat()}",
    ]
