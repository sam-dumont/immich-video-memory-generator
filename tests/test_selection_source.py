"""The editorial flow starts with every source-eligible asset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.selection_flow import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    build_episode_groups,
    build_moment_groups,
    prepare_editorial_source,
)
from immich_memories.api.models import AssetType, VideoClipInfo
from tests.conftest import make_asset


def test_only_source_scope_and_owner_exclusions_apply_before_pass_zero() -> None:
    """Editorial evidence survives until a later pass can judge its contribution."""
    pregnancy_test = make_asset("pregnancy-test", original_file_name="IMG_0001.jpg")
    pregnancy_test.type = AssetType.IMAGE
    screenshot = make_asset("screenshot", original_file_name="Screenshot 2026-08-25.png")
    screenshot.type = AssetType.IMAGE
    short_clip = VideoClipInfo(
        asset=make_asset("short-clip", duration="0:00:01.000"),
        duration_seconds=1.0,
        width=640,
        height=480,
    )
    owner_excluded = make_asset("owner-excluded")
    request = EditorialSelectionRequest(
        scope=SourceScope(),
        owner_excluded_asset_ids=("owner-excluded",),
    )
    dependencies = EditorialDependencies(
        source_fetcher=lambda _scope: (pregnancy_test, screenshot, short_clip, owner_excluded)
    )

    prepared = prepare_editorial_source(request, dependencies)

    assert prepared.candidate_ids == ("pregnancy-test", "screenshot", "short-clip")
    assert prepared.excluded_ids == ("owner-excluded",)
    assert prepared.trace.story_of("pregnancy-test").first_pass == "pass-0"  # noqa: S105


def test_source_observations_survive_without_running_legacy_selectors(monkeypatch) -> None:
    """Observed quality and subject signals inform later passes; they never cull source material."""
    from immich_memories.analysis import source_filter, source_quality, subject_policy
    from immich_memories.photos import burst_dedup, moment_suppression

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy selection must not run before pass 0")

    monkeypatch.setattr(source_filter, "not_shot_here", forbidden)
    monkeypatch.setattr(source_quality, "is_usable_source", forbidden)
    monkeypatch.setattr(subject_policy, "filter_candidates_by_subject", forbidden)
    monkeypatch.setattr(burst_dedup, "drop_burst_duplicates", forbidden)
    monkeypatch.setattr(moment_suppression, "suppress_photos_covered_by_motion", forbidden)
    clip = VideoClipInfo(
        asset=make_asset("evidence", exif_make=None, exif_model=None, duration="0:00:01.250"),
        duration_seconds=1.25,
        width=640,
        height=480,
        live_burst_still_ids=["evidence", "other-burst-member"],
        llm_category="screen",
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (clip,)),
    )

    assert prepared.candidate_ids == ("evidence",)
    assert prepared.candidates[0].grounded_annotations == (
        "resolution:640x480",
        "reencode-suspected",
        "duration:1.250s",
        "motion:available",
        "burst-members:2",
        "subject:screen",
    )


def test_groups_conserve_candidates_in_canonical_order_with_stable_ids() -> None:
    """Episode and moment walls use time/place grouping without electing a winner."""
    noon = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    assets = (
        make_asset("d", file_created_at=noon + timedelta(hours=3)),
        make_asset("b", file_created_at=noon),
        make_asset("c", file_created_at=noon + timedelta(minutes=5)),
        make_asset("a", file_created_at=noon),
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: assets),
    )

    episodes = build_episode_groups(prepared.candidates)
    moments = build_moment_groups(tuple(reversed(prepared.candidates)))

    assert prepared.candidate_ids == ("a", "b", "c", "d")
    assert tuple(group.candidate_ids for group in episodes) == (("a", "b", "c"), ("d",))
    assert tuple(group.candidate_ids for group in moments) == (("a", "b", "c"), ("d",))
    assert tuple(
        group.group_id for group in build_episode_groups(tuple(reversed(prepared.candidates)))
    ) == (tuple(group.group_id for group in episodes))
    assert tuple(asset_id for group in moments for asset_id in group.candidate_ids) == (
        "a",
        "b",
        "c",
        "d",
    )


def test_requested_dates_and_supported_media_define_the_source_scope() -> None:
    """Acquisition can over-return, but source scope still admits only supported in-range media."""
    day = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    too_early = make_asset("too-early", file_created_at=day - timedelta(seconds=1))
    in_window = make_asset("in-window", file_created_at=day)
    unsupported = make_asset("audio", file_created_at=day)
    unsupported.type = AssetType.AUDIO

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope(start_at=day, end_at=day)),
        EditorialDependencies(source_fetcher=lambda _scope: (too_early, unsupported, in_window)),
    )

    assert prepared.candidate_ids == ("in-window",)
    assert prepared.excluded_ids == ()
    assert prepared.candidates[0].shippable_duration == 10.0
