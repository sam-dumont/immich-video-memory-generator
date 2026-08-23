"""Integration tests for the photo pipeline with real Immich and FFmpeg.

Connects to a real Immich server, fetches IMAGE assets, and runs scoring,
HDR detection, and face-data checks against them.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import detect_photo_hdr_type
from immich_memories.photos.scoring import score_photo
from immich_memories.timeperiod import DateRange
from tests.integration.conftest import requires_ffmpeg

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


class TestImmichPhotoScoring:
    """Tests that verify photo scoring works on real Immich data."""

    def test_scoring_produces_valid_scores(self, immich_photos):
        """score_photo returns valid scores for real photos."""
        photos, _config, _client = immich_photos

        config = PhotoConfig()
        scores = [score_photo(p, config) for p in photos[:20]]

        assert all(0.0 <= s <= 1.0 for s in scores)
        # At least some variance in scores
        if len(scores) >= 3:
            assert len(set(scores)) >= 2, "All photos scored identically — scoring may be broken"


class TestImmichPhotoHdrDetection:
    """Tests that verify HDR detection against real Immich photos."""

    def test_hdr_photo_detection(self, immich_photos, tmp_path):
        """Check if any photos are HDR and log the results."""
        photos, _config, client = immich_photos

        detected = []
        for photo in photos[:20]:
            source = tmp_path / f"hdr_check_{photo.id}.jpg"
            client.download_asset(photo.id, source)
            detected.append(detect_photo_hdr_type(source))

        hdr_count = sum(1 for h in detected if h)
        logger.info(f"HDR detection: {hdr_count}/{len(detected)} photos are HDR")

        # A library with no HDR photos is a valid library, so the assertion is
        # on the vocabulary rather than the count.
        assert detected
        assert all(h in (None, "hlg", "pq") for h in detected)


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
