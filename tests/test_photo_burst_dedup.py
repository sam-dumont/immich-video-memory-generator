"""One photo per burst, not five.

Measured on a real June pool: 64 of 303 photos are near-duplicates of another
shot within five minutes -- 21% of the pool, in groups of up to five. Nothing
removed them. `deduplicate_by_thumbnails` exists but is only ever called on
video clips, and it clusters on hash alone with no time bound, so reusing it
here would merge the same room photographed months apart.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from immich_memories.photos.burst_dedup import PhotoCandidate, drop_burst_duplicates

BASE = datetime(2026, 6, 14, 11, 0, 0)
SCENE = "ff00ff00ff00ff00"
SCENE_SHIFTED = "ff00ff00ff00ff03"  # 2 bits away — the next frame of the same burst
OTHER = "00ff00ff00ff00ff"  # 64 bits away


def photo(key: str, offset: float, digest: str | None, score: float = 0.5) -> PhotoCandidate:
    return PhotoCandidate(
        key=key, taken_at=BASE + timedelta(seconds=offset), thumbnail_hash=digest, score=score
    )


def test_a_burst_collapses_to_its_best_frame() -> None:
    kept = drop_burst_duplicates(
        [
            photo("a", 0, SCENE, score=0.4),
            photo("b", 1.5, SCENE_SHIFTED, score=0.9),
            photo("c", 3.0, SCENE, score=0.6),
        ],
        window_seconds=300,
        hash_threshold=8,
    )

    assert kept == ["b"]


def test_the_same_scene_on_another_day_is_not_a_burst() -> None:
    """A kitchen photographed twice in a month is two memories."""
    kept = drop_burst_duplicates(
        [photo("monday", 0, SCENE), photo("friday", 4 * 86400, SCENE)],
        window_seconds=300,
        hash_threshold=8,
    )

    assert kept == ["monday", "friday"]


def test_different_subjects_seconds_apart_both_survive() -> None:
    kept = drop_burst_duplicates(
        [photo("cake", 0, SCENE), photo("faces", 2, OTHER)],
        window_seconds=300,
        hash_threshold=8,
    )

    assert kept == ["cake", "faces"]


def test_a_photo_without_a_hash_is_kept() -> None:
    """Redundancy is measured, never assumed -- the same rule the moment filter
    follows. A missing thumbnail must not delete the photo."""
    kept = drop_burst_duplicates(
        [photo("hashed", 0, SCENE), photo("unhashed", 1, None)],
        window_seconds=300,
        hash_threshold=8,
    )

    assert "unhashed" in kept


def test_input_order_survives() -> None:
    kept = drop_burst_duplicates(
        [photo("x", 0, SCENE), photo("y", 9999, OTHER), photo("z", 20000, SCENE)],
        window_seconds=300,
        hash_threshold=8,
    )

    assert kept == ["x", "y", "z"]
