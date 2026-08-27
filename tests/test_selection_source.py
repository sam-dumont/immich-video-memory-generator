"""The editorial flow starts with every source-eligible asset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from immich_memories.analysis.editorial_contracts import (
    LivePhotoRenderingFamily,
    SourceEvidence,
    live_photo_rendering_family_id,
)
from immich_memories.analysis.selection_source import (
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
    assert prepared.trace.story_of("pregnancy-test").first_pass is None
    assert [item.name for item in prepared.trace.editorial_passes] == ["source-eligibility"]


def test_source_observations_survive_without_running_legacy_selectors(monkeypatch) -> None:
    """Eligible source facts survive without invoking the old ranking pipeline."""
    from immich_memories.analysis import source_filter, subject_policy
    from immich_memories.photos import burst_dedup, moment_suppression

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy selection must not run before pass 0")

    monkeypatch.setattr(source_filter, "not_shot_here", forbidden)
    monkeypatch.setattr(subject_policy, "filter_candidates_by_subject", forbidden)
    monkeypatch.setattr(burst_dedup, "drop_burst_duplicates", forbidden)
    monkeypatch.setattr(moment_suppression, "suppress_photos_covered_by_motion", forbidden)
    clip = VideoClipInfo(
        asset=make_asset("evidence", exif_make=None, exif_model=None, duration="0:00:01.250"),
        duration_seconds=1.25,
        width=1920,
        height=1080,
        live_burst_still_ids=["evidence", "other-burst-member"],
        llm_category="screen",
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (clip,)),
    )

    assert prepared.candidate_ids == ("evidence",)
    assert prepared.candidates[0].grounded_annotations == (
        "resolution:1920x1080",
        "duration:1.250s",
        "motion:available",
        "burst-members:2",
        "subject:screen",
    )


def test_low_resolution_video_without_camera_metadata_is_source_excluded() -> None:
    """A renamed messaging re-encode is settled before any editorial pass sees it."""
    suspect = VideoClipInfo(
        asset=make_asset(
            "suspect",
            original_file_name="renamed-export.mp4",
            exif_make=None,
            exif_model=None,
        ),
        duration_seconds=1.25,
        width=352,
        height=640,
    )
    old_camera_original = VideoClipInfo(
        asset=make_asset("old-camera", exif_make="Nokia", exif_model="N95"),
        duration_seconds=2.0,
        width=640,
        height=480,
    )
    full_resolution_without_exif = VideoClipInfo(
        asset=make_asset("full-resolution", exif_make=None, exif_model=None),
        duration_seconds=2.0,
        width=1920,
        height=1080,
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                suspect,
                old_camera_original,
                full_resolution_without_exif,
            )
        ),
    )

    assert set(prepared.candidate_ids) == {"old-camera", "full-resolution"}
    assert prepared.excluded_ids == ("suspect",)
    story = prepared.trace.story_of("suspect")
    assert story.dropped_at == "source-eligibility"
    assert story.reason == "low-resolution source without camera metadata"


def test_high_resolution_still_without_camera_metadata_is_source_eligible() -> None:
    """A published high-res photo can lose camera EXIF without becoming a re-encode."""
    official_photo = make_asset(
        "official-photo",
        original_file_name="official-photo.jpg",
        exif_make=None,
        exif_model=None,
    ).model_copy(update={"type": AssetType.IMAGE, "width": 4000, "height": 2666})

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(
            scope=SourceScope(stills_need_a_camera=True, min_source_short_side=1080)
        ),
        EditorialDependencies(source_fetcher=lambda _scope: (official_photo,)),
    )

    assert prepared.candidate_ids == ("official-photo",)
    assert prepared.excluded_ids == ()


def test_favourite_overrides_low_resolution_source_inference() -> None:
    """A star is direct owner evidence even when the file resembles a re-encode."""
    favourite = make_asset(
        "favourite",
        original_file_name="renamed.jpg",
        exif_make=None,
        exif_model=None,
        is_favorite=True,
    ).model_copy(update={"type": AssetType.IMAGE, "width": 640, "height": 480})

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope(min_source_short_side=1080)),
        EditorialDependencies(source_fetcher=lambda _scope: (favourite,)),
    )

    assert prepared.candidate_ids == ("favourite",)
    assert prepared.excluded_ids == ()


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


@pytest.mark.parametrize("richer_first", (False, True))
def test_duplicate_source_favourite_is_or_merged_without_losing_motion_evidence(
    tmp_path: Path,
    richer_first: bool,
) -> None:
    """Arrival order cannot trade a star for the richer representation of the same asset."""
    favourite_snapshot = make_asset("live-photo", is_favorite=True)
    favourite_snapshot.type = AssetType.IMAGE
    favourite_snapshot.live_photo_video_id = "live-photo-motion"
    clip_asset = favourite_snapshot.model_copy(update={"is_favorite": False})
    local_path = tmp_path / "live-photo.mov"
    local_path.write_bytes(b"generated motion placeholder")
    richer = VideoClipInfo(
        asset=clip_asset,
        local_path=str(local_path),
        duration_seconds=3.5,
        width=1920,
        height=1080,
        live_burst_still_ids=["live-photo", "neighbour"],
        live_burst_video_ids=["live-photo-motion", "neighbour-motion"],
        live_burst_trim_points=[(0.0, 1.0), (0.5, 1.5)],
        live_burst_shutter_timestamps=[
            favourite_snapshot.file_created_at.timestamp(),
            favourite_snapshot.file_created_at.timestamp() + 1.0,
        ],
    )
    sources = (richer, favourite_snapshot) if richer_first else (favourite_snapshot, richer)

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: sources),
    )

    assert prepared.candidate_ids == ("live-photo",)
    assert prepared.candidates[0].favourite is True
    assert prepared.candidates[0].shippable_duration == 3.5
    assert "burst-members:2" in prepared.candidates[0].grounded_annotations
    assert prepared.visual_sources[0].motion_path == local_path


def test_enriched_stitch_identity_is_propagated_symmetrically_to_admitted_members() -> None:
    """A later pass can see the exact source-declared stitch without choosing its carrier."""
    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    favourite = make_asset("favourite", file_created_at=when, is_favorite=True)
    favourite.type = AssetType.IMAGE
    favourite.live_photo_video_id = "favourite-motion"
    sibling = make_asset("sibling", file_created_at=when + timedelta(seconds=1))
    sibling.type = AssetType.IMAGE
    sibling.live_photo_video_id = "sibling-motion"
    enriched = VideoClipInfo(
        asset=favourite,
        duration_seconds=4.0,
        width=1920,
        height=1080,
        live_burst_still_ids=["sibling", "favourite"],
        live_burst_video_ids=["sibling-motion", "favourite-motion"],
        live_burst_trim_points=[(0.5, 1.5), (0.0, 1.0)],
        live_burst_shutter_timestamps=[
            sibling.file_created_at.timestamp(),
            favourite.file_created_at.timestamp(),
        ],
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (sibling, enriched)),
    )

    assert prepared.candidate_ids == ("favourite", "sibling")
    assert tuple(candidate.live_photo_stitch_member_ids for candidate in prepared.candidates) == (
        ("favourite", "sibling"),
        ("favourite", "sibling"),
    )
    family_ids = tuple(candidate.rendering_family_id for candidate in prepared.candidates)
    assert len(set(family_ids)) == 1
    assert family_ids[0] is not None
    assert family_ids[0].startswith("live-photo-rendering-family-v1-")
    assert len(prepared.rendering_families) == 1
    family = prepared.rendering_families[0]
    assert family.family_id == family_ids[0]
    assert family.still_ids == ("favourite", "sibling")
    assert family.video_ids == ("favourite-motion", "sibling-motion")
    assert family.trim_points == ((0.0, 1.0), (0.5, 1.5))
    assert family.shutter_timestamps == (
        favourite.file_created_at.timestamp(),
        sibling.file_created_at.timestamp(),
    )
    assert family.motion_duration_seconds is None
    assert family.minimum_motion_seconds is None


def test_stitch_sidecar_cannot_reintroduce_an_owner_excluded_member() -> None:
    """Source scope wins over a richer wrapper's broader stitch declaration."""
    admitted = make_asset("admitted")
    admitted.type = AssetType.IMAGE
    admitted.live_photo_video_id = "admitted-motion"
    excluded = make_asset("excluded")
    excluded.type = AssetType.IMAGE
    excluded.live_photo_video_id = "excluded-motion"
    enriched = VideoClipInfo(
        asset=admitted,
        duration_seconds=4.0,
        width=1920,
        height=1080,
        live_burst_still_ids=["admitted", "excluded"],
        live_burst_video_ids=["admitted-motion", "excluded-motion"],
        live_burst_trim_points=[(0.0, 1.0), (0.25, 1.25)],
        live_burst_shutter_timestamps=[
            admitted.file_created_at.timestamp(),
            excluded.file_created_at.timestamp(),
        ],
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(
            scope=SourceScope(),
            owner_excluded_asset_ids=("excluded",),
        ),
        EditorialDependencies(source_fetcher=lambda _scope: (excluded, enriched)),
    )

    assert prepared.candidate_ids == ("admitted",)
    assert prepared.excluded_ids == ("excluded",)
    assert prepared.candidates[0].live_photo_stitch_member_ids == ("admitted",)
    assert prepared.candidates[0].rendering_family_id == prepared.rendering_families[0].family_id
    assert prepared.rendering_families[0].still_ids == ("admitted",)
    assert prepared.rendering_families[0].video_ids == ("admitted-motion",)
    assert "excluded" not in " ".join(prepared.candidates[0].grounded_annotations)


