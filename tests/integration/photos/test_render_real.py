"""Real Immich integration tests for photos/photo_pipeline.py rendering.

Downloads real photos from Immich, scores them, renders animated clips,
verifies the output. Tests the full _render_single_photo and score_photos
flows with actual image data.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from immich_memories.config_loader import Config
from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.photo_pipeline import _render_single_photo, score_photos
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
def immich_photo_assets():
    """Fetch real photo assets from Immich. Module-scoped."""
    from immich_memories.api.sync_client import SyncImmichClient

    config = Config.from_yaml(Config.get_default_path())
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    photos = client.get_photos_for_date_range(
        DateRange(start=date(2025, 1, 1), end=date(2025, 3, 31))
    )
    if not photos:
        # Widen the range
        photos = client.get_photos_for_date_range(
            DateRange(start=date(2024, 1, 1), end=date(2025, 12, 31))
        )
    if not photos:
        pytest.skip("No photos found in Immich")

    logger.info(f"Found {len(photos)} photos in Immich")
    return photos, config, client


class TestScorePhotosRealImmich:
    def test_score_photos_returns_valid_scores(self, immich_photo_assets, tmp_path):
        """score_photos with real assets should return (asset, score) tuples in [0, 1]."""
        photos, config, client = immich_photo_assets
        photo_config = PhotoConfig()

        scored = score_photos(
            assets=photos[:10],
            config=photo_config,
            video_clip_count=20,
            work_dir=tmp_path / "score_work",
            download_fn=client.download_asset,
            thumbnail_fn=client.get_asset_thumbnail,
        )

        assert len(scored) > 0
        for _asset, score in scored:
            assert 0.0 <= score <= 1.0
        logger.info(
            f"Scored {len(scored)} photos, range [{scored[0][1]:.3f} - {scored[-1][1]:.3f}]"
        )


class TestRenderSinglePhotoRealImmich:
    def test_render_produces_valid_video_clips(self, immich_photo_assets, tmp_path):
        """Real Immich photographs each render to a playable clip.

        The max_ratio cap this class also exercised lived inside the batch
        renderer, as a second independent cap over an already-selected set.
        max_ratio is now one policy on the ranked list (enforce_photo_cap).
        """
        photos, _config, client = immich_photo_assets
        photo_config = PhotoConfig()
        photo_config.duration = 3.0

        clips = [
            _render_single_photo(
                asset=photo,
                config=photo_config,
                target_w=1280,
                target_h=720,
                work_dir=tmp_path / "render_work",
                download_fn=client.download_asset,
            )
            for photo in photos[:3]
        ]
        rendered = [clip for clip in clips if clip is not None]

        assert rendered
        for clip in rendered:
            assert clip.path.exists()
            probe = ffprobe_json(clip.path)
            assert has_stream(probe, "video")
            duration = get_duration(probe)
            assert duration > 1.0
            logger.info(f"Rendered photo clip: {clip.asset_id}, {duration:.1f}s")
