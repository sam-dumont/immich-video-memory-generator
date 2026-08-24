"""A Live Photo is a photograph. The clip has to earn the swap.

A live photo has two renderings: the still, and the motion stitched from its
burst. Today every cluster becomes a clip and the still is suppressed as
"already shown as motion" — so the clip wins by being a clip, and a lone
1.5-second wobble displaces a photograph that would have shipped.

The owner's rule: the video is worth it only when enough of the burst stitches
together to beat a still — three to four seconds. Below that the still wins,
and it always works.

Measured on four real days: 85-100% of live photos sit in a burst long enough
to qualify. The ones that do not are singletons, which is exactly right.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from immich_memories.analysis.live_photo_pipeline import build_live_photo_clips

NOON = datetime(2024, 6, 4, 12, 0, tzinfo=UTC)


def _live(index: int, *, seconds: float = 0.0, favorite: bool = False):
    from tests.conftest import make_asset

    asset = make_asset(
        f"still-{index}",
        is_favorite=favorite,
        file_created_at=NOON + timedelta(seconds=seconds),
        duration=None,
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


class TestAClipHasToBeatAStill:
    def test_a_lone_live_photo_stays_a_photograph(self) -> None:
        """A lone Live Photo stitches to exactly 3.0s — the raw clip, nothing
        merged — while the smallest genuine stitch is 4.0s. The threshold sits
        between them, so a burst of one never qualifies."""
        clips, _videos, _stay = build_live_photo_clips([_live(1)], config=_config())
        assert clips == []

    def test_a_burst_long_enough_to_stitch_becomes_a_clip(self) -> None:
        stitchable = [_live(i, seconds=i * 2.0) for i in range(4)]
        clips, _videos, _stay = build_live_photo_clips(stitchable, config=_config())
        assert len(clips) == 1
        assert clips[0].duration_seconds >= 3.0

    def test_the_stills_of_a_refused_burst_are_not_claimed_by_any_clip(self) -> None:
        """Nothing suppresses a photograph that no clip says it is showing."""
        clips, _videos, _stay = build_live_photo_clips([_live(1)], config=_config())
        claimed = {sid for c in clips for sid in (c.live_burst_still_ids or ())}
        assert "still-1" not in claimed

    def test_the_threshold_is_the_stitched_length_not_the_count(self) -> None:
        """Two long components can beat four tiny ones; duration is the rule."""
        clips, _videos, _stay = build_live_photo_clips(
            [_live(i, seconds=i * 2.0) for i in range(4)], config=_config(minimum=99.0)
        )
        assert clips == []


class TestARefusedBurstIsStillAPhotograph:
    """Refusing the clip must not lose the photographs.

    Every live still goes to clip-building and is excluded from the plain-still
    pool, so a burst refused without being handed back vanishes from selection
    entirely — not shown as motion, not shown as a photograph. That is a moment
    lost rather than a rendering chosen, and it is the failure this whole issue
    is about.
    """

    def test_the_stills_of_a_refused_burst_come_back(self) -> None:
        _clips, _videos, stays = build_live_photo_clips([_live(1)], config=_config())
        assert [a.id for a in stays] == ["still-1"]

    def test_a_burst_that_earns_its_clip_does_not_come_back_as_photographs(self) -> None:
        """Otherwise the same moment ships twice, once each way."""
        stitchable = [_live(i, seconds=i * 2.0) for i in range(4)]
        clips, _videos, stays = build_live_photo_clips(stitchable, config=_config())
        assert clips and stays == []

    def test_every_still_ends_up_somewhere(self) -> None:
        """The invariant: shown as motion, or shown as a photograph, never neither."""
        mixed = [_live(i, seconds=i * 2.0) for i in range(3)] + [_live(9, seconds=600.0)]
        clips, _videos, stays = build_live_photo_clips(mixed, config=_config())
        accounted = {sid for c in clips for sid in (c.live_burst_still_ids or ())}
        accounted |= {a.id for a in stays}
        assert accounted == {a.id for a in mixed}