def test_rendering_family_manifest_is_canonical_across_source_and_entry_order() -> None:
    """The family hash follows aligned chronology, never independent array sorting."""
    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    first = make_asset("first", file_created_at=when)
    first.live_photo_video_id = "video-first"
    second = make_asset("second", file_created_at=when + timedelta(seconds=1))
    second.live_photo_video_id = "video-second"

    def prepare(reverse: bool):
        aligned = (
            ["second", "first"] if reverse else ["first", "second"],
            ["video-second", "video-first"] if reverse else ["video-first", "video-second"],
            [(0.5, 1.5), (0.0, 1.0)] if reverse else [(0.0, 1.0), (0.5, 1.5)],
            [second.file_created_at.timestamp(), first.file_created_at.timestamp()]
            if reverse
            else [first.file_created_at.timestamp(), second.file_created_at.timestamp()],
        )
        enriched = VideoClipInfo(
            asset=second,
            duration_seconds=4.0,
            live_burst_still_ids=aligned[0],
            live_burst_video_ids=aligned[1],
            live_burst_trim_points=aligned[2],
            live_burst_shutter_timestamps=aligned[3],
        )
        sources = (enriched, first) if reverse else (first, enriched)
        return prepare_editorial_source(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(source_fetcher=lambda _scope: sources),
        )

    chronological = prepare(False)
    permuted = prepare(True)

    assert chronological.rendering_families == permuted.rendering_families
    family = chronological.rendering_families[0]
    assert family.still_ids == ("first", "second")
    assert family.video_ids == ("video-first", "video-second")
    assert family.trim_points == ((0.0, 1.0), (0.5, 1.5))


