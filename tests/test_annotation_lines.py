"""Annotation lines combine live source truth with one stored fact surface.
The source-preparation variants arrive with the slice that ports `selection_source`.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.api.models import ExifInfo, Person
from immich_memories.store.asset_annotations import (
    AssetAnnotationFactBatch,
    StoredAssetAnnotationFacts,
)
from tests.conftest import make_asset

CAPTURED = datetime(2026, 8, 25, 12, tzinfo=UTC)


@dataclass(frozen=True)
class OwnerPersonContext:
    """Stand-in for whatever the caller hands the reader as owner-reviewed context.

    The reader only reads the three attributes off a structural protocol, so the
    test supplies its own record rather than an exported class nothing in the
    product constructs.
    """

    relationship: str | None = None
    tier: str | None = None
    birth_date: date | None = None


_SCHEMA = """
CREATE TABLE asset_people (asset_id, person_name, person_id, birth_date);
CREATE TABLE descriptions (asset_id, model, text);
CREATE TABLE description_fields (asset_id, model, field, value);
CREATE TABLE flags (asset_id, flag, evidence, source);
CREATE TABLE head_facts (asset_id, head, version, label);
CREATE TABLE pixel_facts (asset_id, producer_key, sharpness, brightness, contrast,
    dark_fraction, bright_fraction, needs_rotation);
