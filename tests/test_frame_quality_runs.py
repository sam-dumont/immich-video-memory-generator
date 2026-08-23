"""The tie-break that reads the pixels has to be able to see them.

Metadata alone ties hundreds of photos onto a handful of values — measured
across four months, 227-648 photos collapsing onto 5-8 distinct scores, all
inside 0.24-0.48 — so "best N" means "first N of the largest group". #489
added a quality share to break that, and it only ever read a thumbnail cache
that, on a CLI run, nothing had filled: the re-weighting silently did nothing
while the log claimed quality decided a third of the score.
"""

from __future__ import annotations

import io

from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.photo_pipeline import _apply_frame_quality
from tests.conftest import make_asset


def _jpeg(*, blurry: bool) -> bytes:
    """A real, readable frame — sharp edges or a flat wash."""
    import numpy as np
    from PIL import Image

    if blurry:
        pixels = np.full((64, 64), 128, dtype="uint8")
    else:
        pixels = np.indices((64, 64))[1].astype("uint8") * 4
        pixels[::2] = 255 - pixels[::2]
    buffer = io.BytesIO()
    Image.fromarray(pixels, mode="L").convert("RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _tied_pool(count: int) -> list[tuple[object, float]]:
    return [(make_asset(f"photo-{i}"), 0.30) for i in range(count)]


def test_the_pixels_break_the_tie_when_nothing_cached_them(tmp_path) -> None:
    """On a CLI run the only cache writers are the video prefetcher and burst
    dedup, so nearly every photo arrives unmeasured and keeps the flat share.
    """
    pool = _tied_pool(6)
    frames = {asset.id: _jpeg(blurry=index % 2 == 0) for index, (asset, _score) in enumerate(pool)}

    class _EmptyCache:
        def get(self, _asset_id: str, _size: str) -> bytes | None:
            return None

    rescored = _apply_frame_quality(
        pool,
        PhotoConfig(),
        _EmptyCache(),
        thumbnail_fn=lambda asset_id, **_kw: frames[asset_id],
    )

    assert len({round(score, 6) for _asset, score in rescored}) > 1, (
        "every photo kept the same score; the pixels were never read"
    )


def test_a_cached_frame_is_not_fetched_again(tmp_path) -> None:
    """The cache exists so a warm run costs nothing."""
    pool = _tied_pool(4)
    sharp = _jpeg(blurry=False)
    blurry = _jpeg(blurry=True)

    class _WarmCache:
        def get(self, asset_id: str, _size: str) -> bytes:
            return sharp if asset_id.endswith(("0", "1")) else blurry

    fetched: list[str] = []

    rescored = _apply_frame_quality(
        pool,
        PhotoConfig(),
        _WarmCache(),
        thumbnail_fn=lambda asset_id, **_kw: fetched.append(asset_id) or sharp,
    )

    assert fetched == []
    assert len({round(score, 6) for _asset, score in rescored}) > 1


def test_without_a_way_to_fetch_it_still_uses_what_is_cached(tmp_path) -> None:
    """The old call signature keeps working; it just cannot reach further."""
    pool = _tied_pool(4)
    sharp = _jpeg(blurry=False)
    blurry = _jpeg(blurry=True)

    class _PartialCache:
        def get(self, asset_id: str, _size: str) -> bytes | None:
            return sharp if asset_id.endswith("0") else (blurry if asset_id.endswith("1") else None)

    rescored = _apply_frame_quality(pool, PhotoConfig(), _PartialCache())

    assert len({round(score, 6) for _asset, score in rescored}) > 1


def test_a_row_scored_by_an_older_formula_is_not_reused() -> None:
    """The key names the model and the prompt; the formula changes too.

    A warm-cache photo ranked on the pre-#489 formula while a cache-miss
    photo beside it ranked on the new one — two scoring regimes inside one
    selection, and no column to tell them apart.
    """
    from immich_memories.photos.scoring import _PHOTO_LOOK_VERSION, _photo_look_version

    key = _photo_look_version("some-model")

    assert key.startswith("some-model#")
    assert _photo_look_version("other-model") != key
    # Pinned on purpose: changing the prompt or the scoring weights without
    # bumping this silently mixes two regimes in one selection, and nothing
    # else in the system would notice. If this assertion fails, that is the
    # question being asked — bump it, do not edit the number here.
    assert _PHOTO_LOOK_VERSION == "look2"
