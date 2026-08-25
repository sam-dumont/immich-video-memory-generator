"""The look asks what a photo is OF, and then throws the answer away.

photos/scoring.py asks the model for a category — "exactly one of: people,
animal, landscape, object, screen" — because subject policy acts on that label
and refuses to guess from prose. asset_scores has held an llm_category column
all along. Nothing ever wrote it: save_asset_score has no such parameter, and
_payload_from_cache hard-codes category=None. Measured on a real library:
8,827 rows, every one NULL.

So a food-prep pan came back from cache as an uncategorised photograph, scored
0.426 against a 0.44 bar, and the policy that would have dropped it saw
UNKNOWN and let it through.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.cache.asset_score_cache import AssetScoreCache
from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.photos.scoring import PhotoLook, _payload_from_cache


class TestTheCategoryMakesTheRoundTrip:
    def test_a_saved_category_comes_back(self, tmp_path):
        # WHY the migrated schema rather than a hand-written CREATE TABLE:
        # a hand-copy drifts from the real one and cannot show a column bug.
        db_path = tmp_path / "scores.db"
        VideoAnalysisCache(db_path)
        cache = AssetScoreCache(db_path=db_path)

        cache.save_asset_score(
            asset_id="pan",
            asset_type="photo",
            metadata_score=0.4,
            combined_score=0.426,
            llm_category="object",
            model_version="m#look2",
        )
        rows = cache.get_asset_scores_batch(["pan"], model_version="m#look2")

        assert rows["pan"]["llm_category"] == "object"

    def test_the_payload_carries_it_to_the_pool(self):
        payload = _payload_from_cache({"llm_category": "object", "llm_interest": 0.6})

        assert payload["category"] == "object"


class TestARowThatCannotAnswerIsNotAHit:
    def test_a_category_less_row_is_looked_at_again(self, tmp_path):
        """Otherwise nothing changes: every existing row is NULL.

        A cached row is only as good as everything that produced it. This one
        predates the category being stored, so it cannot answer what the pool
        now asks and is not a hit.
        """
        from immich_memories.photos.scoring import _enhance_with_llm

        cache = MagicMock()
        cache.get_asset_scores_batch.return_value = {
            "pan": {
                "combined_score": 0.426,
                "llm_description": "a frying pan",
                "llm_category": None,
            }
        }
        asset = MagicMock(id="pan")
        looked = PhotoLook(score=0.42, payload={"category": "object"})

        with (
            # WHY: the score cache is a SQLite read.
            patch("immich_memories.photos.scoring._get_score_cache", return_value=cache),
            # WHY: the photo look is a VLM call over the network.
            patch("immich_memories.photos.scoring._llm_score_photo", return_value=looked) as look,
        ):
            _, payloads = _enhance_with_llm(
                [(asset, 0.4)],
                MagicMock(),
                tmp_path,
                MagicMock(),
                db_path=tmp_path / "c.db",
                app_config=MagicMock(),
            )

        look.assert_called_once()
        assert payloads["pan"]["category"] == "object"
