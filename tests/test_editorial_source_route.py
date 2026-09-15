"""The source route conserves scope and resolves only the final rendering material.

The live-manifest-to-render-target case waits for the slice that ports
``generate_clips._validated_render_directives`` and the directive-aware
``_prefetch_assets``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from immich_memories.analysis.editorial_source_route import (
    metadata_demand,
    project_source_rendering,
)
from immich_memories.analysis.motion_rendering import motion_renderings
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from tests.conftest import make_asset, make_clip


def photo(key, *, at=None, live=None):
    asset = make_asset(
        key, duration=None, file_created_at=at or datetime(2020, 5, 2, 9, tzinfo=UTC)
    )
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = live
    return asset


def demand(sources, *, requested=None, owner=(), **scope):
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0, **scope), owner),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )
    return prepared, metadata_demand(
        prepared, sources if requested is None else requested, photo_seconds=4
    )


def test_all_eligible_requested_sources_survive_without_metadata_ranking():
    ordinary = [photo(f"picture-{n}") for n in range(70)]
    context = photo("context-only")
    hidden = photo("hidden")
    hidden.is_archived = True
    owner = photo("owner-excluded")
    tiny = make_clip("short-but-contextual", duration=0.4)
    prepared, rows = demand(
        [*ordinary, context, hidden, owner, tiny],
        requested=[*ordinary, hidden, owner, tiny],
        owner=(owner.id,),
    )
    assert context.id in prepared.candidate_ids
    assert {row.clip.asset.id for row in rows} == {a.id for a in ordinary} | {tiny.asset.id}
    assert all(row.score == 0 and row.analyzed is False for row in rows)
    assert rows[-1].clip.duration_seconds == 0.4


def test_metadata_demand_rejects_missing_captured_source_and_keeps_measured_clip_metadata():
    clip = make_clip("video", duration=7)
    clip.rotation = 90
    clip.color_transfer = "smpte2084"
    prepared, rows = demand([clip])
    assert rows[0].clip.rotation == 90
    assert rows[0].clip.is_hdr
    assert rows[0].clip is not clip
    with pytest.raises(ValueError, match="omitted captured"):
        metadata_demand(prepared, [photo("absent")], photo_seconds=4)


def test_live_components_and_owner_excluded_sidecars_do_not_become_demand():
    first = photo("first", live="v1")
    second = photo("second", at=first.file_created_at + timedelta(seconds=1), live="v2")
    component = make_clip("v1")
    _, rows = demand([first, second, component], owner=(second.id,))
    assert [r.clip.asset.id for r in rows] == ["first"]
    with pytest.raises(ValueError, match="escaped demanded material"):
        project_source_rendering(
            [
                {
                    "asset_id": "first",
                    "kind": "live-motion",
                    "members": ["first", "second"],
                    "seconds": 4,
                }
            ],
            rows,
            config=Config(),
            include_live_photos=True,
        )


@pytest.mark.parametrize(
    "change,match",
    [
        ({"asset_id": "absent"}, "escaped"),
        ({"seconds": float("nan")}, "finite"),
        ({"seconds": True}, "numeric"),
        ({"seconds": 11}, "exceeds"),
        ({"start_time": 3, "end_time": 6}, "disagrees"),
        ({"kind": "live-motion", "members": ["video"]}, "media contract"),
        ({"kind": "made-up"}, "unknown"),
        ({"kind": "still"}, "exact source frame"),
    ],
)
def test_bad_native_rendering_fails_without_guessing(change, match):
    _, rows = demand([make_clip("video", duration=10)])
    carrier = {"asset_id": "video", "kind": "video", "seconds": 4} | change
    with pytest.raises(ValueError, match=match):
        project_source_rendering([carrier], rows, config=Config(), include_live_photos=True)


def test_declared_source_interval_and_video_still_frame_are_not_replaced_by_best_segment():
    _, rows = demand([make_clip("video", duration=10)])
    movie = project_source_rendering(
        [{"asset_id": "video", "kind": "video", "seconds": 4, "start_time": 2, "end_time": 6}],
        rows,
        config=Config(),
        include_live_photos=True,
    )
    assert (movie.plan.selections[0].start_time, movie.plan.selections[0].end_time) == (2, 6)
    still = project_source_rendering(
        [{"asset_id": "video", "kind": "still", "seconds": 4, "render_frame_seconds": 1.25}],
        rows,
        config=Config(),
        include_live_photos=True,
    )
    assert still.plan.selections[0].render_frame_seconds == 1.25
    with pytest.raises(ValueError, match="source"):
        project_source_rendering(
            [{"asset_id": "video", "kind": "still", "seconds": 4, "render_frame_seconds": 10}],
            rows,
            config=Config(),
            include_live_photos=True,
        )


def test_only_exact_native_centisecond_rounding_is_reconciled_and_reported():
    _, rows = demand([make_clip("fractional", duration=1.826)])
    carrier = {"asset_id": "fractional", "kind": "video", "seconds": 1.83, "raw_seconds": 1.83}
    projected = project_source_rendering([carrier], rows, config=Config(), include_live_photos=True)
    assert projected.plan.selections[0].end_time == 1.826
    assert projected.render_adjustments == (
        {
            "asset_id": "fractional",
            "reason": "native-duration-rounded-to-centiseconds",
            "native_interval": [0, 1.83],
            "source_interval": [0, 1.826],
        },
    )
    for invalid in [carrier | {"seconds": 1.84}, carrier | {"raw_seconds": 2}]:
        with pytest.raises(ValueError, match="exceeds"):
            project_source_rendering([invalid], rows, config=Config(), include_live_photos=True)


def test_negative_video_source_never_borrows_photograph_duration():
    """A negative duration is refused at source admission (#1013), before the
    route's own guard could see it; the guard stays for defense in depth."""
    prepared, rows = demand([make_clip("negative", duration=-1)])
    assert rows == ()
    assert prepared.excluded_ids == ("negative",)
    assert "metadata" in prepared.trace.story_of("negative").reason


def test_changed_live_companion_or_trim_fails_before_rendering():
    first = photo("first", live="v1")
    second = photo("second", at=first.file_created_at + timedelta(seconds=1), live="v2")
    _, rows = demand([first, second])
    manifest = motion_renderings([first, second], Config())["first"]
    carrier = {
        "asset_id": "first",
        "kind": "live-motion",
        "members": ["first", "second"],
        "seconds": 4,
        "video_ids": list(manifest.video_ids),
        "trim_points": [list(p) for p in manifest.trim_points],
    }
    for change in [{"video_ids": ["v1", "changed"]}, {"trim_points": [[0, 1], [0, 2]]}]:
        with pytest.raises(ValueError, match="manifest changed"):
            project_source_rendering(
                [carrier | change], rows, config=Config(), include_live_photos=True
            )