CREATE TABLE pixel_facts_thresholds (name, value, producer_key);
CREATE TABLE motion_bursts (asset_id, burst_id, still_ids, duration_seconds, beats_a_still);
"""


def store_with(tmp_path: Path, **tables: tuple[tuple, ...]) -> Path:
    store_path = tmp_path / "annotations.sqlite"
    with sqlite3.connect(store_path) as connection:
        connection.executescript(_SCHEMA)
        for table, rows in tables.items():
            placeholders = ",".join("?" for _ in rows[0])
            # Only the table name from this file's own keyword is interpolated.
            connection.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",  # noqa: S608
                rows,
            )
    return store_path


def candidate(asset_id: str, **overrides) -> EditorialCandidate:
    source = make_asset(asset_id, file_created_at=CAPTURED).model_copy(
        update={
            "exif_info": overrides.pop("exif", None),
            "people": list(overrides.pop("people", ())),
            "live_photo_video_id": overrides.pop("live_photo_video_id", None),
        }
    )
    fields = {
        "media_kind": "photo",
        "live_photo_stitch_member_ids": (),
        "rendering_family_id": None,
        "favourite": False,
        "shippable_duration": 0.0,
        "grounded_annotations": (),
        **overrides,
    }
    return EditorialCandidate(asset_id=asset_id, taken_at=CAPTURED, source=source, **fields)


def reader(store_path: Path, *candidates: EditorialCandidate, **overrides):
    return StoredAnnotationLineReader(
        store_path=store_path,
        candidates=candidates,
        description_model="student-v1",
        head_versions=overrides.pop("head_versions", {"activity": "public-v1"}),
        pixel_producer_key="pixel-v1",
        **overrides,
    )


def test_a_line_renders_every_available_fact_without_stable_asset_ids(tmp_path: Path) -> None:
    """Everything the editor may read, and nothing that could identify the library."""
    asset = "asset-private-001"
    store_path = store_with(
        tmp_path,
        asset_people=((asset, "Stale Name", "person-private-001", "1999-01-01"),),
        descriptions=((asset, "student-v1", "A runner crosses a city street."),),
        description_fields=(
            (asset, "student-v1", "setting", "outdoor road race"),
            (asset, "student-v1", "exposure", "underexposed"),
        ),
        flags=(
            (asset, "screen", '{"reason":"computer display"}', "docling"),
            (asset, "dark", '{"reason":"low exposure"}', "exposure"),
        ),
        head_facts=((asset, "activity", "public-v1", "sport-active"),),
        pixel_facts=((asset, "pixel-v1", 5.0, 20.0, 10.0, 0.7, 0.0, 1),),
        pixel_facts_thresholds=(("sharpness_p10", 10.0, "pixel-v1"),),
        motion_bursts=((asset, "burst-1", '["still-a","still-b"]', 4.4, 1),),
    )
    subject = candidate(
        "asset-private-001",
        media_kind="live_photo",
        favourite=True,
        exif=ExifInfo(city="Brussels", state="Brussels", country="Belgium"),
        live_photo_video_id="component-private-001",
        people=(
            Person(
                id="person-private-001",
                name="Robin",
                birthDate=datetime(2000, 2, 1, tzinfo=UTC),
            ),
        ),
        grounded_annotations=("live-photo-stitch-members:2", "duration:10.000s"),
    )

    batch = reader(
        store_path,
        subject,
        people_context={
            "person-private-001": OwnerPersonContext(relationship="friend", tier="inner")
        },
    ).lines_for(("asset-private-001",))

    assert batch.missing_asset_ids == ()
    assert batch.contract.producer_versions == (
        "description:student-v1",
        "flags:all-except-exposure-v2",
        "head:activity:public-v1",
        "motion-bursts:legacy-v1",
        "people:immich-live+owner-context-v1",
        "pixel:pixel-v1",
        "source:editorial-candidate-v1",
    )
    line = batch.as_mapping()["asset-private-001"]
    assert "asset-private-001" not in line
    assert "component-private-001" not in line
    assert "Stale Name" not in line
    assert "2026-08-25 12:00" in line
    assert "LIVE PHOTO BURST of 2 stills, stitches to a 4s clip" in line
    assert "A runner crosses a city street." in line
    assert "setting: outdoor road race" in line
    assert "exposure: underexposed" in line
    assert "at Brussels, Brussels, Belgium" in line
    assert "Robin (friend; aged 26; inner circle)" in line
    assert "activity=sport-active" in line
    assert "STARRED by the photographer" in line
    assert "FLAGGED screen (computer display)" in line
    assert "FLAGGED dark" not in line
    assert "SOFT (blurry)" in line
    assert "DARK" in line
    assert "rotated" in line
    assert "duration:10.000s" in line
    assert "live-photo-stitch-members" not in line
    assert batch.records_by_id()["asset-private-001"].stitching_burst_id == "burst-1"


@pytest.mark.parametrize(
    "born,expected",
    [
        ("2026-09-01", "capture predates recorded birth date"),
        ("2026-08-24", "newborn"),
        ("2026-07-25", "31 days old"),
        ("2025-08-25", "12 months old"),
        ("1990-08-26", "aged 35"),
    ],
)
def test_a_persons_age_is_stated_at_the_precision_the_capture_deserves(
    tmp_path: Path, born: str, expected: str
) -> None:
    """A newborn and a thirty-five-year-old are not the same editorial fact."""
    store_path = store_with(
        tmp_path,
        asset_people=(("asset-private-002", "Robin", "person-private-002", born),),
    )

    batch = reader(store_path, candidate("asset-private-002")).lines_for(("asset-private-002",))

    assert f"Robin ({expected})" in batch.as_mapping()["asset-private-002"]


@pytest.mark.parametrize(
    "pixels,expected",
    [
        ((50.0, 250.0, 10.0, 0.0, 0.0, 0), ("BLOWN OUT",)),
        ((5.0, 120.0, 10.0, 0.0, 0.9, 0), ("SOFT (blurry)", "BLOWN OUT")),
        ((50.0, 120.0, 10.0, 0.0, 0.0, 0), ()),
    ],
)
def test_pixel_warnings_only_fire_on_the_measurement_that_earns_them(
    tmp_path: Path, pixels: tuple, expected: tuple[str, ...]
) -> None:
    store_path = store_with(
        tmp_path,
        pixel_facts=(("a-picture", "pixel-v1", *pixels),),
        pixel_facts_thresholds=(("sharpness_p10", 10.0, "pixel-v1"),),
    )

    line = reader(store_path, candidate("a-picture")).lines_for(("a-picture",)).lines[0].text

    warned = tuple(w for w in ("SOFT (blurry)", "DARK", "BLOWN OUT") if w in line)
    assert warned == expected


@pytest.mark.parametrize(
    "kind,duration,motion,expected",
    [
        ("video", 12.0, None, "VIDEO 12s raw"),
        ("video", 0.0, None, "VIDEO"),
        ("live_photo", 0.0, ('["one"]', 3.0, 1), "LIVE PHOTO, renders as a 3s clip"),
        ("live_photo", 0.0, ('["one"]', 3.0, 0), "LIVE PHOTO (renders as a still)"),
        ("live_photo", 0.0, None, "LIVE PHOTO (renders as a still)"),
    ],
)
def test_the_line_says_what_the_material_will_actually_render_as(
    tmp_path: Path, kind: str, duration: float, motion: tuple | None, expected: str
) -> None:
    bursts = {"motion_bursts": (("a-picture", "b", *motion),)} if motion else {}
    store_path = store_with(tmp_path, **bursts)

    subject = candidate("a-picture", media_kind=kind, shippable_duration=duration)
    line = reader(store_path, subject).lines_for(("a-picture",)).lines[0].text

    assert expected in line


def test_a_place_without_names_falls_back_to_the_recorded_coordinates(tmp_path: Path) -> None:
    """A GPS fix still tells the editor two pictures were taken somewhere apart."""
    subject = candidate("a-picture", exif=ExifInfo(latitude=12.3456, longitude=65.4321))

    line = reader(store_with(tmp_path), subject).lines_for(("a-picture",)).lines[0].text

    assert "at 12.346,65.432" in line


def test_a_line_that_would_carry_a_private_identifier_is_withheld_without_logging_it(
    tmp_path: Path,
) -> None:
    private_id = "12345678-1234-4234-8234-123456789abc"

    class ContaminatedFacts:
        def facts_for(self, asset_ids: tuple[str, ...]) -> AssetAnnotationFactBatch:
            return AssetAnnotationFactBatch(
                requested_asset_ids=asset_ids,
                facts=(
                    StoredAssetAnnotationFacts(
                        asset_id=private_id,
                        description="Upstream label 12345678 appears in this description.",
                    ),
                ),
                unavailable_asset_ids=(),
            )

    batch = reader(
        tmp_path / "unused.sqlite",
        candidate(private_id),
        head_versions={},
        fact_repository=ContaminatedFacts(),
    ).lines_for((private_id,))

    assert batch.lines == ()
    assert batch.missing_asset_ids == (private_id,)
    assert batch.warnings == (
        "!! 1 annotation line(s) withheld because private identifiers were rendered",
    )
    assert "12345678" not in " ".join(batch.warnings)


def test_an_unreadable_store_yields_no_partial_evidence(tmp_path: Path) -> None:
    """Half a fact surface reads as a different picture, so none of it ships."""
    batch = reader(tmp_path / "never-written.sqlite", candidate("a-picture")).lines_for(
        ("a-picture",)
    )

    assert batch.lines == ()
    assert batch.missing_asset_ids == ("a-picture",)
    assert batch.warnings == ("!! annotation fact store unavailable",)


def test_an_asset_outside_the_prepared_source_is_never_rendered(tmp_path: Path) -> None:
    batch = reader(store_with(tmp_path), candidate("a-picture")).lines_for(("another-picture",))

    assert batch.lines == ()
    assert batch.warnings == ("!! requested annotation source unavailable",)


def test_a_reader_built_on_duplicate_candidates_refuses_to_answer(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unique candidate IDs"):
        reader(store_with(tmp_path), candidate("a-picture"), candidate("a-picture"))


def test_a_reader_asked_for_nothing_refuses_rather_than_returning_everything(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="at least one requested asset"):
        reader(store_with(tmp_path), candidate("a-picture")).lines_for(())
