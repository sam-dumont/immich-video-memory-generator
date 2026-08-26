"""The editorial flow starts with every source-eligible asset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_contracts import SourceEvidence
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
    assert prepared.excluded_ids == ("too-early", "audio")
    assert prepared.candidates[0].shippable_duration == 10.0
    assert prepared.trace.as_dict()["editorial_passes"][0] == {
        "name": "source-eligibility",
        "input_ids": ["too-early", "audio", "in-window"],
        "kept_ids": ["in-window"],
        "rejected": [
            {"asset_id": "too-early", "reason": "outside date scope"},
            {"asset_id": "audio", "reason": "unsupported media"},
        ],
        "unresolved": [],
        "duration_before": 30.0,
        "duration_after": 10.0,
        "provenance": {
            "pass_name": "source-eligibility",
            "pass_version": "1",
            "schema_version": "1",
            "model_identity": "",
            "input_ids": ["too-early", "audio", "in-window"],
            "sheet_hashes": [],
            "request_key": "source-eligibility",
            "cache_hit": False,
        },
        "conservation": {
            "valid": True,
            "missing_ids": [],
            "duplicate_ids": [],
            "unexpected_ids": [],
        },
        "request_traces": [],
    }


def test_library_scope_rejects_an_over_returned_asset_and_records_why() -> None:
    """The source boundary is enforced even when acquisition over-returns another library's asset."""
    scoped = make_asset("in-library")
    other_library = make_asset("elsewhere")
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope(library_ids=("family-library",))),
        EditorialDependencies(
            source_fetcher=lambda _scope: (scoped, other_library),
            library_membership=lambda asset, _library_ids: asset.id == "in-library",
        ),
    )

    assert prepared.candidate_ids == ("in-library",)
    assert prepared.excluded_ids == ("elsewhere",)
    source_trace = prepared.trace.as_dict()["editorial_passes"][0]
    assert source_trace["rejected"] == [
        {"asset_id": "elsewhere", "reason": "outside library scope"}
    ]
    story = prepared.trace.story_of("elsewhere")
    assert story.dropped_at == "source-eligibility"
    assert story.reason == "outside library scope"


def test_interleaved_place_threads_preserve_every_group_member() -> None:
    """Place threads may interleave chronologically without being mistaken for missing assets."""
    noon = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    circuit = make_asset("circuit", file_created_at=noon)
    home = make_asset("home", file_created_at=noon + timedelta(minutes=3))
    circuit2 = make_asset("circuit2", file_created_at=noon + timedelta(minutes=6))
    for asset, location in (
        (circuit, (50.437, 5.971)),
        (home, (50.878, 4.326)),
        (circuit2, (50.437, 5.971)),
    ):
        assert asset.exif_info is not None
        asset.exif_info.latitude, asset.exif_info.longitude = location

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (circuit, home, circuit2)),
    )

    assert tuple(group.candidate_ids for group in prepared.moment_groups) == (
        ("circuit", "circuit2"),
        ("home",),
    )


def test_duplicate_asset_representations_prefer_the_enriched_clip() -> None:
    """A Live Photo's still and motion representation remain one editorial candidate."""
    still = make_asset("live-photo", duration="0:00:00.500")
    still.type = AssetType.IMAGE
    still.live_photo_video_id = "live-photo-motion"
    clip = VideoClipInfo(
        asset=still,
        duration_seconds=1.5,
        width=1920,
        height=1080,
        live_burst_still_ids=["live-photo", "burst-neighbour"],
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (still, clip)),
    )

    assert prepared.candidate_ids == ("live-photo",)
    candidate = prepared.candidates[0]
    assert candidate.media_kind == "live_photo"
    assert candidate.shippable_duration == 1.5
    assert "burst-members:2" in candidate.grounded_annotations


def test_conflicting_duplicate_asset_representations_are_rejected() -> None:
    """Two different records claiming one ID cannot be coalesced silently."""
    noon = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    first = make_asset("duplicated", file_created_at=noon)
    conflicting = make_asset(
        "duplicated",
        file_created_at=noon,
        original_file_name="different-source.mov",
    )

    from pytest import raises

    with raises(ValueError, match="conflicting source representations for asset duplicated"):
        prepare_editorial_source(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(source_fetcher=lambda _scope: (first, conflicting)),
        )


def test_precomputed_evidence_and_clip_analysis_survive_source_preparation() -> None:
    """Already-available measurements become annotations without triggering new analysis."""
    clip = VideoClipInfo(
        asset=make_asset("analysed"),
        duration_seconds=2.0,
        width=1920,
        height=1080,
        llm_quality=0.875,
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (clip,),
            source_evidence=lambda _source: SourceEvidence(
                blur=0.125,
                exposure=0.75,
                similarity="cluster-7",
            ),
        ),
    )

    assert prepared.candidates[0].grounded_annotations == (
        "resolution:1920x1080",
        "duration:2.000s",
        "motion:available",
        "analysis-quality:0.875",
        "blur:0.125",
        "exposure:0.75",
        "similarity:cluster-7",
    )
