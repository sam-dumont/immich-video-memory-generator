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
            work_dir=tmp_path,
            download_fn=None,
            db_path=config.cache.database_path,
            app_config=config,
        )

    line = next(m for m in caplog.messages if m.startswith("Photo scoring:"))
    shortlisted = int(line.split("->")[2].split()[0])
    assert shortlisted == LLM_SHORTLIST_CEILING


def test_grouping_is_shared_with_whatever_reads_a_moment() -> None:
    """The groups themselves are the unit, not just their representatives.

    Reading a moment from contact sheets needs every member of a moment, not
    the one photo that stands in for it — so the grouping is named separately
    and one_photo_per_moment picks from what it returns. Two callers, one
    definition of what a moment is.
    """
    from immich_memories.photos.photo_pipeline import moments_of, one_photo_per_moment

    burst = [_photo(f"b{i}", minute=i, score=0.5 + i / 100) for i in range(4)]
    later = [_photo("later", minute=200, score=0.9)]
    groups = moments_of(burst + later, 10.0)

    assert [len(g) for g in groups] == [4, 1]
    assert [item[0].id for item in one_photo_per_moment(burst + later, 10.0)] == [
        max(g, key=lambda item: (bool(item[0].is_favorite), item[1]))[0].id for g in groups
    ]


class TestReadingMomentsInsteadOfSamplingThem:
    """With read_moments on, the shortlist is what the model saw happening.

    Sampling one photo per moment still lets metadata choose WHICH photo
    represents the moment. Reading the moment hands that choice to the thing
    that can actually see it.
    """

    def _jpeg(self):
        """A real JPEG: the reading skips frames that will not open."""
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), (80, 90, 100)).save(buffer, "JPEG")
        return buffer.getvalue()

    def _assets(self, n):
        from datetime import UTC, datetime, timedelta

        from tests.conftest import make_asset

        out = []
        for i in range(n):
            asset = make_asset(f"p{i}", exif_make="Apple", duration=None)
            asset.file_created_at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=i)
            out.append(asset)
        return out

    def test_the_shortlist_is_what_the_reading_kept(self, tmp_path) -> None:
        from unittest.mock import patch

        from immich_memories.analysis.moment_reading import MomentReading
        from immich_memories.config import Config
        from immich_memories.photos.photo_pipeline import score_photos

        assets = self._assets(6)
        config = Config(
            cache={"database": str(tmp_path / "c.db"), "directory": str(tmp_path)},
            photos={"read_moments": True},
            content_analysis={"enabled": True},
        )
        chosen = [assets[4]]

        # WHY: the reading is the model's answer; this asserts what selection
        # does with it, not how it was obtained.
        with (
            # WHY: the reading is the model's answer about a moment.
            patch(
                "immich_memories.analysis.moment_reading.read_moment",
                return_value=MomentReading(about="a walk", subjects=(), keep=tuple(chosen)),
            ),
            # WHY: the per-photo look is the expensive call this pass decides what to spend on; ca
            patch(
                "immich_memories.photos.photo_pipeline._enhance_with_llm",
                side_effect=lambda shortlist, *_a, **_k: (shortlist, {}),
            ) as enhanced,
        ):
            score_photos(
                assets,
                config.photos,
                work_dir=tmp_path,
                download_fn=None,
                thumbnail_fn=lambda _id, **_kw: self._jpeg(),
                db_path=config.cache.database_path,
                app_config=config,
            )

        looked_at = [asset.id for asset, _score in enhanced.call_args.args[0]]
        assert looked_at == ["p4"]

    def test_off_by_default_keeps_sampling(self, tmp_path) -> None:
        """The flag is the whole difference; nothing changes until it is set."""
        from unittest.mock import patch

        from immich_memories.config import Config
        from immich_memories.photos.photo_pipeline import score_photos

        assets = self._assets(6)
        config = Config(cache={"database": str(tmp_path / "c.db"), "directory": str(tmp_path)})

        with (
            # WHY: the reading is the model's answer; with the flag off it must never be asked for
            patch("immich_memories.analysis.moment_reading.read_moment") as never,
            # WHY: the per-photo look is the expensive call downstream of it.
            patch(
                "immich_memories.photos.photo_pipeline._enhance_with_llm",
                side_effect=lambda shortlist, *_a, **_k: (shortlist, {}),
            ),
        ):
            score_photos(
                assets,
                config.photos,
                work_dir=tmp_path,
                download_fn=None,
                db_path=config.cache.database_path,
                app_config=config,
            )

        never.assert_not_called()


class TestASheetShowsTheWholeMoment:
    """A moment is not only its photographs.

    On one real day the photo pool held 3 assets while the moment held 133 —
    the rest were video and Live Photos. A sheet built from the pool alone
    describes a fragment and then judges from it: photos-only read nothing of
    that day, while the whole moment named the event, the circuit and the cars.

    So everything in the moment goes on the sheet, and only the candidates can
    be chosen from it.
    """

    def _jpeg(self):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), (90, 90, 90)).save(buffer, "JPEG")
        return buffer.getvalue()

    def _asset(self, name: str, minutes: int):
        from datetime import UTC, datetime, timedelta

        from tests.conftest import make_asset

        asset = make_asset(name, exif_make="Apple", duration=None)
        asset.file_created_at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes)
        return asset

    def test_the_sheet_is_tiled_from_candidates_and_context_alike(self, tmp_path) -> None:
        from unittest.mock import patch

        from immich_memories.analysis.moment_reading import MomentReading
        from immich_memories.config import Config
        from immich_memories.photos.photo_pipeline import score_photos

        photos = [self._asset(f"p{i}", i) for i in range(2)]
        alongside = [self._asset(f"v{i}", i) for i in range(4)]
        config = Config(
            cache={"database": str(tmp_path / "c.db"), "directory": str(tmp_path)},
            photos={"read_moments": True},
            content_analysis={"enabled": True},
        )

        seen: list[int] = []

        def _record(assets, frames, *_a, **_k):
            seen.append(len(assets))
            return MomentReading(about="a day", subjects=(), keep=(photos[1],))

        with (
            # WHY: the model is the external boundary; this asserts what it is shown.
            patch("immich_memories.analysis.moment_reading.read_moment", side_effect=_record),
            # WHY: the per-photo look is the expensive call the sheet decides on.
            patch(
                "immich_memories.photos.photo_pipeline._enhance_with_llm",
                side_effect=lambda shortlist, *_a, **_k: (shortlist, {}),
            ) as enhanced,
        ):
            score_photos(
                photos,
                config.photos,
                work_dir=tmp_path,
                download_fn=None,
                thumbnail_fn=lambda _id, **_kw: self._jpeg(),
                db_path=config.cache.database_path,
                app_config=config,
                alongside=alongside,
            )

        assert seen == [6], "the sheet must show the whole moment, not just the candidates"
        looked_at = [asset.id for asset, _score in enhanced.call_args.args[0]]
        assert looked_at == ["p1"], "only candidates can be chosen from it"
