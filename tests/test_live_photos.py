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
        assert with_lp.is_live_photo is True
        assert without_lp.is_live_photo is False


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
            Asset(
                **_make_asset(
                    id="c",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:08Z",
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
                    fileCreatedAt="2024-07-15T10:00:05Z",
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

        assets = [
            Asset(
                **_make_asset(
                    id="c",
                    type="IMAGE",
                    fileCreatedAt="2024-07-15T10:30:08Z",
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
                    fileCreatedAt="2024-07-15T10:30:04Z",
                    livePhotoVideoId="v2",
                )
            ),
        ]

        clusters = cluster_live_photos(assets, merge_window_seconds=10)

        assert len(clusters) == 1
        assert [a.id for a in clusters[0].assets] == ["a", "b", "c"]

    def test_trim_points_no_overlap(self):
        """When clips don't overlap (>3s apart), each uses full 0-3s range."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        # Photos 5s apart — no overlap since each clip is only 3s
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
        trims = clusters[0].trim_points()

        # No overlap: each clip uses its full duration
        assert len(trims) == 2
        assert trims[0] == (0.0, 3.0)
        assert trims[1] == (0.0, 3.0)

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
        """A cluster with 3+ photos is considered a burst."""
        from immich_memories.processing.live_photo_merger import cluster_live_photos

        burst = [
            Asset(
                **_make_asset(id="a", fileCreatedAt="2024-07-15T10:30:00Z", livePhotoVideoId="v1")
            ),
            Asset(
                **_make_asset(id="b", fileCreatedAt="2024-07-15T10:30:02Z", livePhotoVideoId="v2")
            ),
            Asset(
                **_make_asset(id="c", fileCreatedAt="2024-07-15T10:30:04Z", livePhotoVideoId="v3")
            ),
        ]
        single = [
            Asset(
                **_make_asset(id="d", fileCreatedAt="2024-07-15T11:00:00Z", livePhotoVideoId="v4")
            ),
        ]

        clusters = cluster_live_photos(burst + single, merge_window_seconds=10)

        assert clusters[0].is_burst is True
        assert clusters[1].is_burst is False

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
        assert cluster.is_favorite is True

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
        assert cluster.is_favorite is False


class TestBurstMergerCommand:
    """Slice 4: FFmpeg command construction for merging Live Photo burst clips."""

    def test_builds_trim_and_concat_filter(self):
        """Should generate ffmpeg filter_complex that trims each clip and concatenates."""
        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/clip0.mov"), Path("/tmp/clip1.mov"), Path("/tmp/clip2.mov")]
        trim_points = [(0.0, 2.5), (0.5, 3.0), (0.0, 3.0)]
        output = Path("/tmp/merged.mp4")

        cmd = build_merge_command(clip_paths, trim_points, output)

        # Should have -i for each input
        assert cmd.count("-i") == 3
        # Should use filter_complex with trim + concat
        assert "-filter_complex" in cmd
        filter_idx = cmd.index("-filter_complex")
        filter_str = cmd[filter_idx + 1]
        # Each input should be trimmed
        assert "[0:v]trim=start=0.0:end=2.5" in filter_str
        assert "[1:v]trim=start=0.5:end=3.0" in filter_str
        assert "[2:v]trim=start=0.0:end=3.0" in filter_str
        # Should concatenate all streams
        assert "concat=n=3" in filter_str
        # Output path
        assert str(output) == cmd[-1]

    def test_includes_audio_streams(self):
        """Should trim and concat both video and audio streams."""
        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/a.mov"), Path("/tmp/b.mov")]
        trim_points = [(0.0, 2.5), (0.5, 3.0)]
        output = Path("/tmp/out.mp4")

        cmd = build_merge_command(clip_paths, trim_points, output)
        filter_str = cmd[cmd.index("-filter_complex") + 1]

        # Should have audio trim + concat too
        assert "[0:a]atrim=start=0.0:end=2.5" in filter_str
        assert "[1:a]atrim=start=0.5:end=3.0" in filter_str
        assert "concat=n=2:v=1:a=1" in filter_str

    def test_single_clip_no_concat(self):
        """A single clip should just trim without concat filter."""
        from immich_memories.processing.live_photo_merger import build_merge_command

        clip_paths = [Path("/tmp/only.mov")]
        trim_points = [(0.0, 3.0)]
        output = Path("/tmp/out.mp4")

        cmd = build_merge_command(clip_paths, trim_points, output)

        # Single clip: just trim, no concat needed
        assert "-filter_complex" in cmd
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "[0:v]trim=start=0.0:end=3.0" in filter_str
        assert "concat" not in filter_str


class TestLivePhotoConfig:
    """Slice 5: Config options for live photos."""

    def test_default_config_has_live_photos_disabled(self):
        from immich_memories.config_models import AnalysisConfig

        config = AnalysisConfig()
        assert not config.include_live_photos

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

        with patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_video",
            side_effect=[True, False, True],
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

        with patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_video",
            return_value=True,
        ):
            valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)

        assert valid_paths == clip_paths
        assert valid_trims == trim_points


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