def test_incomplete_rendering_manifest_fails_visibly_but_keeps_stills() -> None:
    """Incomplete source evidence cannot invent a later motion option."""
    still = make_asset("still")
    enriched = VideoClipInfo(
        asset=still,
        duration_seconds=2.0,
        live_burst_still_ids=["still"],
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (enriched,)),
    )

    assert prepared.candidate_ids == ("still",)
    assert prepared.rendering_families == ()
    assert prepared.candidates[0].rendering_family_id is None
    assert prepared.source_warnings == (
        "!! invalid Live Photo rendering family for still: incomplete aligned manifest",
    )
    assert prepared.trace.warnings == list(prepared.source_warnings)


def test_conflicting_rendering_manifests_fail_visibly_without_an_invented_union() -> None:
    """Two overlapping but different source manifests invalidate both families."""
    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    a = make_asset("a", file_created_at=when)
    b = make_asset("b", file_created_at=when + timedelta(seconds=1))
    c = make_asset("c", file_created_at=when + timedelta(seconds=2))
    first = VideoClipInfo(
        asset=a,
        live_burst_still_ids=["a", "b"],
        live_burst_video_ids=["va", "vb"],
        live_burst_trim_points=[(0.0, 1.0), (0.0, 1.0)],
        live_burst_shutter_timestamps=[
            a.file_created_at.timestamp(),
            b.file_created_at.timestamp(),
        ],
    )
    second = VideoClipInfo(
        asset=c,
        live_burst_still_ids=["b", "c"],
        live_burst_video_ids=["vb-other", "vc"],
        live_burst_trim_points=[(0.0, 1.0), (0.0, 1.0)],
        live_burst_shutter_timestamps=[
            b.file_created_at.timestamp(),
            c.file_created_at.timestamp(),
        ],
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (first, b, second)),
    )

    assert prepared.candidate_ids == ("a", "b", "c")
    assert prepared.rendering_families == ()
    assert all(candidate.rendering_family_id is None for candidate in prepared.candidates)
    assert prepared.source_warnings == (
        "!! conflicting Live Photo rendering family for admitted asset b",
    )


