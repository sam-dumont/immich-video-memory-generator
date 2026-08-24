"""A day is parallel threads, not a timeline.

Measured on a real day: a circuit at 16:37, home at 16:49, the circuit again at
18:05 — 120km apart, twelve minutes. Two devices, two people, one library. Group
that by time alone and you get a story neither of them had.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

# The private name on purpose: these test the grouping rules themselves, not
# the filtered door every reader goes through. moments_to_read is tested below.
from immich_memories.analysis.moment_grouping import (
    _group_by_time_and_place as group_by_time_and_place,
)

MIDDAY = datetime(2024, 6, 4, 12, 0, tzinfo=UTC)
CIRCUIT = (50.437, 5.971)
HOME = (50.878, 4.326)


def _asset(name: str, minutes: int, where: tuple[float, float] | None = CIRCUIT):
    exif = None
    if where is not None:
        exif = SimpleNamespace(latitude=where[0], longitude=where[1])
    return SimpleNamespace(
        id=name, file_created_at=MIDDAY + timedelta(minutes=minutes), exif_info=exif
    )


def test_close_in_time_and_place_is_one_moment() -> None:
    group = [_asset("a", 0), _asset("b", 2), _asset("c", 5)]
    assert [[a.id for a in m] for m in group_by_time_and_place(group)] == [["a", "b", "c"]]


def test_a_gap_in_time_starts_a_new_moment() -> None:
    group = [_asset("a", 0), _asset("b", 90)]
    assert [[a.id for a in m] for m in group_by_time_and_place(group)] == [["a"], ["b"]]


def test_far_apart_at_the_same_time_is_two_moments_not_one() -> None:
    """The finding: someone else was somewhere else, at the same time."""
    group = [_asset("circuit", 0), _asset("home", 3, HOME), _asset("circuit2", 6)]
    moments = group_by_time_and_place(group)
    assert [[a.id for a in m] for m in moments] == [["circuit", "circuit2"], ["home"]]


def test_an_asset_without_a_place_joins_the_moment_it_sits_in() -> None:
    """Most libraries have some assets with no GPS; they must not all cluster."""
    group = [_asset("a", 0), _asset("b", 2, None), _asset("c", 4)]
    assert [[a.id for a in m] for m in group_by_time_and_place(group)] == [["a", "b", "c"]]


def test_travelling_is_one_thread_when_the_time_allows_it() -> None:
    """A device that drove there is not a second person: hours, not minutes."""
    group = [_asset("home", 0, HOME), _asset("circuit", 180)]
    assert len(group_by_time_and_place(group)) == 2


class TestForeignMediaNeverReachesASheet:
    """Received media is often the most striking thing in an episode.

    A period read from unfiltered sheets reported a wedding, a fresh tattoo and
    a grid comparing chihuahuas to muffins as the month's remarkable days. The
    library owner's verdict: forwarded messages, screenshots and a meme. The
    sheet was not seeing more than the text — it was seeing things that were
    never photographed here.

    source_filter already says this: gone "before it is sampled into a prompt".
    Reading bypassed it, so the filter now sits where assets enter grouping and
    every reader inherits it.
    """

    def _config(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            analysis=SimpleNamespace(
                exclude_filename_patterns=("IMG-*-WA*", "Screenshot*"),
                exclude_stills_without_camera_exif=True,
            )
        )

    def _asset(self, name: str, minutes: int, *, make: str | None = "Apple", fav: bool = False):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=name,
            original_file_name=name,
            is_favorite=fav,
            type="IMAGE",
            duration=None,
            exif_info=SimpleNamespace(make=make, city=None, latitude=None, longitude=None),
            file_created_at=MIDDAY + timedelta(minutes=minutes),
        )

    def test_a_forwarded_file_never_reaches_a_moment(self) -> None:
        from immich_memories.analysis.moment_grouping import moments_to_read

        assets = [
            self._asset("IMG_0001.HEIC", 0),
            self._asset("IMG-20240604-WA0007.jpg", 1),
            self._asset("Screenshot 2024-06-04.png", 2),
        ]
        moments = moments_to_read(assets, self._config())
        assert [a.id for m in moments for a in m] == ["IMG_0001.HEIC"]

    def test_a_still_no_camera_made_never_reaches_a_moment(self) -> None:
        """1498 of 1541 make-less jpgs in one real library were messaging files."""
        from immich_memories.analysis.moment_grouping import moments_to_read

        assets = [self._asset("IMG_0002.HEIC", 0), self._asset("forward.jpg", 1, make=None)]
        moments = moments_to_read(assets, self._config())
        assert [a.id for m in moments for a in m] == ["IMG_0002.HEIC"]

    def test_a_favourite_survives_whatever_it_looks_like(self) -> None:
        """A star settles it here as everywhere: the owner said this one matters."""
        from immich_memories.analysis.moment_grouping import moments_to_read

        assets = [self._asset("IMG-20240604-WA0009.jpg", 0, make=None, fav=True)]
        moments = moments_to_read(assets, self._config())
        assert [a.id for m in moments for a in m] == ["IMG-20240604-WA0009.jpg"]
