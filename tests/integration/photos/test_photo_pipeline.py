"""Integration tests for the photo pipeline with real Immich and FFmpeg.

Connects to a real Immich server, fetches IMAGE assets, runs grouping,
scoring, animation (with HDR detection), and verifies the output clips.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date

import pytest

from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import PhotoAnimator, detect_photo_hdr_type
from immich_memories.photos.grouper import PhotoGrouper
from immich_memories.photos.scoring import score_photo
from immich_memories.timeperiod import DateRange
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

logger = logging.getLogger(__name__)


def _has_immich() -> bool:
    try:
        config = Config.from_yaml(Config.get_default_path())
        if not config.immich.url or not config.immich.api_key:
            return False
        import httpx

        resp = httpx.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


requires_immich = pytest.mark.skipif(not _has_immich(), reason="Immich not reachable")
pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich]


@pytest.fixture(scope="module")
def immich_photos():
    """Fetch real photos from Immich. Module-scoped to avoid repeated API calls."""
    from immich_memories.api.sync_client import SyncImmichClient

    config = Config.from_yaml(Config.get_default_path())
    config.defaults.target_duration_seconds = 60  # Cap at 60s for test speed
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    # Narrow range — one month is enough to verify the pipeline works
    photos = client.get_photos_for_date_range(
        DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))
    )

    if not photos:
        pytest.skip("No photos found in Immich")

    logger.info(f"Found {len(photos)} photos in Immich")
    return photos, config, client


class TestImmichPhotoFetch:
    """Tests that verify we can fetch and inspect real photos from Immich."""

    def test_photos_are_images_not_live_photos(self, immich_photos):
        """Fetched photos are IMAGE type and not live photos."""
        photos, _config, _client = immich_photos

        for photo in photos[:10]:  # Check first 10
            assert photo.type == AssetType.IMAGE
            assert not photo.is_live_photo

    def test_photos_have_created_date(self, immich_photos):
        """All photos have a file_created_at timestamp."""
        photos, _config, _client = immich_photos

        for photo in photos[:10]:
            assert photo.file_created_at is not None

    def test_photos_sorted_chronologically(self, immich_photos):
        """Photos are returned sorted by creation date."""
        photos, _config, _client = immich_photos

        if len(photos) < 2:
            pytest.skip("Need at least 2 photos")

        for i in range(min(len(photos) - 1, 20)):
            assert photos[i].file_created_at <= photos[i + 1].file_created_at

    def test_some_photos_have_exif(self, immich_photos):
        """At least some photos have EXIF data (camera make/model)."""
        photos, _config, _client = immich_photos

        has_exif = [p for p in photos if p.exif_info and p.exif_info.make]
        logger.info(f"{len(has_exif)}/{len(photos)} photos have EXIF make")
        # Most real photos should have EXIF — at least 1
        assert len(has_exif) >= 1


class TestImmichPhotoGrouping:
    """Tests that verify photo grouping works on real Immich data."""

    def test_grouper_produces_groups(self, immich_photos):
        """PhotoGrouper clusters real photos into groups."""
        photos, _config, _client = immich_photos

        config = PhotoConfig(series_gap_seconds=60.0)
        grouper = PhotoGrouper(config)
        groups = grouper.group(photos)

        assert len(groups) >= 1
        logger.info(
            f"Grouped {len(photos)} photos into {len(groups)} groups, "
            f"{sum(1 for g in groups if g.is_series)} series"
        )

        # All asset IDs should be accounted for
        all_ids = {aid for g in groups for aid in g.asset_ids}
        original_ids = {p.id for p in photos}
        assert all_ids == original_ids

    def test_scoring_produces_valid_scores(self, immich_photos):
        """score_photo returns valid scores for real photos."""
        photos, _config, _client = immich_photos

        config = PhotoConfig()
        scores = [score_photo(p, config) for p in photos[:20]]

        assert all(0.0 <= s <= 1.0 for s in scores)
        # At least some variance in scores
        if len(scores) >= 3:
            assert len(set(scores)) >= 2, "All photos scored identically — scoring may be broken"


class TestImmichPhotoAnimation:
    """Tests that verify real Immich photos can be animated to video clips."""

    def test_download_and_animate_ken_burns(self, immich_photos, tmp_path):
        """Download a real photo from Immich, animate with Ken Burns, verify output."""
        photos, config, client = immich_photos

        # Pick first photo
        photo = photos[0]
        source_path = tmp_path / f"{photo.id}.jpg"
        client.download_asset(photo.id, source_path)
        assert source_path.exists()
        assert source_path.stat().st_size > 100

        # Probe the source to get dimensions
        probe = ffprobe_json(source_path)
        streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
        if not streams:
            pytest.skip("Downloaded asset has no video stream (may be HEIF without decoder)")
        src_w = int(streams[0]["width"])
        src_h = int(streams[0]["height"])

        # Detect HDR type
        hdr_type = detect_photo_hdr_type(source_path)
        logger.info(f"Photo {photo.id}: {src_w}x{src_h}, HDR={hdr_type}")

        # Animate
        output_path = tmp_path / "ken_burns.mp4"
        photo_config = PhotoConfig(duration=3.0)
        animator = PhotoAnimator(photo_config, target_w=1920, target_h=1080)
        cmd = animator.build_ffmpeg_command(
            source_path=source_path,
            output_path=output_path,
            width=src_w,
            height=src_h,
            mode="auto",
            asset_id=photo.id,
            hdr_type=hdr_type,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"FFmpeg failed: {result.stderr[:500]}"

        # Verify output
        assert output_path.exists()
        out_probe = ffprobe_json(output_path)
        assert has_stream(out_probe, "video")
        assert has_stream(out_probe, "audio")

        duration = get_duration(out_probe)
        assert 2.0 < duration < 4.5

        out_streams = [s for s in out_probe.get("streams", []) if s.get("codec_type") == "video"]
        out_w = int(out_streams[0]["width"])
        out_h = int(out_streams[0]["height"])
        assert out_w == 1920
        assert out_h == 1080

        logger.info(f"Animated photo → {out_w}x{out_h}, duration={duration:.1f}s")

    def test_animate_portrait_photo(self, immich_photos, tmp_path):
        """Find a portrait photo and animate with auto mode (should use blur_bg)."""
        photos, _config, client = immich_photos

        # Find a portrait photo
        portrait = None
        for photo in photos:
            if photo.exif_info and photo.exif_info.latitude:
                # Try to find one with location data for variety
                pass
            source = tmp_path / f"probe_{photo.id}.jpg"
            client.download_asset(photo.id, source)
            probe = ffprobe_json(source)
            streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
            if not streams:
                continue
            w = int(streams[0]["width"])
            h = int(streams[0]["height"])
            if h > w:
                portrait = (photo, source, w, h)
                break

        if portrait is None:
            pytest.skip("No portrait photos found in Immich")

        photo_asset, source_path, src_w, src_h = portrait
        logger.info(f"Portrait photo: {photo_asset.id} ({src_w}x{src_h})")

        output = tmp_path / "portrait_auto.mp4"
        photo_config = PhotoConfig(duration=3.0)
        animator = PhotoAnimator(photo_config, target_w=1920, target_h=1080)
        cmd = animator.build_ffmpeg_command(
            source_path=source_path,
            output_path=output,
            width=src_w,
            height=src_h,
            mode="auto",
            asset_id=photo_asset.id,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"FFmpeg failed: {result.stderr[:500]}"

        out_probe = ffprobe_json(output)
        assert has_stream(out_probe, "video")

        # Auto on portrait → blur_bg → landscape output
        out_streams = [s for s in out_probe.get("streams", []) if s.get("codec_type") == "video"]
        assert int(out_streams[0]["width"]) == 1920
        assert int(out_streams[0]["height"]) == 1080

    def test_hdr_photo_detection(self, immich_photos, tmp_path):
        """Check if any photos are HDR and log the results."""
        photos, _config, client = immich_photos

        hdr_count = 0
        checked = 0
        for photo in photos[:20]:
            source = tmp_path / f"hdr_check_{photo.id}.jpg"
            client.download_asset(photo.id, source)
            hdr = detect_photo_hdr_type(source)
            if hdr:
                hdr_count += 1
                logger.info(f"HDR photo found: {photo.id} type={hdr}")
            checked += 1

        logger.info(f"HDR detection: {hdr_count}/{checked} photos are HDR")
        # This test always passes — it's observational.
        # If HDR photos exist, verify they can be animated:
        if hdr_count > 0:
            # Find the first HDR photo and animate it
            for photo in photos[:20]:
                source = tmp_path / f"hdr_check_{photo.id}.jpg"
                hdr = detect_photo_hdr_type(source)
                if hdr:
                    probe = ffprobe_json(source)
                    streams = [
                        s for s in probe.get("streams", []) if s.get("codec_type") == "video"
                    ]
                    if not streams:
                        continue
                    w, h = int(streams[0]["width"]), int(streams[0]["height"])
                    output = tmp_path / "hdr_animated.mp4"
                    animator = PhotoAnimator(
                        PhotoConfig(duration=3.0), target_w=1920, target_h=1080
                    )
                    cmd = animator.build_ffmpeg_command(
                        source_path=source,
                        output_path=output,
                        width=w,
                        height=h,
                        mode="auto",
                        asset_id=photo.id,
                        hdr_type=hdr,
                    )
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    assert result.returncode == 0, f"HDR animation failed: {result.stderr[:500]}"
                    assert output.exists()

                    out_probe = ffprobe_json(output)
                    assert has_stream(out_probe, "video")
                    logger.info(f"HDR photo animated successfully: {hdr} → HEVC")
                    break


class TestImmichPhotoWithPeople:
    """Tests that verify face/people data on photos from Immich."""

    def test_some_photos_have_people(self, immich_photos):
        """At least some photos should have people tagged."""
        photos, _config, _client = immich_photos

        with_people = [p for p in photos if p.people]
        logger.info(f"{len(with_people)}/{len(photos)} photos have people tagged")

        # This may be 0 if Immich face detection hasn't run — just log it
        if with_people:
            first = with_people[0]
            logger.info(
                f"Photo {first.id} has {len(first.people)} people: {[p.name for p in first.people]}"
            )

    def test_face_data_available_for_scoring(self, immich_photos):
        """Photos with faces score higher than those without."""
        photos, _config, _client = immich_photos

        with_people = [p for p in photos if p.people]
        without_people = [p for p in photos if not p.people]

        if not with_people or not without_people:
            pytest.skip("Need both photos with and without people for comparison")

        config = PhotoConfig()
        score_with = score_photo(with_people[0], config)
        score_without = score_photo(without_people[0], config)

        logger.info(f"Score with faces: {score_with:.3f}, without: {score_without:.3f}")
        assert score_with > score_without
