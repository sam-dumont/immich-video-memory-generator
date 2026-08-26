"""Selects reduces repetition it can prove, and refuses the judgement it cannot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

from PIL import Image

from immich_memories.analysis.selection_selects import run_selects
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from tests.conftest import make_asset

WHEN = datetime(2024, 2, 3, 12, tzinfo=UTC)


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), colour).save(output, "JPEG")
    return output.getvalue()


def run_selects_on(prepared):
    """In the live flow this is Cull's survivors; here every candidate reaches it."""
    return run_selects(prepared, prepared.candidates)


def _prepared(*assets):
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("navy"),
        ),
    )


def test_two_cameras_on_one_instant_leave_one_survivor() -> None:
    """Two devices at the same instant are one moment seen twice, not two pictures.

    Measured on a real dense month: 558 of 1468 candidates share an exact capture
    instant. No model is needed to find them.
    """
    result = run_selects_on(
        _prepared(
            make_asset("left-camera", file_created_at=WHEN),
            make_asset("right-camera", file_created_at=WHEN),
        )
    )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("left-camera",)
    assert result.absorbed[0].asset_id == "right-camera"
    assert result.absorbed[0].kept_asset_id == "left-camera"


def test_a_second_apart_is_a_different_photograph() -> None:
    """The absorbing rule is exact instants only, and this is why.

    Measured on real pixels: two frames 7.6 seconds apart in one place, one
    subject, were two different pictures of a fast-moving event -- and the model
    said so. Any similarity or time-window rule merges them and destroys the
    sequence. Arithmetic is only allowed the part it can prove.
    """
    result = run_selects_on(
        _prepared(
            make_asset("first", file_created_at=WHEN),
            make_asset("a-second-later", file_created_at=WHEN + timedelta(seconds=1)),
        )
    )

    assert len(result.survivors) == 2
    assert result.absorbed == ()


def test_one_instant_in_two_places_is_two_threads_not_one_picture() -> None:
    """Two devices far apart at one time are parallel threads, which is the point.

    Measured on a real day: a racing circuit at 16:37 and a house 120km away at
    16:49. Absorbing by instant alone would fold two people's separate days into
    each other, so the rule has to live inside a moment, which is bounded by
    place as well as time.
    """
    here = make_asset("at-the-circuit", file_created_at=WHEN)
    here.exif_info.latitude, here.exif_info.longitude = 50.44, 5.97
    far = make_asset("at-the-house", file_created_at=WHEN)
    far.exif_info.latitude, far.exif_info.longitude = 51.21, 4.42

    result = run_selects_on(_prepared(here, far))

    assert len(result.survivors) == 2
    assert result.absorbed == ()


def test_the_favourite_survives_its_own_instant() -> None:
    """The star settles it here as it settles every other hard gate."""
    result = run_selects_on(
        _prepared(
            make_asset("plain", file_created_at=WHEN),
            make_asset("starred", file_created_at=WHEN, is_favorite=True),
        )
    )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("starred",)
    assert result.absorbed[0].asset_id == "plain"
