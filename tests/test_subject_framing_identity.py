"""A memory about one person reads that person's face, not whichever named face is largest.

Every picture here is banked the way preparation banks it (Immich's faces, through the
store) and read back as the line the pick reads, so the test crosses the one place the
identity used to be dropped.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_story_shortlist import _capture_group_moments
from immich_memories.analysis.subject_framing import face_boxes_of, framing_visibility
from immich_memories.api.models import AssetFace, Person
from immich_memories.store.editorial_preparation import initialize, remember_faces
from tests.conftest import make_asset

CAPTURED = datetime(2030, 5, 1, 10, tzinfo=UTC)
PERSON_A = Person(id="person-a", name="Person A")
PERSON_B = Person(id="person-b", name="Person B")


def _face(person: Person | None, x1: int, y1: int, x2: int, y2: int) -> AssetFace:
    return AssetFace(
        id=f"face-{x1}-{y1}",
        person=person,
        bounding_box_x1=x1,
        bounding_box_y1=y1,
        bounding_box_x2=x2,
        bounding_box_y2=y2,
        image_width=1000,
        image_height=1000,
    )


def _candidate(asset_id: str, people: list[Person]) -> EditorialCandidate:
    source = make_asset(asset_id, file_created_at=CAPTURED).model_copy(update={"people": people})
    return EditorialCandidate(
        asset_id=asset_id,
        taken_at=CAPTURED,
        source=source,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=False,
        shippable_duration=0.0,
        grounded_annotations=(),
    )


def _lines(tmp_path: Path, faces: dict[str, list[AssetFace]], **reader_options) -> dict[str, str]:
    store_path = tmp_path / "annotations.sqlite"
    with sqlite3.connect(store_path) as connection:
        initialize(connection)
        for asset_id, found in faces.items():
            remember_faces(connection, asset_id, face_boxes_of(found))
        connection.commit()
    reader = StoredAnnotationLineReader(
        store_path=store_path,
        candidates=[_candidate(asset_id, [PERSON_A, PERSON_B]) for asset_id in faces],
        description_model="student-v1",
        head_versions={},
        pixel_producer_key="pixel-v1",
        **reader_options,
    )
    return dict(reader.lines_for(tuple(faces)).as_mapping())


def _frame_that_carries(lines: dict[str, str], *, sharper: str) -> str:
    [choice] = _capture_group_moments(
        [
            {"asset_id": asset, "taken": f"2030-05-01T10:00:0{i}", "moment": "m1", "kind": "still"}
            for i, asset in enumerate(lines)
        ],
        quality=lambda asset: 1.0 if asset == sharper else 0.0,
        subject=lambda asset: framing_visibility(lines[asset]),
    )
    return choice.primary


# A is a speck at the border behind a large, well-framed B.
_A_SPECK_B_LARGE = [_face(PERSON_A, 960, 400, 985, 425), _face(PERSON_B, 300, 300, 550, 600)]
# A is inside the frame and the largest face; B is smaller beside him.
_A_LARGE_B_SMALLER = [_face(PERSON_A, 300, 300, 500, 550), _face(PERSON_B, 650, 350, 750, 470)]


def test_in_a_memory_about_a_the_frame_showing_a_beats_the_sharper_one_where_b_is_large(
    tmp_path: Path,
) -> None:
    """B's good framing used to be read as A's, and the sharper speck took the moment."""
    lines = _lines(
        tmp_path,
        {"a-speck": _A_SPECK_B_LARGE, "a-shown": _A_LARGE_B_SMALLER},
        subjects=("Person A",),
    )

    assert _frame_that_carries(lines, sharper="a-speck") == "a-shown"
    assert framing_visibility(lines["a-speck"]).rung == 1


def test_a_memory_with_two_subjects_reads_the_better_shown_of_them(tmp_path: Path) -> None:
    lines = _lines(tmp_path, {"both": _A_SPECK_B_LARGE}, subjects=("Person A", "Person B"))

    assert framing_visibility(lines["both"]).rung == 3


def test_a_picture_naming_none_of_the_subjects_reads_like_a_picture_naming_nobody(
    tmp_path: Path,
) -> None:
    lines = _lines(tmp_path, {"only-b": [_face(PERSON_B, 300, 300, 550, 600)]}, subjects=("C",))

    assert "subject-framing" not in lines["only-b"]


def test_a_memory_with_no_subject_reads_the_best_shown_named_face_as_before(
    tmp_path: Path,
) -> None:
    lines = _lines(tmp_path, {"a-speck": _A_SPECK_B_LARGE, "a-shown": _A_LARGE_B_SMALLER})

    assert framing_visibility(lines["a-speck"]).rung == 3
    assert _frame_that_carries(lines, sharper="a-speck") == "a-speck"


def test_the_runtime_reads_lines_about_the_people_the_memory_is_about(tmp_path: Path) -> None:
    """The reader above only helps if the planner hands it the memory's people."""
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_runtime import (
        EditorialRunContext,
        build_editorial_planner,
    )
    from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config
    from tests.test_editorial_runtime import _window

    config = Config(
        cache={"directory": str(tmp_path / "cache")},
    )
    # Isolate the metadata reading path from the model acquisition component.
    config.editorial.preparation.tier = "metadata_only"
    store_path = config.editorial.resolve_annotation_database(config.cache.cache_path)
    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=ThumbnailCache(tmp_path / "thumbnails"),
        context=EditorialRunContext(
            "spotlight",
            "A",
            "person_spotlight",
            (_window(2030, 5, 1),),
            60,
            tmp_path / "run",
            people=("Person A",),
        ),
        # WHY: the people context is read from the owner's config dir; none is needed here.
        ports=EditorialRuntimePorts(load_people=dict),
    )
    with sqlite3.connect(store_path) as connection:
        remember_faces(connection, "a-speck", face_boxes_of(_A_SPECK_B_LARGE))
        connection.commit()
    prepared = SimpleNamespace(candidates=[_candidate("a-speck", [PERSON_A, PERSON_B])])

    assert planner._prepare_annotations is not None
    reader = planner._prepare_annotations.readings.reader(prepared)
    [line] = reader.lines_for(("a-speck",)).lines

    assert framing_visibility(line.text).rung == 1
