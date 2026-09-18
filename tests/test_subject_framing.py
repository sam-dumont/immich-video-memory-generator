"""A picture that names somebody has to show them, and the boxes say how well."""

import pytest

from immich_memories.analysis.subject_framing import (
    FaceBox,
    face_boxes_of,
    framing_annotation,
    framing_visibility,
    subject_framing,
)


def box(x1, y1, x2, y2, *, named=True):
    return FaceBox(x1=x1, y1=y1, x2=x2, y2=y2, named=named)


def test_a_picture_with_no_named_face_has_nothing_to_say_about_its_subject():
    assert subject_framing([box(0.4, 0.4, 0.6, 0.6, named=False)]) is None
    assert subject_framing([]) is None


def test_a_speck_at_the_frames_edge_beside_larger_faces_is_neither_inside_nor_foremost():
    framing = subject_framing(
        [box(0.955, 0.40, 0.99, 0.46), box(0.30, 0.30, 0.50, 0.55, named=False)]
    )

    assert (framing.inside, framing.foremost) == (False, False)
    assert framing.rung == 1
    assert framing.share == pytest.approx(0.0021)


def test_a_small_subject_with_a_face_width_of_air_on_every_side_is_inside_the_frame():
    framing = subject_framing([box(0.40, 0.40, 0.46, 0.48)])

    assert (framing.inside, framing.foremost, framing.rung) == (True, True, 3)


def test_a_face_filling_the_frame_is_inside_it_wherever_a_face_width_of_air_cannot_fit():
    filling = subject_framing([box(0.02, 0.02, 0.98, 0.98)])
    cut_off = subject_framing([box(0.0, 0.02, 0.60, 0.98)])

    assert filling.inside is True
    assert cut_off.inside is False


def test_the_best_shown_named_face_answers_for_a_picture_holding_several():
    framing = subject_framing([box(0.90, 0.90, 0.95, 0.95), box(0.40, 0.35, 0.60, 0.65)])

    assert framing.rung == 3
    assert framing.share == pytest.approx(0.06)


def test_the_visibility_a_reader_parses_back_is_the_one_the_line_was_given():
    framing = subject_framing([box(0.955, 0.40, 0.99, 0.46), box(0.3, 0.3, 0.5, 0.55, named=False)])
    line = f"2022-03-27 11:34 | at a track | {framing_annotation(framing)} | resolution:4000x2666"

    visibility = framing_visibility(line)

    assert (visibility.rung, visibility.share) == (framing.rung, pytest.approx(framing.share, 0.01))


def test_a_line_without_the_fact_leaves_every_picture_where_it_was():
    assert framing_visibility("2022-03-27 11:34 | at a track") == framing_visibility("")


def test_the_annotation_says_in_words_what_the_rung_counts():
    edged = framing_annotation(subject_framing([box(0.0, 0.4, 0.04, 0.46)]))
    framed = framing_annotation(subject_framing([box(0.40, 0.40, 0.46, 0.48)]))

    assert "at the frame's edge" in edged
    assert "inside the frame" in framed and "the largest face" in framed


def test_immich_boxes_are_normalized_by_the_rendition_they_were_found_on():
    from datetime import UTC, datetime

    from immich_memories.api.models import Asset, AssetFace, Person

    when = datetime(2022, 3, 27, tzinfo=UTC)
    asset = Asset(
        id="a1",
        type="IMAGE",
        file_created_at=when,
        file_modified_at=when,
        updated_at=when,
        original_file_name="photo.jpg",
        width=4000,
        height=2666,
        people=[
            Person(
                id="p1",
                name="Named",
                faces=[
                    AssetFace(
                        id="f1",
                        boundingBoxX1=800,
                        boundingBoxY1=200,
                        boundingBoxX2=880,
                        boundingBoxY2=300,
                        imageWidth=1000,
                        imageHeight=1000,
                    )
                ],
            ),
            Person(id="p2", name="", faces=[AssetFace(id="f2", imageWidth=0, imageHeight=0)]),
        ],
    )

    boxes = face_boxes_of(asset)

    assert boxes == (FaceBox(x1=0.8, y1=0.2, x2=0.88, y2=0.3, named=True),)


def test_an_asset_immich_found_no_face_on_produces_no_boxes():
    from types import SimpleNamespace

    assert face_boxes_of(SimpleNamespace(people=[])) == ()
