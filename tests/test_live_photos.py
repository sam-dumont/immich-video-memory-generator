"""Tests for Live Photos feature."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from immich_memories.api.models import Asset
from immich_memories.api.search_service import SearchService
from immich_memories.timeperiod import DateRange


def _make_asset(**overrides) -> dict:
    """Build a raw Immich API asset dict."""
    base = {
        "id": overrides.get("id", "asset-1"),
        "type": overrides.get("type", "IMAGE"),
        "fileCreatedAt": overrides.get("fileCreatedAt", "2024-07-15T10:30:00Z"),
        "fileModifiedAt": overrides.get("fileModifiedAt", "2024-07-15T10:30:00Z"),
        "updatedAt": overrides.get("updatedAt", "2024-07-15T10:30:00Z"),
        "isFavorite": overrides.get("isFavorite", False),
    }
    base.update(overrides)
    return base


class TestAssetLivePhotoField:
    def test_asset_has_live_photo_video_id(self):
        data = _make_asset(livePhotoVideoId="video-abc")
        asset = Asset(**data)
        assert asset.live_photo_video_id == "video-abc"

    def test_asset_live_photo_video_id_none_by_default(self):
        data = _make_asset()
        asset = Asset(**data)
        assert asset.live_photo_video_id is None

    def test_is_live_photo_property(self):
        with_lp = Asset(**_make_asset(livePhotoVideoId="vid-1"))
        without_lp = Asset(**_make_asset())
        assert with_lp.is_live_photo
        assert not without_lp.is_live_photo


class TestGetLivePhotosForDateRange:
    """Slice 2: Fetching live photos from Immich via SearchService."""

    def _make_search_service(self, pages: list[list[dict]]) -> SearchService:
        """Create a SearchService with a fake request fn returning paginated results."""
        call_count = 0

        async def fake_request(method: str, endpoint: str, **kwargs) -> dict:
            nonlocal call_count
            page_data = pages[call_count] if call_count < len(pages) else []
            call_count += 1
            has_next = call_count < len(pages)
            return {
                "assets": {
                    "total": sum(len(p) for p in pages),
                    "count": len(page_data),
                    "items": page_data,
                    "nextPage": str(call_count + 1) if has_next else None,
                },
            }

        return SearchService(fake_request)

    @pytest.mark.asyncio
    async def test_returns_only_live_photos(self):
        """Should return only IMAGE assets that have livePhotoVideoId set."""
        assets_data = [
            _make_asset(id="img-1", type="IMAGE", livePhotoVideoId="vid-1"),
            _make_asset(id="img-2", type="IMAGE"),  # no live photo
            _make_asset(id="img-3", type="IMAGE", livePhotoVideoId="vid-3"),
        ]
        service = self._make_search_service([assets_data])
        dr = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )

        result = await service.get_live_photos_for_date_range(dr)

        assert len(result) == 2
        assert result[0].id == "img-1"
        assert result[1].id == "img-3"
        assert all(a.is_live_photo for a in result)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_live_photos(self):
        """Should return empty list when no assets have livePhotoVideoId."""
        assets_data = [
            _make_asset(id="img-1", type="IMAGE"),
            _make_asset(id="img-2", type="IMAGE"),
        ]
        service = self._make_search_service([assets_data])
        dr = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )

        result = await service.get_live_photos_for_date_range(dr)

        assert not result

    @pytest.mark.asyncio
    async def test_paginates_through_all_pages(self):
        """Should paginate and collect live photos from all pages."""
        page1 = [_make_asset(id="img-1", type="IMAGE", livePhotoVideoId="vid-1")]
        page2 = [_make_asset(id="img-2", type="IMAGE", livePhotoVideoId="vid-2")]
        service = self._make_search_service([page1, page2])
        dr = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )

        result = await service.get_live_photos_for_date_range(dr)

        assert len(result) == 2
        assert result[0].id == "img-1"
        assert result[1].id == "img-2"


class TestTemporalClustering:
    """Slice 3: Group live photos into temporal clusters with overlap-aware trim points.

    Apple Live Photos are ~3s each (1.5s before + 1.5s after shutter). When photos
    are taken in rapid succession, their video portions overlap. The clustering
    algorithm groups them and computes non-overlapping trim windows so each clip
    contributes only its unique frames.
    """

    def test_groups_photos_within_window(self):
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # Photos 1s apart — overlap with 3s clip duration AND within merge window
        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:00Z",
                    livePhotoVideoId="v1",
                )
            ),
            Asset(
                **_make_asset(
                    id="b",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:01Z",
                    livePhotoVideoId="v2",
                )
            ),
            Asset(
                **_make_asset(
                    id="c",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:02Z",
                    livePhotoVideoId="v3",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10)

        assert len(clusters) == 1
        assert clusters[0].count == 3
        assert [a.id for a in clusters[0].assets] == ["a", "b", "c"]

    def test_separates_distant_photos(self):
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # a,b overlap (1s gap < 3s clip) but c is an hour away
        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:00:00Z",
                    livePhotoVideoId="v1",
                )
            ),
            Asset(
                **_make_asset(
                    id="b",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:00:01Z",
                    livePhotoVideoId="v2",
                )
            ),
            Asset(
                **_make_asset(
                    id="c",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T11:00:00Z",
                    livePhotoVideoId="v3",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10)

        assert len(clusters) == 2
        assert clusters[0].count == 2
        assert clusters[1].count == 1

    def test_single_photo_is_own_cluster(self):
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:00:00Z",
                    livePhotoVideoId="v1",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10)

        assert len(clusters) == 1
        assert clusters[0].count == 1

    def test_empty_input(self):
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        clusters = cluster_live_photos([], merge_window_seconds=10)
        assert not clusters

    def test_sorted_by_timestamp_within_cluster(self):
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # Photos 2s apart (overlap with 3s clip), given out of order
        assets = [
            Asset(
                **_make_asset(
                    id="c",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:04Z",
                    livePhotoVideoId="v3",
                )
            ),
            Asset(
                **_make_asset(
                    id="a",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:00Z",
                    livePhotoVideoId="v1",
                )
            ),
            Asset(
                **_make_asset(
                    id="b",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:02Z",
                    livePhotoVideoId="v2",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10)

        assert len(clusters) == 1
        assert [a.id for a in clusters[0].assets] == ["a", "b", "c"]

    def test_trim_points_no_overlap(self):
        """When clips don't overlap (>3s apart), split into individual clusters."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # Photos 5s apart — no overlap since each clip is only 3s
        # With auto-detection + split, these become 2 separate single-clip clusters
        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:00Z",
                    livePhotoVideoId="v1",
                )
            ),
            Asset(
                **_make_asset(
                    id="b",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:05Z",
                    livePhotoVideoId="v2",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10)

        # Non-overlapping clips get split into individual clusters
        assert len(clusters) == 2
        assert clusters[0].trim_points() == [(0.0, 3.0)]
        assert clusters[1].trim_points() == [(0.0, 3.0)]

    def test_trim_points_with_overlap_handoff_at_shutter(self):
        """Handoff at next shutter time: P1 plays until P2's shutter, P2 picks up from there."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # Photos 2s apart, clip_duration=3s, half_dur=1.5s
        # P1 shutter=0, P2 shutter=2
        # Handoff at t=2 (P2's shutter)
        # P1 local: handoff = 2 - 0 + 1.5 = 3.5 → clamped to 3.0 (full clip)
        # P2 local: handoff = 2 - 2 + 1.5 = 1.5
        # But gap=2 < clip_duration=3, so overlap exists
        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:00Z",
                    livePhotoVideoId="v1",
                )
            ),
            Asset(
                **_make_asset(
                    id="b",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:02Z",
                    livePhotoVideoId="v2",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10, clip_duration=3.0)
        trims = clusters[0].trim_points()

        assert len(trims) == 2
        # P1: plays full clip until handoff at local 3.0 (clamped)
        assert trims[0] == pytest.approx((0.0, 3.0), abs=0.01)
        # P2: starts at local 1.5 (the shutter point, since handoff IS at its shutter)
        assert trims[1] == pytest.approx((1.5, 3.0), abs=0.01)

    def test_trim_points_rapid_burst(self):
        """Your exact example: photos at t=0, t=0.5, t=2."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        assets = [
            Asset(
                **_make_asset(
                    id="a", fileCreatedAt="2024-07-15T10:30:00.000Z", livePhotoVideoId="v1"
                )
            ),
            Asset(
                **_make_asset(
                    id="b", fileCreatedAt="2024-07-15T10:30:00.500Z", livePhotoVideoId="v2"
                )
            ),
            Asset(
                **_make_asset(
                    id="c", fileCreatedAt="2024-07-15T10:30:02.000Z", livePhotoVideoId="v3"
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10, clip_duration=3.0)
        trims = clusters[0].trim_points()

        # P1 shutter=0: plays [-1.5, 0.5] → local (0, 2.0)
        # P2 shutter=0.5: plays [0.5, 2.0] → local (1.5, 3.0)
        # P3 shutter=2: plays [2.0, 3.5] → local (1.5, 3.0)
        assert len(trims) == 3
        assert trims[0] == pytest.approx((0.0, 2.0), abs=0.01)
        assert trims[1] == pytest.approx((1.5, 3.0), abs=0.01)
        assert trims[2] == pytest.approx((1.5, 3.0), abs=0.01)

    def test_estimated_duration(self):
        """Total duration = sum of all non-overlapping trim segments."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # Photos at t=0, t=0.5, t=2 → trims (0,2.0) + (1.5,3.0) + (1.5,3.0) = 2.0+1.5+1.5 = 5.0s
        assets = [
            Asset(
                **_make_asset(
                    id="a", fileCreatedAt="2024-07-15T10:30:00.000Z", livePhotoVideoId="v1"
                )
            ),
            Asset(
                **_make_asset(
                    id="b", fileCreatedAt="2024-07-15T10:30:00.500Z", livePhotoVideoId="v2"
                )
            ),
            Asset(
                **_make_asset(
                    id="c", fileCreatedAt="2024-07-15T10:30:02.000Z", livePhotoVideoId="v3"
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10, clip_duration=3.0)

        assert clusters[0].estimated_duration == pytest.approx(5.0, abs=0.01)

    def test_is_burst(self):
        """A cluster with 2+ photos is considered a burst."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        pair = [
            Asset(
                **_make_asset(id="a", fileCreatedAt="2024-07-15T10:30:00Z", livePhotoVideoId="v1")
            ),
            Asset(
                **_make_asset(id="b", fileCreatedAt="2024-07-15T10:30:02Z", livePhotoVideoId="v2")
            ),
        ]
        single = [
            Asset(
                **_make_asset(id="c", fileCreatedAt="2024-07-15T11:00:00Z", livePhotoVideoId="v3")
            ),
        ]

        clusters = cluster_live_photos(pair + single, merge_window_seconds=10)

        assert clusters[0].is_burst  # 2 photos = burst
        assert not clusters[1].is_burst  # 1 photo = not burst

    def test_cluster_is_favorite_if_any_photo_favorite(self):
        """Cluster should be favorite if ANY photo in it is favorited."""
        from immich_memories.processing.live_photo_merger import LivePhotoCluster

        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    fileCreatedAt="2024-07-15T10:30:00Z",
                    livePhotoVideoId="v1",
                    isFavorite=False,
                )
            ),
            Asset(
                **_make_asset(
                    id="b",
                    fileCreatedAt="2024-07-15T10:30:02Z",
                    livePhotoVideoId="v2",
                    isFavorite=True,
                )
            ),
            Asset(
                **_make_asset(
                    id="c",
                    fileCreatedAt="2024-07-15T10:30:04Z",
                    livePhotoVideoId="v3",
                    isFavorite=False,
                )
            ),
        ]
        cluster = LivePhotoCluster(assets=assets)
        assert cluster.is_favorite

    def test_cluster_not_favorite_if_none_favorite(self):
        """Cluster should not be favorite if no photos are favorited."""
        from immich_memories.processing.live_photo_merger import LivePhotoCluster

        assets = [
            Asset(
                **_make_asset(
                    id="a",
                    fileCreatedAt="2024-07-15T10:30:00Z",
                    livePhotoVideoId="v1",
                    isFavorite=False,
                )
            ),
        ]
        cluster = LivePhotoCluster(assets=assets)
        assert not cluster.is_favorite


