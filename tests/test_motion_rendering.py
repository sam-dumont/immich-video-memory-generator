"""Motion is something a photograph HAS, not a pool it belongs to.

A Live Photo is a photograph that may also render as motion. Modelling it as a
video instead needs a separate clips pool, suppression of the stills that pool
claims, a way to hand back the ones it refuses, and an invariant to prove none
fell between the two — four mechanisms that exist only because of the split.

Here the burst is described once, attached to the photograph it belongs to, and
the choice of rendering is a later question about an asset that already won its
place.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from immich_memories.analysis.motion_rendering import MotionRendering, motion_renderings

NOON = datetime(2024, 6, 4, 12, 0, tzinfo=UTC)


def _live(index: int, *, seconds: float = 0.0):
    from tests.conftest import make_asset

    asset = make_asset(
        f"still-{index}", file_created_at=NOON + timedelta(seconds=seconds), duration=None
    )
    asset.live_photo_video_id = f"video-{index}"
    return asset


def _config(minimum: float = 3.5):
    return SimpleNamespace(
        analysis=SimpleNamespace(
            live_photo_merge_window_seconds=10.0,
            live_photo_min_clip_seconds=minimum,
        )
    )


class TestWhatABurstCouldShow:
    def test_every_photograph_in_a_burst_knows_about_it(self) -> None:
        """Keyed by every still, not just the first: any of them may be the one
        selection keeps, and the motion belongs to all of them equally."""
        burst = [_live(i, seconds=i * 2.0) for i in range(3)]
        found = motion_renderings(burst, _config())
        assert set(found) == {"still-0", "still-1", "still-2"}
        assert len({id(v) for v in found.values()}) == 1

    def test_a_burst_that_merges_is_worth_showing_as_motion(self) -> None:
        burst = [_live(i, seconds=i * 2.0) for i in range(3)]
        rendering = motion_renderings(burst, _config())["still-0"]
        # A real merge of two reaches 4.0s; a lone photograph is always 3.0.
        assert rendering.duration_seconds >= 4.0

    def test_a_lone_photograph_is_not(self) -> None:
        """It stitches to exactly the raw 3.0s: nothing was merged."""
        rendering = motion_renderings([_live(1)], _config())["still-1"]
        assert rendering.duration_seconds == 3.0

    def test_the_photograph_is_offered_either_way(self) -> None:
        """Nothing is removed from anywhere — that is the whole point."""
        lone = _live(1)
        assert "still-1" in motion_renderings([lone], _config())

    def test_a_photograph_with_no_motion_has_none(self) -> None:
        from tests.conftest import make_asset

        plain = make_asset("plain", file_created_at=NOON, duration=None)
        assert motion_renderings([plain], _config()) == {}


class TestWhatTheRendererNeeds:
    def test_a_rendering_carries_everything_the_stitch_needs(self) -> None:
        """video ids, trim points and shutter times — what assembly reads."""
        burst = [_live(i, seconds=i * 2.0) for i in range(3)]
        r = motion_renderings(burst, _config())["still-0"]
        assert isinstance(r, MotionRendering)
        assert len(r.video_ids) == 3
        assert len(r.trim_points) == 3
        assert len(r.shutter_timestamps) == 3


def test_a_second_still_of_the_same_live_video_stays_a_photograph_without_ending_the_plan() -> None:
    """A shared album duplicates a Live Photo's still; both point at one video. The video is
    offered once, by the earliest still; the duplicate still gets no motion offer; nothing raises."""
    first = _live(1)
    twin = _live(2, seconds=0.5)
    twin.live_photo_video_id = first.live_photo_video_id
    other = _live(3, seconds=2.0)
    renderings = motion_renderings([first, twin, other], _config())
    assert first.id in renderings and other.id in renderings
    assert twin.id not in renderings
    assert renderings[first.id].video_ids.count(first.live_photo_video_id) == 1


def _companion(video_id: str, *, duration="0:00:03.000", asset_id=None):
    from tests.conftest import make_asset

    companion = make_asset(asset_id or video_id, file_created_at=NOON, duration=duration)
    return companion


def test_a_still_whose_companion_is_absent_stays_an_ordinary_photograph() -> None:
    """No motion offer is not a failure: the picture remains selectable without one."""
    offered, absent = _live(1), _live(2, seconds=1.0)

    renderings = motion_renderings(
        [offered, absent], _config(), companion_assets={"video-1": _companion("video-1")}
    )

    assert set(renderings) == {"still-1"}


def test_a_companion_offering_no_playable_length_is_no_offer_at_all() -> None:
    unknown = _companion("video-1", duration=None)
    empty = _companion("video-2", duration="0:00:00.000")

    assert motion_renderings([_live(1)], _config(), companion_assets={"video-1": unknown}) == {}
    assert motion_renderings([_live(2)], _config(), companion_assets={"video-2": empty}) == {}


def test_a_companion_that_disagrees_with_its_source_link_is_refused() -> None:
    import pytest

    mislinked = _companion("video-1", asset_id="another-video")

    with pytest.raises(ValueError, match="disagrees with its source link"):
        motion_renderings([_live(1)], _config(), companion_assets={"video-1": mislinked})


def test_a_companion_with_an_impossible_length_is_refused() -> None:
    import pytest

    from tests.conftest import make_asset

    broken = make_asset("video-1", file_created_at=NOON, duration=None)
    broken.duration_seconds = float("inf")

    with pytest.raises(ValueError, match="finite and positive"):
        motion_renderings([_live(1)], _config(), companion_assets={"video-1": broken})


def test_the_companions_own_length_bounds_what_the_burst_can_show() -> None:
    """Without companion metadata a Live still assumes the raw 3.0s; a measured
    companion says how much of that footage actually exists."""
    lone = _live(1)
    companions = {"video-1": _companion("video-1", duration="0:00:01.500")}

    assumed = motion_renderings([lone], _config())["still-1"]
    measured = motion_renderings([lone], _config(), companion_assets=companions)["still-1"]

    assert assumed.duration_seconds == 3.0
    assert measured.duration_seconds == 1.5
    assert measured.trim_points == ((0.0, 1.5),)
