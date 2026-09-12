"""The Structure workprint represents every surviving moment without selecting the cut."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image

from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.visual_atlas import build_visual_atlas
from tests.conftest import make_asset

WHEN = datetime(2024, 6, 1, 12, tzinfo=UTC)


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 48), colour).save(output, "JPEG")
    return output.getvalue()


def _stripe(columns: int) -> bytes:
    image = Image.new("L", (8, 8), 0)
    for x in range(columns):
        for y in range(8):
            image.putpixel((x, y), 255)
    image = image.resize((64, 64), Image.Resampling.NEAREST).convert("RGB")
    output = BytesIO()
    image.save(output, "JPEG", quality=100, subsampling=0)
    return output.getvalue()


def test_workprint_conserves_cull_survivors_behind_one_proxy_per_moment(
    tmp_path: Path,
) -> None:
    """A representative changes the view, never the membership reaching Structure."""
    from immich_memories.analysis.selection_structure import build_structure_workprint

    pixels = {"first": _jpeg("red"), "culled": _jpeg("green"), "later": _jpeg("blue")}
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("first", file_created_at=WHEN),
                make_asset("culled", file_created_at=WHEN + timedelta(minutes=1)),
                make_asset("later", file_created_at=WHEN + timedelta(hours=1)),
            ),
            preview_jpeg=lambda asset: pixels[asset.id],
        ),
    )
    admitted = tuple(
        candidate for candidate in prepared.candidates if candidate.asset_id != "culled"
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=None)

    workprint = build_structure_workprint(
        prepared,
        admitted,
        atlas=atlas,
        output_dir=tmp_path / "workprint",
    )

    assert workprint.representative_ids == ("first", "later")
    assert tuple(asset_id for moment in workprint.moments for asset_id in moment.candidate_ids) == (
        "first",
        "later",
    )
    assert tuple(moment.representative.asset_id for moment in workprint.moments) == (
        "first",
        "later",
    )
    assert tuple(ref.entity_id for page in workprint.pages for ref in page.tile_refs) == (
        "first",
        "later",
    )


def test_workprint_uses_the_visual_medoid_as_a_moments_proxy() -> None:
    """The proxy covers its moment; it does not claim to be that moment's best frame."""
    from immich_memories.analysis.selection_structure import build_structure_workprint

    pixels = {
        "left": _stripe(1),
        "central": _stripe(3),
        "right": _stripe(5),
    }
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=WHEN + timedelta(minutes=index))
                for index, asset_id in enumerate(("left", "central", "right"))
            ),
            preview_jpeg=lambda asset: pixels[asset.id],
        ),
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=None)

    workprint = build_structure_workprint(prepared, prepared.candidates, atlas=atlas)

    assert workprint.representative_ids == ("central",)
    assert workprint.moments[0].candidate_ids == ("left", "central", "right")


def test_a_viewable_favourite_overrides_the_visual_medoid() -> None:
    """The owner's mark decides which member stands for the moment on the workprint."""
    from immich_memories.analysis.selection_structure import build_structure_workprint

    pixels = {
        "left": _stripe(1),
        "central": _stripe(3),
        "starred": _stripe(5),
    }
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("left", file_created_at=WHEN),
                make_asset("central", file_created_at=WHEN + timedelta(minutes=1)),
                make_asset(
                    "starred",
                    file_created_at=WHEN + timedelta(minutes=2),
                    is_favorite=True,
                ),
            ),
            preview_jpeg=lambda asset: pixels[asset.id],
        ),
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=None)

    workprint = build_structure_workprint(prepared, prepared.candidates, atlas=atlas)

    assert workprint.representative_ids == ("starred",)
    assert workprint.moments[0].candidate_ids == ("left", "central", "starred")
