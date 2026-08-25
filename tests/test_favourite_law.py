"""Within a moment, the favourite wins. Always.

A rule that lives in a prompt is a rule the next prompt edit deletes. This one
is measurable: given the pool selection chose from and what it shipped, either
no moment shipped something else while its favourite was dropped, or the ones
that did can be named.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.favourite_law import moments_that_lost_their_favourite
from tests.conftest import make_asset

NOON = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


def _asset(asset_id: str, *, minutes: float = 0.0, favourite: bool = False):
    return make_asset(
        asset_id,
        file_created_at=NOON + timedelta(minutes=minutes),
        is_favorite=favourite,
        duration=None,
    )


class TestTheFavouriteWinsItsMoment:
    def test_a_moment_that_shipped_someone_else_is_named(self):
        """The favourite was dropped and a neighbour from the same moment shipped.

        This is the violation the law exists to forbid: not that every
        favourite ships, but that none is passed over in favour of something
        standing beside it.
        """
        starred = _asset("starred", favourite=True)
        neighbour = _asset("neighbour", minutes=1)

        lost = moments_that_lost_their_favourite([starred, neighbour], {"neighbour"})

        assert len(lost) == 1
        assert lost[0].favourites == ("starred",)
        assert lost[0].shipped == ("neighbour",)

    def test_a_moment_represented_by_its_favourite_is_not_a_violation(self):
        starred = _asset("starred", favourite=True)
        neighbour = _asset("neighbour", minutes=1)

        assert moments_that_lost_their_favourite([starred, neighbour], {"starred"}) == []

    def test_a_moment_nobody_shipped_is_not_a_violation(self):
        """A memory has a runtime; whole moments go unshown.

        The law is not that every favourite ships. It is that none is passed
        over in favour of something standing beside it.
        """
        starred = _asset("starred", favourite=True)
        neighbour = _asset("neighbour", minutes=1)
        elsewhere = _asset("elsewhere", minutes=600)

        assert (
            moments_that_lost_their_favourite([starred, neighbour, elsewhere], {"elsewhere"}) == []
        )

    def test_a_moment_in_another_place_is_another_moment(self):
        """Two devices at the same time in different places are parallel threads.

        A favourite shot 120km away is not passed over by a photograph taken at
        the same minute somewhere else.
        """
        starred = _asset("starred", favourite=True)
        starred.exif_info.latitude, starred.exif_info.longitude = 50.85, 4.35
        far = _asset("far", minutes=1)
        far.exif_info.latitude, far.exif_info.longitude = 51.92, 4.48

        assert moments_that_lost_their_favourite([starred, far], {"far"}) == []


def _item(asset):
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.api.models import VideoClipInfo

    return ClipWithSegment(
        clip=VideoClipInfo(asset=asset, duration_seconds=4.0),
        start_time=0.0,
        end_time=4.0,
        score=0.9 if not asset.is_favorite else 0.1,
    )


class TestTheLawIsEnforcedNotJustMeasured:
    """Selection repairs the violation rather than reporting it."""

    def test_the_favourite_takes_the_place_of_its_neighbour(self):
        """Substitute, never add: the moment keeps one seat, and the star has it.

        Adding the favourite instead would grow the cut past the runtime it
        was fitted to. The neighbour gave up nothing but its place.
        """
        from immich_memories.analysis.favourite_law import let_the_favourite_win

        starred = _item(_asset("starred", favourite=True))
        neighbour = _item(_asset("neighbour", minutes=1))
        far = _item(_asset("far", minutes=600))

        repaired = let_the_favourite_win([neighbour, far], [starred, neighbour, far])

        assert {i.clip.asset.id for i in repaired} == {"starred", "far"}

    def test_a_moment_already_showing_its_favourite_is_left_alone(self):
        from immich_memories.analysis.favourite_law import let_the_favourite_win

        starred = _item(_asset("starred", favourite=True))
        neighbour = _item(_asset("neighbour", minutes=1))

        repaired = let_the_favourite_win([starred], [starred, neighbour])

        assert [i.clip.asset.id for i in repaired] == ["starred"]


class TestTheDayCapEvictsInTheRightOrder:
    """Where the bulk of the loss happens, and it was favourite-blind."""

    def test_a_days_cap_keeps_the_favourite_over_a_higher_scored_neighbour(self):
        """The per-day cap ranked on score alone and dropped stars wholesale.

        Measured on one real August: 52 favourites in the pool, 13 left after
        this one stage. It keeps the best N photographs of each day, and "best"
        did not know what the owner had starred.
        """
        from immich_memories.analysis.clip_distribution import _partition_photos_per_day
        from immich_memories.api.models import AssetType

        starred = _item(_asset("starred", favourite=True))
        starred.score = 0.1
        rivals = []
        for index in range(6):
            rival = _item(_asset(f"rival-{index}", minutes=index * 30))
            rival.score = 0.9
            rivals.append(rival)
        for item in [starred, *rivals]:
            item.clip.asset.type = AssetType.IMAGE

        kept, overflow = _partition_photos_per_day([starred, *rivals])

        assert "starred" in {c.clip.asset.id for c in kept}
        assert "starred" not in {c.clip.asset.id for c in overflow}
