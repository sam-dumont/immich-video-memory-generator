"""The score has to be able to tell two photos apart (#489).

Measured on four real months, the metadata score produced 5-8 distinct values
for 227-648 photos, always inside 0.24-0.48, with up to 67% of a month sharing
one number. Inside one 232-photo tie group, sharpness spanned 4.1 to 60.1.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from immich_memories.photos.frame_quality import measure, rank


def _jpeg(pixels: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(pixels.astype("uint8")).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _noise(seed: int, size: int = 96) -> np.ndarray:
    return (
        np.random.default_rng(seed)
        .integers(0, 255, size=(size, size), dtype=np.int16)
        .astype("uint8")
    )


def test_a_sharp_photo_measures_sharper_than_a_flat_one() -> None:
    detailed = measure(_jpeg(_noise(1)))
    flat = measure(_jpeg(np.full((96, 96), 128)))

    assert detailed is not None and flat is not None
    assert detailed.sharpness > flat.sharpness


def test_a_crushed_photo_loses_exposure() -> None:
    """Beyond a point no amount of detail makes a photo worth showing."""
    nearly_black = measure(_jpeg(np.full((96, 96), 4)))
    mid_grey = measure(_jpeg(np.full((96, 96), 118)))

    assert nearly_black is not None and mid_grey is not None
    assert nearly_black.exposure < mid_grey.exposure


def test_an_unreadable_thumbnail_costs_the_photo_its_measurements_not_the_run() -> None:
    assert measure(b"not a jpeg") is None


def test_rank_spreads_a_pool_across_the_whole_range() -> None:
    ranked = rank([5.0, 1.0, 3.0, 9.0])

    assert min(ranked) == 0.0
    assert max(ranked) == 1.0
    assert ranked[3] > ranked[0] > ranked[1]


def test_tied_values_share_a_position_rather_than_deciding_an_order() -> None:
    ranked = rank([2.0, 2.0, 2.0, 9.0])

    assert ranked[0] == ranked[1] == ranked[2]
    assert ranked[3] > ranked[0]


@pytest.mark.parametrize("pool", [[], [7.0]])
def test_rank_handles_a_pool_too_small_to_order(pool: list[float]) -> None:
    assert len(rank(pool)) == len(pool)


def test_frame_quality_breaks_a_metadata_tie_group() -> None:
    """The behaviour that matters: a tied pool comes out ordered.

    Without this, "best N photos" means "first N of the largest tie group",
    in whatever order the API happened to return them.
    """
    from immich_memories.photos.photo_pipeline import _apply_frame_quality

    class _Thumbnails:
        def __init__(self, data: dict[str, bytes]) -> None:
            self._data = data

        def get(self, asset_id: str, _size: str) -> bytes | None:
            return self._data.get(asset_id)

    class _Asset:
        def __init__(self, asset_id: str) -> None:
            self.id = asset_id

    assets = [_Asset(f"a{i}") for i in range(4)]
    scored = [(a, 0.4267) for a in assets]  # the real tie value from 2007-08
    thumbs = _Thumbnails(
        {
            "a0": _jpeg(np.full((96, 96), 128)),  # flat
            "a1": _jpeg(_noise(2)),  # detailed
            "a2": _jpeg(np.full((96, 96), 3)),  # crushed
            "a3": _jpeg(_noise(3) // 2 + 60),  # detailed, mid exposure
        }
    )

    rescored = _apply_frame_quality(scored, thumbs)

    scores = [s for _a, s in rescored]
    assert len(set(scores)) == len(scores), "a tie group must come out ordered"
    by_id = {a.id: s for a, s in rescored}
    assert by_id["a1"] > by_id["a2"], "detail should beat a crushed frame"