def test_duplicate_enriched_source_conflict_fails_visibly_as_still() -> None:
    """Coalescing duplicate snapshots cannot hide contradictory render evidence."""
    still = make_asset("still")
    still.live_photo_video_id = "motion"
    first = VideoClipInfo(
        asset=still,
        duration_seconds=2.0,
        live_burst_still_ids=["still"],
        live_burst_video_ids=["motion"],
        live_burst_trim_points=[(0.0, 1.0)],
        live_burst_shutter_timestamps=[still.file_created_at.timestamp()],
    )
    contradictory = first.model_copy(
        update={
            "live_burst_video_ids": ["different-motion"],
            "live_burst_trim_points": [(0.25, 1.25)],
        }
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (first, contradictory)),
    )

    assert prepared.candidate_ids == ("still",)
    assert prepared.rendering_families == ()
    assert prepared.candidates[0].rendering_family_id is None
    assert prepared.source_warnings == (
        "!! conflicting Live Photo rendering manifests for duplicate asset still",
    )


def test_rendering_family_cannot_cross_editorial_moment_groups() -> None:
    """A source declaration spanning two moments fails rather than creating two outputs."""
    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    early = make_asset("early", file_created_at=when)
    late = make_asset("late", file_created_at=when + timedelta(hours=3))
    enriched = VideoClipInfo(
        asset=early,
        live_burst_still_ids=["early", "late"],
        live_burst_video_ids=["ve", "vl"],
        live_burst_trim_points=[(0.0, 1.0), (0.0, 1.0)],
        live_burst_shutter_timestamps=[
            early.file_created_at.timestamp(),
            late.file_created_at.timestamp(),
        ],
    )

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (enriched, late)),
    )

    assert prepared.rendering_families == ()
    assert all(candidate.rendering_family_id is None for candidate in prepared.candidates)
    assert prepared.source_warnings[0].startswith(
        "!! Live Photo rendering family crosses moment groups: live-photo-rendering-family-v1-"
    )


def test_rendering_family_contract_rejects_unsafe_or_misaligned_manifests() -> None:
    """The immutable bridge accepts only a versioned, safe, chronological manifest."""
    still_ids = ("a",)
    video_ids = ("v",)
    trim_points = ((0.0, 1.0),)
    timestamps = (1.0,)
    family_id = live_photo_rendering_family_id(
        still_ids,
        video_ids,
        trim_points,
        timestamps,
        motion_duration_seconds=None,
        minimum_motion_seconds=None,
    )

    valid = LivePhotoRenderingFamily(
        family_id=family_id,
        still_ids=still_ids,
        video_ids=video_ids,
        trim_points=trim_points,
        shutter_timestamps=timestamps,
    )
    assert valid.family_id == family_id
    with pytest.raises(ValueError, match="safe aligned timing"):
        LivePhotoRenderingFamily(
            family_id=family_id,
            still_ids=still_ids,
            video_ids=video_ids,
            trim_points=((0.0, float("inf")),),
            shutter_timestamps=timestamps,
        )
    with pytest.raises(ValueError, match="duration and minimum"):
        LivePhotoRenderingFamily(
            family_id=family_id,
            still_ids=still_ids,
            video_ids=video_ids,
            trim_points=trim_points,
            shutter_timestamps=timestamps,
            motion_duration_seconds=2.0,
        )


