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


class TestOneDefinitionOfABurst:
    """Clips are built from the renderings, so there is one definition of a burst.

    Two places computing what a burst is means two places to disagree about it.
    """

    def test_a_clip_carries_exactly_what_its_rendering_describes(self) -> None:
        from immich_memories.analysis.live_photo_pipeline import build_live_photo_clips

        burst = [_live(i, seconds=i * 2.0) for i in range(3)]
        clips, _videos = build_live_photo_clips(burst, config=_config())
        rendering = motion_renderings(burst, _config())["still-0"]

        assert len(clips) == 1
        clip = clips[0]
        assert tuple(clip.live_burst_video_ids or ()) == rendering.video_ids
        assert tuple(clip.live_burst_trim_points or ()) == rendering.trim_points
        assert clip.duration_seconds == rendering.duration_seconds
        assert tuple(clip.live_burst_still_ids or ()) == rendering.still_ids

    def test_a_merged_burst_carries_its_components_a_single_one_does_not(self) -> None:
        """A stitch needs its parts listed; a single component does not."""
        from immich_memories.analysis.live_photo_pipeline import build_live_photo_clips

        burst = [_live(i, seconds=i * 2.0) for i in range(3)]
        clips, _videos = build_live_photo_clips(burst, config=_config())
        assert clips[0].live_burst_video_ids is not None


class TestMotionHasToBeatThePhotograph:
    """A Live Photo is a photograph unless the motion is worth more than it.

    Measured: a lone Live Photo stitches to exactly 3.0s — the raw clip, nothing
    merged — while the smallest genuine merge of two reaches 4.0s. So a burst of
    one is a second of wobble displacing a photograph that would have shipped.
    """

    def test_a_burst_that_merges_becomes_a_clip(self) -> None:
        from immich_memories.analysis.live_photo_pipeline import build_live_photo_clips

        burst = [_live(i, seconds=i * 2.0) for i in range(3)]
        clips, _videos = build_live_photo_clips(burst, config=_config())
        assert len(clips) == 1

    def test_a_lone_photograph_stays_a_photograph(self) -> None:
        from immich_memories.analysis.live_photo_pipeline import build_live_photo_clips

        clips, _videos = build_live_photo_clips([_live(1)], config=_config())
        assert clips == []


class TestNothingFallsBetweenTheTwo:
    """The photo pool is every image a clip is not showing. That is the whole rule.

    Partitioning by "is it a Live Photo" is what let a refused burst belong to
    neither pool; asking "is a clip showing it" cannot, because the answer comes
    from the clips that exist.
    """

    def _image(self, name: str, *, seconds: float = 0.0, live: bool = False):
        from immich_memories.api.models import AssetType
        from tests.conftest import make_asset

        asset = make_asset(name, file_created_at=NOON + timedelta(seconds=seconds), duration=None)
        asset.type = AssetType.IMAGE
        if live:
            asset.live_photo_video_id = f"v-{name}"
        return asset

    def test_a_live_photo_too_short_to_stitch_is_offered_as_a_photograph(self) -> None:
        from immich_memories.analysis.album_source import split_album_assets
        from immich_memories.config_loader import Config

        assets = [self._image("plain"), self._image("lone", seconds=60.0, live=True)]
        media = split_album_assets(assets, config=Config(), use_live_photos=True, use_photos=True)

        assert media.live_photo_clips == []
        assert [a.id for a in media.photos] == ["plain", "lone"]

    def test_a_burst_shown_as_motion_is_not_also_offered_as_photographs(self) -> None:
        """Otherwise the same moment ships twice, once each way."""
        from immich_memories.analysis.album_source import split_album_assets
        from immich_memories.config_loader import Config

        burst = [self._image(f"b{i}", seconds=i * 2.0, live=True) for i in range(3)]
        media = split_album_assets(
            [self._image("plain", seconds=600.0), *burst],
            config=Config(),
            use_live_photos=True,
            use_photos=True,
        )

        assert len(media.live_photo_clips) == 1
        assert [a.id for a in media.photos] == ["plain"]
