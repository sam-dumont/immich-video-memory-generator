"""What the episode scan is told about each visual, beyond its pixels."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from PIL import Image

from immich_memories.analysis.episode_scan_request import build_episode_request
from immich_memories.analysis.period_insight import run_period_insight
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.api.models import Asset, AssetType, ExifInfo, Person


def _asset(
    asset_id: str,
    *,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    people: tuple[Person, ...] = (),
    taken: datetime | None = None,
) -> Asset:
    when = taken or datetime(2026, 4, 1, 12, tzinfo=UTC)
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        isFavorite=False,
        originalFileName=f"{asset_id}.HEIC",
        exifInfo=ExifInfo(
            make="Apple",
            model="iPhone 15 Pro",
            city=city,
            state=state,
            country=country,
        ),
        people=list(people),
        duration=None,
    )


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), "navy").save(output, "JPEG")
    return output.getvalue()


class _Silent:
    """A provider that answers nothing; this suite reads the REQUEST, not the reply."""

    def ask(self, _request):
        raise TimeoutError("generated timeout")


def _request_for(assets, tmp_path):
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg(),
        ),
    )
    result = run_period_insight(
        prepared,
        requester=_Silent(),
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
    )
    return build_episode_request(result.episode_packs[0], limits=VisionRequestLimits())


def test_the_scan_is_told_where_a_visual_was_taken(tmp_path) -> None:
    """Place is grounded fact and the strongest free context Immich returns.

    `selection_review._place_for_llm` records why the city/state/country form is
    the one that reasons: a caption says "Paradise" because that reads well on
    screen, while Paradise and Winchester are two Las Vegas Strip townships that
    without the state look like unrelated villages instead of one trip.
    """
    request = _request_for(
        (_asset("a", city="Jette", state="Brussels", country="Belgium"),), tmp_path
    )

    assert "place:Jette, Brussels, Belgium" in request.grounded_annotations[0]


def test_a_visual_with_no_place_says_so_rather_than_inventing_one(tmp_path) -> None:
    request = _request_for((_asset("a"),), tmp_path)

    assert "place:" not in request.grounded_annotations[0]


def test_the_scan_is_told_who_immich_recognised(tmp_path) -> None:
    """A count collapsed to one word cannot tell one afternoon from another.

    `subject-evidence:people` is the whole of it today, so a month of rides with
    a partner and a month of rides alone are described identically.
    """
    request = _request_for(
        (
            _asset(
                "a",
                people=(
                    Person(id="p1", name="Ada"),
                    Person(id="p2", name="Bo"),
                    Person(id="p3", name=""),
                ),
            ),
        ),
        tmp_path,
    )

    annotation = request.grounded_annotations[0]
    assert "people:Ada, Bo" in annotation
    assert "faces:3" in annotation


def test_unnamed_faces_are_counted_and_never_guessed(tmp_path) -> None:
    """An unnamed cluster is evidence someone is there, not evidence of who."""
    request = _request_for(
        (_asset("a", people=(Person(id="p1", name=""), Person(id="p2", name=""))),),
        tmp_path,
    )

    annotation = request.grounded_annotations[0]
    assert "faces:2" in annotation
    assert "people:" not in annotation