def test_failed_preview_does_not_mark_a_video_unavailable_when_motion_frames_work(
    tmp_path: Path,
) -> None:
    """Usable motion pixels outrank a failed still-preview provider."""
    from immich_memories.analysis.visual_atlas import build_visual_atlas

    asset = make_asset("motion", duration="0:00:03.000")
    local_path = tmp_path / "motion.mp4"
    local_path.write_bytes(b"generated motion placeholder")
    clip = VideoClipInfo(
        asset=asset,
        duration_seconds=3.0,
        width=1920,
        height=1080,
        local_path=str(local_path),
    )
    frames = []
    for index, colour in enumerate(("red", "green", "blue")):
        path = tmp_path / f"frame-{index}.jpg"
        Image.new("RGB", (48, 32), colour).save(path, "JPEG")
        frames.append(path)

    def failed_preview(_asset):
        raise RuntimeError("generated preview failure")

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (clip,),
            preview_jpeg=failed_preview,
        ),
    )
    # WHY: duration probing and frame decoding are the two external FFmpeg boundaries.
    with (
        patch("immich_memories.analysis.visual_atlas.probe_duration", return_value=3.0),
        patch(
            "immich_memories.analysis.visual_atlas.sample_segment_frames",
            return_value=tuple(frames),
        ),
    ):
        atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")

    tile = atlas.tile_for("motion")
    assert prepared.candidate_ids == ("motion",)
    assert tile.kind == "filmstrip"
    assert tile.unavailable_reason is None


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


def test_precomputed_evidence_survives_raw_photo_source_preparation() -> None:
    """Already-computed visual evidence remains available when there is no clip wrapper."""
    photo = make_asset("analysed-photo")
    photo.type = AssetType.IMAGE
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (photo,),
            source_evidence=lambda _source: SourceEvidence(
                blur=0.25,
                exposure=0.5,
                similarity="photo-cluster",
            ),
        ),
    )

    assert prepared.candidates[0].grounded_annotations == (
        "duration:10.000s",
        "blur:0.25",
        "exposure:0.5",
        "similarity:photo-cluster",
    )


def test_a_live_photo_component_is_not_a_candidate_beside_its_still() -> None:
    """Immich lists the motion half as its own asset; it is the photograph, not a second visual."""
    shutter = datetime(2023, 6, 16, 6, 52, 28, 616000, tzinfo=UTC)
    still = make_asset("still", original_file_name="IMG_0955.JPG", file_created_at=shutter)
    still.type = AssetType.IMAGE
    still.live_photo_video_id = "motion"
    # Measured on the real library: the component is filed a beat BEFORE its own
    # still, so it reaches the pool first and cannot be resolved by arrival order.
    motion = make_asset(
        "motion",
        original_file_name="IMG_0955.MOV",
        file_created_at=shutter - timedelta(seconds=1, milliseconds=616),
    )
    motion.type = AssetType.VIDEO

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (motion, still)),
    )

    assert prepared.candidate_ids == ("still",)
    assert prepared.excluded_ids == ("motion",)


def test_an_owner_excluded_still_still_claims_its_own_component() -> None:
    """Dropping the photograph must not readmit the same instant as footage."""
    shutter = datetime(2023, 6, 16, 6, 52, 28, tzinfo=UTC)
    still = make_asset("still", file_created_at=shutter)
    still.type = AssetType.IMAGE
    still.live_photo_video_id = "motion"
    motion = make_asset("motion", file_created_at=shutter - timedelta(seconds=1))
    motion.type = AssetType.VIDEO

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(
            scope=SourceScope(),
            owner_excluded_asset_ids=("still",),
        ),
        EditorialDependencies(source_fetcher=lambda _scope: (motion, still)),
    )

    assert prepared.candidate_ids == ()
    assert prepared.excluded_ids == ("motion", "still")