class TestBurstMergerCommand:
    """Slice 4: FFmpeg command construction for merging Live Photo burst clips."""

    def test_builds_trim_and_xfade_filter(self):
        """Should generate ffmpeg filter_complex that trims, normalizes, and xfades."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/clip0.mov"), Path("/tmp/clip1.mov"), Path("/tmp/clip2.mov")]
        trim_points = [(0.0, 2.5), (0.5, 3.0), (0.0, 3.0)]
        output = Path("/tmp/merged.mp4")

        # WHY: probing real files would fail since they don't exist
        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False
            ),
        ):
            cmd = build_merge_command(clip_paths, trim_points, output)

        assert cmd.count("-i") == 3
        assert "-filter_complex" in cmd
        filter_str = cmd[cmd.index("-filter_complex") + 1]

        # Each input should be trimmed with normalize filter
        assert "[0:v]trim=start=0.0:end=2.5,setpts=PTS-STARTPTS,normalize=" in filter_str
        assert "[1:v]trim=start=0.5:end=3.0,setpts=PTS-STARTPTS,normalize=" in filter_str
        assert "[2:v]trim=start=0.0:end=3.0,setpts=PTS-STARTPTS,normalize=" in filter_str

        # Should use concat (clean cuts, crossfade belongs at assembly stage)
        assert "concat=n=3:v=1:a=0[outv]" in filter_str
        assert str(output) == cmd[-1]

    def test_includes_audio_with_fade(self):
        """Should trim audio with 30ms fade and concat separately from video."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/a.mov"), Path("/tmp/b.mov")]
        trim_points = [(0.0, 2.5), (0.5, 3.0)]
        output = Path("/tmp/out.mp4")

        # WHY: probing real files would fail since they don't exist
        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False
            ),
        ):
            cmd = build_merge_command(clip_paths, trim_points, output)

        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "[0:a]atrim=start=0.0:end=2.5" in filter_str
        assert "[1:a]atrim=start=0.5:end=3.0" in filter_str
        # Audio uses concat with 30ms fade (not acrossfade — it misaligns with video)
        assert "concat=n=2:v=0:a=1[outa]" in filter_str
        assert "afade=t=out" in filter_str

    def test_skips_audio_when_clips_lack_audio(self):
        """Should produce video-only output when any clip lacks audio."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/a.mov"), Path("/tmp/b.mov")]
        trim_points = [(0.0, 2.5), (0.5, 3.0)]
        output = Path("/tmp/out.mp4")

        # WHY: simulating clips without audio streams
        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=False,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False
            ),
        ):
            cmd = build_merge_command(clip_paths, trim_points, output)

        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "atrim" not in filter_str
        assert "-c:a" not in cmd

    def test_single_clip_no_concat(self):
        """A single clip should just trim and normalize without xfade."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/only.mov")]
        trim_points = [(0.0, 3.0)]
        output = Path("/tmp/out.mp4")

        # WHY: probing real files would fail since they don't exist
        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False
            ),
        ):
            cmd = build_merge_command(clip_paths, trim_points, output)

        assert "-filter_complex" in cmd
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "[0:v]trim=start=0.0:end=3.0" in filter_str
        assert "normalize=" in filter_str
        assert "concat" not in filter_str
        assert "xfade" not in filter_str

    def test_falls_back_to_concat_for_very_short_clips(self):
        """When clips are too short for xfade, should fall back to concat."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/a.mov"), Path("/tmp/b.mov")]
        # Very short trim durations (0.3s each — too short for 0.3s fade)
        trim_points = [(0.0, 0.3), (0.0, 0.3)]
        output = Path("/tmp/out.mp4")

        # WHY: probing real files would fail since they don't exist
        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False
            ),
        ):
            cmd = build_merge_command(clip_paths, trim_points, output)

        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "concat=n=2" in filter_str
        assert "xfade" not in filter_str


class TestLivePhotoConfig:
    """Slice 5: Config options for live photos."""

    def test_default_config_has_live_photos_enabled(self):
        from immich_memories.config_models import AnalysisConfig

        config = AnalysisConfig()
        assert config.include_live_photos

    def test_default_merge_window(self):
        from immich_memories.config_models import AnalysisConfig

        config = AnalysisConfig()
        assert config.live_photo_merge_window_seconds == 10.0

    def test_default_min_burst_count(self):
        from immich_memories.config_models import AnalysisConfig

        config = AnalysisConfig()
        assert config.live_photo_min_burst_count == 3

    def test_custom_values(self):
        from immich_memories.config_models import AnalysisConfig

        config = AnalysisConfig(
            include_live_photos=True,
            live_photo_merge_window_seconds=15.0,
            live_photo_min_burst_count=2,
        )
        assert config.include_live_photos
        assert config.live_photo_merge_window_seconds == 15.0
        assert config.live_photo_min_burst_count == 2


class TestFilterValidClips:
    """Slice 7: Filter out burst clips with no valid video stream before merging."""

    def test_removes_invalid_clips_and_their_trim_points(self):
        """Clips that fail probe should be removed along with their trim points."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import filter_valid_clips

        clip_paths = [Path("/tmp/ok1.mov"), Path("/tmp/bad.mov"), Path("/tmp/ok2.mov")]
        trim_points = [(0.0, 2.0), (1.5, 3.0), (0.0, 3.0)]

        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_video",
                side_effect=[True, False, True],
            ),
            # WHY: orientation check runs after video filter; both valid clips are landscape
            patch(
                "immich_memories.processing.live_photo_merger._probe_clip_orientation",
                return_value="landscape",
            ),
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert valid_paths == [Path("/tmp/ok1.mov"), Path("/tmp/ok2.mov")]
        assert valid_trims == [(0.0, 2.0), (0.0, 3.0)]

    def test_all_clips_invalid_returns_empty(self):
        """When all clips are invalid, return empty lists."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import filter_valid_clips

        clip_paths = [Path("/tmp/bad1.mov"), Path("/tmp/bad2.mov")]
        trim_points = [(0.0, 2.0), (1.5, 3.0)]

        with patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_video",
            return_value=False,
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert not valid_paths
        assert not valid_trims

    def test_all_clips_valid_returns_all(self):
        """When all clips are valid, return everything unchanged."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import filter_valid_clips

        clip_paths = [Path("/tmp/ok1.mov"), Path("/tmp/ok2.mov")]
        trim_points = [(0.0, 2.0), (1.5, 3.0)]

        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_video",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._probe_clip_orientation",
                return_value="landscape",
            ),
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert valid_paths == clip_paths
        assert valid_trims == trim_points

    def test_rejects_mismatched_orientation(self):
        """Portrait clip in a mostly-landscape burst gets rejected."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import filter_valid_clips

        clip_paths = [
            Path("/tmp/land1.mov"),
            Path("/tmp/port.mov"),
            Path("/tmp/land2.mov"),
        ]
        trim_points = [(0.0, 2.0), (1.5, 3.0), (0.0, 3.0)]

        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_video",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._probe_clip_orientation",
                side_effect=["landscape", "portrait", "landscape"],
            ),
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert valid_paths == [Path("/tmp/land1.mov"), Path("/tmp/land2.mov")]
        assert valid_trims == [(0.0, 2.0), (0.0, 3.0)]

    def test_keeps_all_same_orientation(self):
        """All portrait clips pass orientation check."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import filter_valid_clips

        clip_paths = [Path("/tmp/p1.mov"), Path("/tmp/p2.mov")]
        trim_points = [(0.0, 2.0), (1.5, 3.0)]

        with (
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_video",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger._probe_clip_orientation",
                return_value="portrait",
            ),
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert valid_paths == clip_paths
        assert valid_trims == trim_points

    def test_single_clip_skips_orientation_check(self):
        """Single valid clip returns without orientation filtering."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import filter_valid_clips

        clip_paths = [Path("/tmp/solo.mov")]
        trim_points = [(0.0, 3.0)]

        with patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_video",
            return_value=True,
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert valid_paths == clip_paths
        assert valid_trims == trim_points


class TestExtractRotation:
    """Unit tests for _extract_rotation — parses rotation from ffprobe stream data."""

    def test_returns_rotation_from_tags(self):
        from immich_memories.processing.live_photo_merger import _extract_rotation

        stream = {"tags": {"rotate": "90"}}
        assert _extract_rotation(stream) == 90

    def test_returns_rotation_from_display_matrix_side_data(self):
        from immich_memories.processing.live_photo_merger import _extract_rotation

        stream = {
            "side_data_list": [
                {"side_data_type": "Display Matrix", "rotation": -90},
            ],
        }
        assert _extract_rotation(stream) == 90

    def test_returns_zero_when_no_rotation_data(self):
        from immich_memories.processing.live_photo_merger import _extract_rotation

        assert _extract_rotation({}) == 0
        assert _extract_rotation({"tags": {}}) == 0
        assert _extract_rotation({"side_data_list": []}) == 0


class TestProbeClipOrientation:
    """Unit tests for _probe_clip_orientation — probes displayed orientation via ffprobe."""

    def test_landscape_video(self):
        """1920x1080 with no rotation → landscape."""
        import json
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import _probe_clip_orientation

        ffprobe_output = json.dumps({"streams": [{"width": 1920, "height": 1080}]})
        mock_result = type("Result", (), {"stdout": ffprobe_output})()

        # WHY: ffprobe is external binary
        with patch("subprocess.run", return_value=mock_result):
            assert _probe_clip_orientation(Path("/tmp/clip.mov")) == "landscape"

    def test_portrait_video_with_90_rotation(self):
        """1920x1080 stored pixels + 90° rotation → portrait (1080x1920 displayed)."""
        import json
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import _probe_clip_orientation

        ffprobe_output = json.dumps(
            {
                "streams": [
                    {
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [
                            {"side_data_type": "Display Matrix", "rotation": -90},
                        ],
                    }
                ]
            }
        )
        mock_result = type("Result", (), {"stdout": ffprobe_output})()

        # WHY: ffprobe is external binary
        with patch("subprocess.run", return_value=mock_result):
            assert _probe_clip_orientation(Path("/tmp/clip.mov")) == "portrait"

    def test_returns_none_when_ffprobe_fails(self):
        """Should return None when ffprobe raises an exception."""
        from unittest.mock import patch

        from immich_memories.processing.live_photo_merger import _probe_clip_orientation

        # WHY: ffprobe is external binary
        with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe not found")):
            assert _probe_clip_orientation(Path("/tmp/clip.mov")) is None


class TestEstimateClipDuration:
    """Detect device-specific clip durations from EXIF make."""

    def test_google_clip_duration(self):
        from immich_memories.processing.live_photo_merger import estimate_clip_duration

        asset = Asset(
            **_make_asset(
                id="px",
                type="IMAGE",
                livePhotoVideoId="v1",
                exifInfo={"make": "Google"},
            )
        )
        assert estimate_clip_duration(asset) == 1.5

    def test_samsung_clip_duration(self):
        from immich_memories.processing.live_photo_merger import estimate_clip_duration

        asset = Asset(
            **_make_asset(
                id="sam",
                type="IMAGE",
                livePhotoVideoId="v1",
                exifInfo={"make": "samsung"},
            )
        )
        # Samsung behaves like Apple — 3.0s default
        assert estimate_clip_duration(asset) == 3.0

    def test_unknown_clip_duration_fallback(self):
        from immich_memories.processing.live_photo_merger import estimate_clip_duration

        asset = Asset(**_make_asset(id="unk", type="IMAGE", livePhotoVideoId="v1"))
        assert estimate_clip_duration(asset) == 3.0


class TestSplitNonOverlapping:
    """Split clusters at gaps where clips don't temporally overlap."""

    def test_non_overlapping_google_clips_split_into_individuals(self):
        """4 Google Pixel clips ~2s apart with 1.5s clip_duration → 4 individual clusters."""
        from immich_memories.processing.live_photo_merger import (
            LivePhotoCluster,
            split_non_overlapping,
        )

        assets = [
            Asset(
                **_make_asset(
                    id=f"px{i}",
                    type="IMAGE",
                    fileCreatedAt=f"2024-07-15T10:30:{i * 2:02d}Z",
                    livePhotoVideoId=f"v{i}",
                    exifInfo={"make": "Google"},
                )
            )
            for i in range(4)
        ]
        cluster = LivePhotoCluster(assets=assets, clip_duration=1.5)
        result = split_non_overlapping(cluster)

        assert len(result) == 4
        for c in result:
            assert c.count == 1
            assert c.clip_duration == 1.5

    def test_overlapping_samsung_cluster_stays_merged(self):
        """7 Samsung clips 0.2s apart → 1 cluster (massive overlap)."""
        from immich_memories.processing.live_photo_merger import (
            LivePhotoCluster,
            split_non_overlapping,
        )

        assets = [
            Asset(
                **_make_asset(
                    id=f"sam{i}",
                    type="IMAGE",
                    fileCreatedAt=f"2024-07-15T14:13:0{i}.200Z",
                    livePhotoVideoId=f"v{i}",
                    exifInfo={"make": "samsung"},
                )
            )
            for i in range(7)
        ]
        # Samsung clips: 3.0s duration, 1s apart → all overlap (gap < 3.0)
        cluster = LivePhotoCluster(assets=assets, clip_duration=3.0)
        result = split_non_overlapping(cluster)

        assert len(result) == 1
        assert result[0].count == 7

    def test_mixed_overlap_splits_at_gap(self):
        """A-B overlap (gap=0.5s), gap to C (gap=5s), C-D overlap (gap=0.5s) → 2 clusters."""
        from immich_memories.processing.live_photo_merger import (
            LivePhotoCluster,
            split_non_overlapping,
        )

        assets = [
            Asset(
                **_make_asset(
                    id="a", fileCreatedAt="2024-07-15T10:30:00.000Z", livePhotoVideoId="v1"
                )
            ),
            Asset(
                **_make_asset(
                    id="b", fileCreatedAt="2024-07-15T10:30:00.500Z", livePhotoVideoId="v2"
                )
            ),
            Asset(
                **_make_asset(
                    id="c", fileCreatedAt="2024-07-15T10:30:05.000Z", livePhotoVideoId="v3"
                )
            ),
            Asset(
                **_make_asset(
                    id="d", fileCreatedAt="2024-07-15T10:30:05.500Z", livePhotoVideoId="v4"
                )
            ),
        ]
        cluster = LivePhotoCluster(assets=assets, clip_duration=3.0)
        result = split_non_overlapping(cluster)

        assert len(result) == 2
        assert [a.id for a in result[0].assets] == ["a", "b"]
        assert [a.id for a in result[1].assets] == ["c", "d"]

    def test_single_clip_cluster_passes_through(self):
        """Single-clip cluster returns unchanged."""
        from immich_memories.processing.live_photo_merger import (
            LivePhotoCluster,
            split_non_overlapping,
        )

        assets = [
            Asset(
                **_make_asset(
                    id="solo", fileCreatedAt="2024-07-15T10:30:00Z", livePhotoVideoId="v1"
                )
            ),
        ]
        cluster = LivePhotoCluster(assets=assets, clip_duration=3.0)
        result = split_non_overlapping(cluster)

        assert len(result) == 1
        assert result[0].count == 1


class TestCLILivePhotosFlag:
    """Slice 5b: CLI --include-live-photos flag."""

    def test_generate_has_include_live_photos_flag(self):
        """The generate command should accept --include-live-photos."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        # --help should list the flag without error
        result = CliRunner().invoke(main, ["generate", "--help"])
        assert result.exit_code == 0
        assert "--include-live-photos" in result.output
