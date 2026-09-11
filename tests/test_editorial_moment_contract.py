"""The production moment editor preserves the proven probe contract byte for byte.

The probe's moment-cut comparisons (probe_description_moment_cut) stay on the probe branch.

The three comparisons against the probe's moment-cut module stay on the probe branch.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


from immich_memories.analysis import editorial_moment_contract as contract
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_source_groups import EditorialGroup
from immich_memories.api.models import ExifInfo, Person
from tests.conftest import make_asset


def _card() -> contract.MomentCard:
    captured = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
    favourite = make_asset(
        "asset-a",
        file_created_at=captured,
        is_favorite=True,
    )
    favourite.people = [Person(id="person-a", name="Alex")]
    favourite.exif_info = ExifInfo(
        city="Brussels",
        state="Brussels",
        country="Belgium",
    )
    video = make_asset(
        "asset-b",
        file_created_at=captured + timedelta(seconds=65),
    )
    candidates = (
        EditorialCandidate(
            asset_id=favourite.id,
            taken_at=favourite.file_created_at,
            media_kind="photo",
            live_photo_stitch_member_ids=(),
            rendering_family_id=None,
            favourite=True,
            source=favourite,
            proposed_segment=None,
            shippable_duration=0.0,
            grounded_annotations=(),
        ),
        EditorialCandidate(
            asset_id=video.id,
            taken_at=video.file_created_at,
            media_kind="video",
            live_photo_stitch_member_ids=(),
            rendering_family_id=None,
            favourite=False,
            source=video,
            proposed_segment=None,
            shippable_duration=0.0,
            grounded_annotations=(),
        ),
    )
    moment = contract.Moment(
        alias="M001",
        group=EditorialGroup("group-private", candidates),
        descriptions=(
            contract.Description("asset-a", "unused"),
            contract.Description("asset-b", "unused"),
        ),
    )
    return contract.MomentCard(
        moment=moment,
        summary="Alex crosses the finish while a friend cheers.",
        answer=None,
        people_metadata=({"name": "Alex", "relationship": "friend"},),
    )