def test_a_video_no_still_claims_is_footage_and_stays_a_candidate() -> None:
    """Only a claimed component is a photograph's half; everything else was filmed."""
    shutter = datetime(2023, 6, 16, 7, 4, 10, tzinfo=UTC)
    still = make_asset("still", file_created_at=shutter)
    still.type = AssetType.IMAGE
    still.live_photo_video_id = "claimed"
    claimed = make_asset("claimed", file_created_at=shutter - timedelta(seconds=1))
    claimed.type = AssetType.VIDEO
    filmed = make_asset("filmed", file_created_at=shutter + timedelta(minutes=5))
    filmed.type = AssetType.VIDEO

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (claimed, still, filmed)),
    )

    assert prepared.candidate_ids == ("still", "filmed")
    assert prepared.excluded_ids == ("claimed",)


def test_material_that_never_came_from_this_camera_is_not_editorial_source() -> None:
    """Low-res forwarded graphics and named messaging exports are not source.

    Missing camera metadata alone is insufficient: published and official
    photographs often lose it. The renamed forwarded case is settled by the
    measured conjunction of low resolution and missing camera metadata.
    """
    when = datetime(2023, 8, 5, 12, tzinfo=UTC)
    theirs = make_asset("theirs", original_file_name="IMG_0776.HEIC", file_created_at=when)
    theirs.type = AssetType.IMAGE
    forwarded = make_asset(
        "forwarded",
        original_file_name="8cfb4458-b9a5-4a2c-afff-f594d861fdb1.jpg",
        exif_make=None,
        exif_model=None,
        file_created_at=when,
    )
    forwarded = forwarded.model_copy(update={"type": AssetType.IMAGE, "width": 640, "height": 480})
    messaged = make_asset(
        "messaged",
        original_file_name="img-20230805-wa0007.jpg",
        file_created_at=when,
    )
    messaged.type = AssetType.IMAGE
    starred = make_asset(
        "starred",
        original_file_name="c810423e-51b7-4a56-8e5b-977cee49338a.jpg",
        exif_make=None,
        exif_model=None,
        is_favorite=True,
        file_created_at=when,
    )
    starred.type = AssetType.IMAGE

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(
            scope=SourceScope(
                excluded_filename_patterns=("img-*-wa[0-9][0-9][0-9][0-9]*",),
                stills_need_a_camera=True,
            )
        ),
        EditorialDependencies(source_fetcher=lambda _s: (theirs, forwarded, messaged, starred)),
    )

    # The star settles it here as it settles every other hard gate.
    assert set(prepared.candidate_ids) == {"theirs", "starred"}
    assert set(prepared.excluded_ids) == {"forwarded", "messaged"}


def test_an_asset_off_the_timeline_never_reaches_a_pass_even_starred() -> None:
    """Visibility is a privacy gate, so the star does not open it.

    Immich carries four visibilities: timeline, archive, hidden and locked.
    Only `timeline` belongs in a memory. `locked` is the folder people put
    intimate pictures in, and the server refuses it to an API key outright --
    `visibility=locked` answers 401 "Elevated permission is required" -- but
    relying on the server to keep saying no is not a gate, it is a bet on a
    remote default we do not control.

    `hidden` is the one that is live today: the default metadata search returns
    it unasked, 3,303 assets on a real library, every one of them the video
    component of a Live Photo that Immich hides so the timeline does not show
    the shot twice.

    Every other source rule here answers "did anyone want this?", and a star
    answers it directly, so `not_shot_here` lets a favourite through. This rule
    answers "may we look at this at all?", which a star cannot speak to -- a
    picture in the locked folder is likely to be starred AND likely to score
    well, which is precisely the combination that must not ship.
    """
    for visibility in ("archive", "hidden", "locked"):
        starred = make_asset(f"off-timeline-{visibility}", is_favorite=True).model_copy(
            update={"visibility": visibility}
        )
        on_timeline = make_asset("on-timeline").model_copy(update={"visibility": "timeline"})

        prepared = prepare_editorial_source(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(source_fetcher=lambda _scope, pair=(starred, on_timeline): pair),
        )

        assert prepared.candidate_ids == ("on-timeline",)
        assert prepared.excluded_ids == (f"off-timeline-{visibility}",)
