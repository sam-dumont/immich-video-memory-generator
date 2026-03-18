"""Tests for photo scoring."""

from __future__ import annotations

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
