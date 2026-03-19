"""Tests for photo scoring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.api.models import Person
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.scoring import score_photo
from tests.conftest import make_asset


def _photo(
    asset_id: str = "p1",
    *,
    is_favorite: bool = False,
    exif_make: str | None = "Apple",
    people: list[Person] | None = None,
):
    """Create a photo Asset for scoring tests."""
    asset = make_asset(
        asset_id,
        is_favorite=is_favorite,
        exif_make=exif_make,
        duration=None,
    )
    if people:
        asset.people = people
    return asset


class TestPhotoScoring:
    """Tests for photo score calculation."""

    def test_default_score_range(self):
        """Score is between 0 and 1."""
        config = PhotoConfig()
        score = score_photo(_photo(), config)
        assert 0.0 <= score <= 1.0

    def test_favorite_boost(self):
        """Favorites score higher than non-favorites."""
        config = PhotoConfig()
        normal = score_photo(_photo("p1", is_favorite=False), config)
        fav = score_photo(_photo("p2", is_favorite=True), config)
        assert fav > normal

    def test_faces_boost(self):
        """Photos with faces score higher."""
        config = PhotoConfig()
        no_faces = score_photo(_photo("p1"), config)
        person = Person(id="person-1", name="Alice")
        with_faces = score_photo(_photo("p2", people=[person]), config)
        assert with_faces > no_faces

    def test_camera_original_boost(self):
        """Photos from real cameras score higher than screenshots."""
        config = PhotoConfig()
        camera = score_photo(_photo("p1", exif_make="Apple"), config)
        screenshot = score_photo(_photo("p2", exif_make=None), config)
        assert camera > screenshot

    def test_penalty_applied(self):
        """Score is reduced by score_penalty factor."""
        config_no_penalty = PhotoConfig(score_penalty=0.0)
        config_with_penalty = PhotoConfig(score_penalty=0.3)
        score_full = score_photo(_photo(), config_no_penalty)
        score_penalized = score_photo(_photo(), config_with_penalty)
        assert score_penalized < score_full

    def test_zero_penalty_means_full_score(self):
        """With score_penalty=0, score equals raw score."""
        config = PhotoConfig(score_penalty=0.0)
        score = score_photo(_photo(), config)
        # Should be the raw score, not reduced
        assert score > 0.0

    def test_max_penalty_gives_zero(self):
        """With score_penalty=1.0, all photos score 0."""
        config = PhotoConfig(score_penalty=1.0)
        score = score_photo(_photo(), config)
        assert score == 0.0


class TestCacheFirstScoring:
    """Tests for _enhance_with_llm cache-first scoring (lines 126-198)."""

    def _make_scored(self, count: int = 3) -> list[tuple]:
        """Build a list of (Asset, metadata_score) tuples."""

        return [(_photo(f"asset-{i}"), 0.5 + i * 0.1) for i in range(count)]

    def test_cache_hit_returns_cached_score_no_llm(self):
        """When score is cached, return it without calling LLM."""
        from immich_memories.photos.photo_pipeline import _enhance_with_llm

        scored = [(_photo("cached-1"), 0.4)]

        mock_cache = MagicMock()
        mock_cache.get_asset_scores_batch.return_value = {
            "cached-1": {"combined_score": 0.88},
        }

        with (
            patch(
                "immich_memories.photos.photo_pipeline._get_score_cache",
                return_value=mock_cache,
            ),
            patch(
                "immich_memories.photos.photo_pipeline._llm_score_photo",
            ) as mock_llm,
        ):
            result = _enhance_with_llm(scored, PhotoConfig(), Path("/tmp"), lambda *_args: None)

        assert len(result) == 1
        assert result[0][1] == 0.88
        mock_llm.assert_not_called()

    def test_cache_miss_calls_llm_and_saves(self):
        """When score is NOT cached, run LLM and save result to cache."""
        from immich_memories.photos.photo_pipeline import _enhance_with_llm

        scored = [(_photo("uncached-1"), 0.5)]

        mock_cache = MagicMock()
        # WHY: database — no cached entry for this asset
        mock_cache.get_asset_scores_batch.return_value = {}

        with (
            patch(
                "immich_memories.photos.photo_pipeline._get_score_cache",
                return_value=mock_cache,
            ),
            # WHY: external LLM API
            patch(
                "immich_memories.photos.photo_pipeline._llm_score_photo",
                return_value=0.75,
            ) as mock_llm,
        ):
            result = _enhance_with_llm(scored, PhotoConfig(), Path("/tmp"), lambda *_args: None)

        assert result[0][1] == 0.75
        mock_llm.assert_called_once()
        mock_cache.save_asset_score.assert_called_once_with(
            asset_id="uncached-1",
            asset_type="photo",
            metadata_score=0.5,
            combined_score=0.75,
        )

    def test_mix_of_cached_and_uncached(self):
        """Batch with some hits and some misses handles both correctly."""
        from immich_memories.photos.photo_pipeline import _enhance_with_llm

        scored = [
            (_photo("hit-1"), 0.3),
            (_photo("miss-1"), 0.4),
            (_photo("hit-2"), 0.6),
        ]

        mock_cache = MagicMock()
        # WHY: database — two hits, one miss
        mock_cache.get_asset_scores_batch.return_value = {
            "hit-1": {"combined_score": 0.91},
            "hit-2": {"combined_score": 0.82},
        }

        with (
            patch(
                "immich_memories.photos.photo_pipeline._get_score_cache",
                return_value=mock_cache,
            ),
            # WHY: external LLM API — only called for the miss
            patch(
                "immich_memories.photos.photo_pipeline._llm_score_photo",
                return_value=0.55,
            ) as mock_llm,
        ):
            result = _enhance_with_llm(scored, PhotoConfig(), Path("/tmp"), lambda *_args: None)

        assert len(result) == 3
        assert result[0][1] == 0.91  # cached
        assert result[1][1] == 0.55  # LLM
        assert result[2][1] == 0.82  # cached
        # LLM called exactly once (for miss-1)
        mock_llm.assert_called_once()
        # Cache save called exactly once (for miss-1)
        mock_cache.save_asset_score.assert_called_once()

    def test_no_cache_available_still_runs_llm(self):
        """When _get_score_cache returns None, LLM runs for all assets."""
        from immich_memories.photos.photo_pipeline import _enhance_with_llm

        scored = [(_photo("no-cache-1"), 0.5)]

        with (
            # WHY: database unavailable
            patch(
                "immich_memories.photos.photo_pipeline._get_score_cache",
                return_value=None,
            ),
            # WHY: external LLM API
            patch(
                "immich_memories.photos.photo_pipeline._llm_score_photo",
                return_value=0.7,
            ) as mock_llm,
        ):
            result = _enhance_with_llm(scored, PhotoConfig(), Path("/tmp"), lambda *_args: None)

        assert result[0][1] == 0.7
        mock_llm.assert_called_once()

    def test_llm_failure_falls_back_to_metadata_score(self, tmp_path: Path):
        """When LLM/download fails, _llm_score_photo returns the metadata score."""
        from immich_memories.photos.photo_pipeline import _llm_score_photo

        asset = _photo("fail-1", exif_make="Apple")
        meta_score = 0.42

        def download_explodes(_id: str, _path: Path) -> None:
            msg = "network error"
            raise ConnectionError(msg)

        result = _llm_score_photo(asset, meta_score, PhotoConfig(), tmp_path, download_explodes)
        assert result == meta_score

    def test_llm_prepare_failure_falls_back(self, tmp_path: Path):
        """When prepare_photo_source fails, falls back to metadata score."""
        from immich_memories.photos.photo_pipeline import _llm_score_photo

        asset = _photo("fail-2")
        asset.original_file_name = "photo.jpg"
        meta_score = 0.55

        # Write a dummy file so download succeeds
        (tmp_path / "fail-2.jpg").write_bytes(b"not-a-real-image")

        with patch(
            "immich_memories.photos.photo_pipeline.prepare_photo_source",
            side_effect=RuntimeError("decode failed"),
        ):
            result = _llm_score_photo(
                asset, meta_score, PhotoConfig(), tmp_path, lambda *_args: None
            )

        assert result == meta_score

    def test_get_score_cache_returns_none_on_import_error(self):
        """_get_score_cache returns None when dependencies are unavailable."""
        from immich_memories.photos.photo_pipeline import _get_score_cache

        with (
            patch(
                "immich_memories.photos.photo_pipeline.VideoAnalysisCache",
                side_effect=ImportError("no module"),
                create=True,
            ),
            patch(
                "immich_memories.cache.database.VideoAnalysisCache",
                side_effect=ImportError("no module"),
            ),
        ):
            result = _get_score_cache()

        assert result is None
