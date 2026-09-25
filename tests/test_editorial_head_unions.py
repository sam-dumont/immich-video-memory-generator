"""The distilled heads only ever add: to the standing zeros, to the screen refusal, to the hold.

Every rule here already had a detector answering it. A second detector joins each one, and the
cases that matter most are the ones where the second says nothing: a bank prepared before this
bundle has no `screen`, `frame_kind` or `uncovered_person` row and must read exactly as it did.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_carrier_eligibility import (
    excluded_carrier_sources,
    screen_flagged,
)
from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.analysis.editorial_shareability_audience import exposure_flagged
from immich_memories.analysis.editorial_source_gate import screen_document_rejections


def reader(*, favourite=False, people=(), product="month", line="", audience="shareable", **heads):
    source = SimpleNamespace(
        assets={"a": SimpleNamespace(is_favorite=favourite, people=people)},
        audience_annotations={"a": SimpleNamespace(heads=tuple(heads.items()))},
        annotations={"a": line},
        intent=SimpleNamespace(product=product),
        audience=audience,
    )
    return RuleStructureReader(source)


# -- a frame that carries nothing ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["empty_room_ceiling_or_floor", "lone_everyday_object"])
def test_a_frame_the_head_says_carries_nothing_does_not_stand(kind):
    assert reader(frame_kind=kind, people="two").standing("a") == 0


def test_a_frame_kind_that_carries_something_is_read_as_before():
    assert reader(frame_kind="people_moment", people="two").standing("a") == 2


def test_a_favourite_still_wins_a_frame_the_head_calls_nothing():
    assert reader(favourite=True, frame_kind="body_part_closeup").standing("a") == 2


def test_a_bank_with_no_frame_kind_row_stands_exactly_as_it_did():
    assert reader(people="two").standing("a") == 2


# -- either head says screen ---------------------------------------------------------------


def test_the_screen_head_refuses_a_frame_the_document_head_calls_a_photograph():
    heads = {"frame_kind": "screen_or_document", "doc_docling": "photograph", "screen": "yes"}

    assert reader(people="one", **heads).standing("a") == 0
    assert reader(people="one", **(heads | {"screen": "no"})).standing("a") == 2


def test_the_document_head_still_refuses_without_the_screen_head():
    heads = {"frame_kind": "screen_or_document", "doc_docling": "screenshot_from_computer"}

    assert reader(people="one", **heads).standing("a") == 0


def test_a_bank_with_no_screen_row_is_read_exactly_as_it_was():
    assert reader(doc_docling="photograph", people="two").standing("a") == 2


def test_the_screen_head_alone_excludes_a_source_the_document_head_admits():
    batch = SimpleNamespace(
        lines=[
            AssetAnnotationLine("shown", "2026-02-01 | A room.", heads=(("screen", "yes"),)),
            AssetAnnotationLine("kept", "2026-02-01 | A room.", heads=(("screen", "no"),)),
        ]
    )

    assert screen_document_rejections(batch) == {"shown": "screen-head"}


def test_the_screen_head_on_a_rendered_line_keeps_a_frame_out_of_the_carrier_pool():
    lines = {
        "shown": "2026-02-01 12:00 | A room. | screen=yes",
        "kept": "2026-02-01 12:00 | A room.",
    }

    assert excluded_carrier_sources(lines) == {"shown": "screen-head"}


def test_the_screen_union_is_one_test_every_gate_calls():
    assert screen_flagged({"screen": "yes"})
    assert not screen_flagged({"screen": "no"})
    assert not screen_flagged({})


# -- either detector says a person is uncovered --------------------------------------------


def test_the_second_opinion_alone_holds_a_picture_to_the_family():
    assert exposure_flagged({"nsfw_marqo": "no", "uncovered_person": "yes"})


def test_the_shipped_detector_still_holds_when_the_second_opinion_says_no():
    """The floor never moves: `uncovered_person: no` is not a clearance, it is silence."""
    assert exposure_flagged({"nsfw_marqo": "yes", "uncovered_person": "no"})


def test_a_bank_with_no_second_opinion_reads_as_the_floor_alone():
    assert exposure_flagged({"nsfw_marqo": "yes"})
    assert not exposure_flagged({"nsfw_marqo": "no"})


def test_nothing_clears_on_a_no_from_the_second_opinion():
    assert reader(nsfw_marqo="yes", uncovered_person="no", people="two").standing("a") == 0


def test_the_second_opinion_alone_takes_a_picture_out_of_standing():
    assert reader(nsfw_marqo="no", uncovered_person="yes", people="two").standing("a") == 0
