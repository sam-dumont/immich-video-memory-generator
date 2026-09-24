"""The carrier rules read what a picture shows, never the tags the pipeline wrote beside it.

Every stitched Live Photo burst carries "LIVE PHOTO BURST of N stills, stitches to a Ns clip" on
its line, and the medical-care rule once refused every one of them as a picture of stitches.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources
from immich_memories.api.models import ExifInfo, Person
from tests.test_annotation_lines import OwnerPersonContext, candidate, reader, store_with

ASSET = "asset-private-001"


def _rendered(tmp_path: Path, caption: str) -> str:
    """A burst line with every tag the renderer writes: media, place, people, framing, heads,
    star, nominator flag, pixel warnings and the source's technical facts."""
    store_path = store_with(
        tmp_path,
        descriptions=((ASSET, "student-v1", caption),),
        description_fields=((ASSET, "student-v1", "setting", "living room"),),
        flags=((ASSET, "review", '{"reason":"a swollen eye, stitches, a rash"}', "nominator"),),
        head_facts=((ASSET, "activity", "public-v1", "posing"),),
        pixel_facts=((ASSET, "pixel-v1", 5.0, 20.0, 10.0, 0.7, 0.9, 1),),
        pixel_facts_thresholds=(("sharpness_p10", 10.0, "pixel-v1"),),
        motion_bursts=((ASSET, "burst-1", '["still-a","still-b","still-c"]', 3.2, 1),),
        face_boxes=((ASSET, "Rash Infection", 0.1, 0.1, 0.4, 0.5),),
    )
    burst = candidate(
        ASSET,
        media_kind="live_photo",
        favourite=True,
        exif=ExifInfo(city="Swollen Hill", country="Bandage Island"),
        people=(Person(id="person-private-001", name="Rash Infection"),),
        grounded_annotations=("resolution:4032x3024", "subject:stitches", "duration:3.200s"),
    )
    line = (
        reader(
            store_path,
            burst,
            people_context={"person-private-001": OwnerPersonContext(relationship="partner")},
        )
        .lines_for((ASSET,))
        .as_mapping()[ASSET]
    )
    assert "stitches to a 3s clip" in line
    return line


def test_a_stitched_burst_is_not_a_picture_of_stitches(tmp_path: Path) -> None:
    line = _rendered(tmp_path, "Two children blowing out candles on a birthday cake.")

    assert excluded_carrier_sources({ASSET: line}) == {}


@pytest.mark.parametrize(
    "caption",
    [
        "A man with stitches on his forehead after a fall.",
        "A child with a bandage on her knee.",
    ],
)
def test_real_care_in_the_caption_still_holds_a_burst(tmp_path: Path, caption: str) -> None:
    line = _rendered(tmp_path, caption)

    assert excluded_carrier_sources({ASSET: line}) == {ASSET: "medical-care"}


def test_care_named_by_a_picture_observation_holds_every_member_row() -> None:
    line = (
        "Material picture p1: 2024-01-01 10:00+00:00 | LIVE PHOTO BURST of 2 stills, "
        "stitches to a 2s clip | A baby asleep\n"
        "Material picture p2: 2024-01-01 10:00+00:00 | picture observations: "
        "subject_action: a baby with a nasal cannula"
    )

    assert excluded_carrier_sources({ASSET: line}) == {ASSET: "medical-care"}


def test_a_screen_or_a_face_close_up_is_still_read_off_the_caption() -> None:
    at = datetime(2024, 1, 1, tzinfo=UTC).isoformat()
    assert excluded_carrier_sources(
        {
            "screen": f"{at} | A laptop screen showing a spreadsheet",
            "face": f"{at} | picture observations: composition: a close-up of a child's eye",
        }
    ) == {"screen": "screen-description", "face": "face-close-up"}


def test_an_uncaptioned_line_hands_the_audience_reader_no_tag_as_its_caption() -> None:
    from immich_memories.analysis.editorial_shareability import evidence_for_unit

    line = (
        "2024-01-01 10:00+00:00 | LIVE PHOTO BURST of 2 stills, stitches to a 2s clip | "
        "at Hometown | with Person A (partner) | subject-framing:1 (9.72% of the frame, "
        "at the frame's edge, not the largest face) | nsfw=yes | STARRED by the photographer | "
        "DARK | rotated | reencode-suspected | motion:available | blur:0.2 | similarity:0.9"
    )

    evidence = evidence_for_unit({"asset_id": ASSET}, {}, {}, {ASSET: line})

    (member,) = evidence["members"]
    assert member["caption"] == ""
    assert member["detectors"] == {"nsfw_marqo": "yes"}


@pytest.mark.parametrize("tag", ["BLOWN OUT", "rotated", "reencode-suspected", "DARK"])
def test_an_uncaptioned_picture_of_someone_is_not_a_lone_object_for_a_tag(tag: str) -> None:
    from immich_memories.analysis.editorial_structure_lines import UnitLines

    line = f"2024-01-01 10:00+00:00 | with Person A (partner) | {tag} | resolution:4032x3024"
    text = UnitLines({ASSET: line}, life_without_prose=lambda _asset: True)
    unit = {"asset_id": ASSET, "kind": "still"}

    assert text.description(unit) == ""
    assert text.shows_life(unit)


def test_a_framing_tag_is_not_prose_that_shows_a_face() -> None:
    from immich_memories.analysis.editorial_structure_lines import UnitLines

    line = (
        "2024-01-01 10:00+00:00 | subject-framing:1 (9.72% of the frame, at the frame's edge, "
        "not the largest face) | An empty beach at dusk."
    )

    assert UnitLines({ASSET: line}).description({"asset_id": ASSET}) == "An empty beach at dusk."
