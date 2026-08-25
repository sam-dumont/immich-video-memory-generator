"""One scoring method everywhere — photographs judged by the rules video uses.

#677 made a video's score content-first: what it shows, adjusted by how well it
was shot. Photographs never made the trip, so they still run the additive
weighted sum #677 replaced — which is why a face is worth what the owner
starring the photograph is worth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.asset_merit import ranking_key
from immich_memories.api.models import Person
from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.scoring import score_photo
from tests.conftest import make_asset


def _photo(asset_id: str, *, is_favorite: bool = False, faces: int = 0):
    asset = make_asset(asset_id, is_favorite=is_favorite, exif_make="Apple", duration=None)
    asset.people = [Person(id=f"{asset_id}-{n}", name=f"Person {n}") for n in range(faces)]
    return asset


class TestFacesAreNotWorthAFavourite:
    """The owner's mark must outrank a crowd of strangers."""

    def test_the_owners_mark_outranks_a_crowd(self):
        """A starred photograph beats an unstarred one full of faces.

        Measured before the change: both score exactly 0.480000. Faces carry
        0.15 + 0.10 and a favourite carries 0.25, so three strangers buy
        precisely what the owner starring it buys, and the winner is whichever
        order the API happened to return.
        """
        config = PhotoConfig()
        starred = _photo("starred", is_favorite=True)
        crowd = _photo("crowd", faces=3)

        best = max([crowd, starred], key=lambda a: ranking_key(a, score_photo(a, config)))

        assert best is starred

    def test_the_star_is_not_also_a_number(self):
        """Merit measures the photograph; the star orders it.

        Kept in both places a favourite counts twice, and the number it adds is
        the one three strangers matched. Video has never scored a favourite —
        it sorts on one — so this is the photograph adopting the video method.
        """
        config = PhotoConfig()

        starred = score_photo(_photo("starred", is_favorite=True), config)
        plain = score_photo(_photo("plain"), config)

        assert starred == plain


class TestTheStarSurvivesEveryNarrowing:
    """Wherever photographs are narrowed on score, the star must still win."""

    def test_a_favourite_survives_its_burst(self):
        """A held shutter that includes the starred frame keeps the star.

        Burst de-duplication keeps one frame per burst, chosen on score. Once
        the star stops being a number in that score, choosing on the number
        alone drops the one frame the owner said mattered.
        """
        from immich_memories.photos.burst_dedup import PhotoCandidate, drop_burst_duplicates

        moment = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        burst = [
            PhotoCandidate(key="sharp", taken_at=moment, thumbnail_hash="f" * 16, score=0.9),
            PhotoCandidate(
                key="starred",
                taken_at=moment + timedelta(seconds=1),
                thumbnail_hash="f" * 16,
                score=0.4,
                is_favorite=True,
            ),
        ]

        kept = drop_burst_duplicates(burst, window_seconds=5.0, hash_threshold=10)

        assert kept == ["starred"]


class TestOnePoolOneScale:
    """A photograph is not marked down for being a photograph."""

    def test_a_plain_photograph_clears_the_judges_floor_unaided(self):
        """One scale, one gate, no compensating discount.

        score_penalty scaled every still to 80% so footage won ties while the
        two ranked in separate budgets. A no-people, non-favourite photograph
        then scored 0.28 against a judge floor of 0.30 and could not clear it,
        so landscapes, pets and scenery were dropped as a class -- and the
        floor itself had to be discounted for stills to undo it. A second
        mechanism whose only job is to cancel a first.

        Undiscounted the same photograph scores 0.35 and clears 0.30 on its
        own, which is what lets the discounted floor go.
        """
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        config = PhotoConfig()

        assert score_photo(_photo("landscape"), config) > PipelineConfig().judge_floor_score
