"""Describe one photo per moment, not three per shippable slot.

Measured on a real 295-photo month:

    Photo scoring: 295 available -> 3 shortlisted for LLM (max 1 selectable)

The shortlist was sized from how many photos the memory could SHIP, so the
pipeline only ever described what it had already decided it might use — and
it decided that on metadata alone. Metadata picks the shortlist, the VLM only
sees the shortlist, so content can only confirm the metadata choice. A better
photo ranked fourth by metadata was never looked at and could never win.

One per moment breaks that: every moment gets exactly one content look, so
nothing is skipped at the start.

This bounds the looking, not the selecting — score_photos still returns every
photo — so it does not by itself make same-moment duplicates impossible. On a
real month the duplicate pair did disappear, but through better ranking rather
than by construction.

A favourite is its moment's representative. The user already said this one
mattered.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from immich_memories.photos.photo_pipeline import one_photo_per_moment


def _photo(asset_id: str, minute: int, score: float, favorite: bool = False):
    return (
        SimpleNamespace(
            id=asset_id,
            is_favorite=favorite,
            file_created_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        ),
        score,
    )


def test_a_burst_contributes_one_representative() -> None:
    """Five shots inside a minute are one moment, so one look."""
    burst = [_photo(f"burst-{i}", minute=i, score=0.5 + i / 100) for i in range(5)]

    chosen = one_photo_per_moment(burst, window_minutes=10.0)

    assert len(chosen) == 1


def test_distinct_moments_each_get_a_look() -> None:
    """Nothing is skipped at the start — that is the whole point."""
    spread = [_photo(f"p-{i}", minute=i * 60, score=0.5) for i in range(6)]

    chosen = one_photo_per_moment(spread, window_minutes=10.0)

    assert len(chosen) == 6


def test_the_favorite_represents_its_moment() -> None:
    """The user already told us which one of these mattered."""
    group = [
        _photo("plain-high-score", minute=0, score=0.9),
        _photo("the-favorite", minute=1, score=0.2, favorite=True),
    ]

    chosen = one_photo_per_moment(group, window_minutes=10.0)

    assert [a.id for a, _ in chosen] == ["the-favorite"]


def test_without_a_favorite_the_best_metadata_score_represents() -> None:
    group = [
        _photo("weaker", minute=0, score=0.3),
        _photo("stronger", minute=1, score=0.8),
    ]

    chosen = one_photo_per_moment(group, window_minutes=10.0)

    assert [a.id for a, _ in chosen] == ["stronger"]


def test_a_library_of_moments_is_still_bounded(tmp_path, caplog) -> None:
    """More moments than the ceiling must not become more calls than the ceiling.

    Sizing by moment removes the old ship-count bound, so the absolute ceiling
    is now the only thing standing between a large library and thousands of
    LLM calls. It carries the guarantee the removed llm_shortlist_size used to
    make, and it is the reason that constant survives.
    """
    import logging

    from immich_memories.config_loader import Config
    from immich_memories.photos.photo_pipeline import LLM_SHORTLIST_CEILING, score_photos
    from tests.conftest import make_asset

    far_apart = [
        make_asset(f"m{i}", is_favorite=False, exif_make="Apple", duration=None)
        for i in range(LLM_SHORTLIST_CEILING + 60)
    ]
    for i, asset in enumerate(far_apart):
        asset.file_created_at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i)

    config = Config(
        cache={"database": str(tmp_path / "cache.db"), "directory": str(tmp_path)},
        content_analysis={"enabled": False},
    )
    with caplog.at_level(logging.INFO):
        score_photos(
            far_apart,
            config.photos,
            video_clip_count=0,
            work_dir=tmp_path,
            download_fn=None,
            db_path=config.cache.database_path,
            app_config=config,
        )

    line = next(m for m in caplog.messages if m.startswith("Photo scoring:"))
    shortlisted = int(line.split("->")[2].split()[0])
    assert shortlisted == LLM_SHORTLIST_CEILING
