"""A nominated pair is one picture only when both arrangements agree."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from threading import Lock
from types import SimpleNamespace

import numpy
import pytest
from PIL import Image

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_same_picture import (
    SELECTS_MAX_CORROBORATION,
    confirm_same_picture_pairs,
)
from immich_memories.analysis.visual_atlas import AtlasTile
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from tests.conftest import make_asset

CAPTURED = datetime(2021, 6, 5, 11, tzinfo=UTC)


def jpeg(shade: int) -> bytes:
    """A textured picture, so a perceptual hash of it says something."""
    pixels = numpy.random.RandomState(shade).randint(0, 256, (48, 64, 3), dtype=numpy.uint8)
    output = BytesIO()
    Image.fromarray(pixels).save(output, "JPEG")
    return output.getvalue()


def candidate(asset_id: str, *, minutes: int = 0) -> EditorialCandidate:
    return EditorialCandidate(
        asset_id=asset_id,
        taken_at=CAPTURED + timedelta(minutes=minutes),
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=False,
        source=make_asset(asset_id, file_created_at=CAPTURED + timedelta(minutes=minutes)),
        shippable_duration=0.0,
        grounded_annotations=(),
    )


def frames(count: int) -> list[EditorialCandidate]:
    return [candidate(f"frame-{index}", minutes=index) for index in range(count)]


def neighbours(index: int) -> frozenset[str]:
    return frozenset({f"frame-{index}", f"frame-{index + 1}"})


class Atlas:
    """Stable tiles for the pictures under judgement, and nothing else."""

    def __init__(self, shades: dict[str, int], *, unavailable: frozenset[str] = frozenset()):
        self.shades = shades
        self.unavailable = unavailable

    def tile_for(self, asset_id: str) -> AtlasTile:
        if asset_id in self.unavailable:
            return AtlasTile(asset_id, "unavailable", None, None, 0, "no pixels")
        blob = jpeg(self.shades[asset_id])
        return AtlasTile(asset_id, "photo", blob, sha256(blob).hexdigest(), 1)


class Requester:
    """Answers each arrangement of each pair, so concurrency cannot reorder the script."""

    def __init__(self, answers=None, default=(True, True)):
        self.answers = dict(answers or {})
        self.default = default
        self.arrangements: list[str] = []
        self.lock = Lock()

    def ask(self, request):
        with self.lock:
            self.arrangements.append(request.render_version)
        scripted = self.answers.get(frozenset(request.ordered_input_ids), self.default)
        answer = scripted[0 if request.render_version.endswith("ab") else 1]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, str):
            return SimpleNamespace(raw_text=answer)
        payload = {"schema_version": "pair-v3", "same": answer, "reason": "Judged pair."}
        return SimpleNamespace(raw_text=json.dumps(payload))


def confirm(tmp_path, pairs, *, default=(True, True), distances=None, unavailable=frozenset()):
    ids = {c.asset_id for pair in pairs for c in pair}
    atlas = Atlas(dict.fromkeys(ids, 200), unavailable=unavailable)
    requester = Requester(default=default)
    decisions = confirm_same_picture_pairs(
        pairs,
        atlas=atlas,
        requester=requester,
        sheet_output_dir=tmp_path / "sheets",
        corroborating_distances=distances,
        limits=VisionRequestLimits(),
    )
    return decisions, requester


def test_a_nominated_pair_is_one_picture_only_when_both_arrangements_agree(tmp_path):
    pair = (candidate("earlier"), candidate("later", minutes=1))

    agreed, requester = confirm(tmp_path, [pair], default=(True, True))
    assert [d.same for d in agreed] == [True]
    assert requester.arrangements == ["selects/pair/ab", "selects/pair/ba"]

    contradicted, _ = confirm(tmp_path, [pair], default=(True, False))
    assert [d.same for d in contradicted] == [False]
    assert contradicted[0].warning is None


def test_a_first_arrangement_saying_different_never_buys_the_second(tmp_path):
    pair = (candidate("earlier"), candidate("later", minutes=1))

    decisions, requester = confirm(tmp_path, [pair], default=(False, True))

    assert [d.same for d in decisions] == [False]
    assert requester.arrangements == ["selects/pair/ab"]


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"schema_version": "pair-v2", "same": True}),
        json.dumps({"schema_version": "pair-v3", "same": "yes"}),
        "not an answer at all",
        RuntimeError("the provider refused"),
    ],
)
def test_an_answer_to_another_question_keeps_both_pictures_with_a_warning(tmp_path, answer):
    pair = (candidate("earlier"), candidate("later", minutes=1))

    decisions, _ = confirm(tmp_path, [pair], default=(answer, True))

    assert decisions[0].same is False
    assert "Unreadable final duplicate pair" in decisions[0].warning


def test_pictures_without_pixels_are_never_cut(tmp_path):
    pair = (candidate("earlier"), candidate("later", minutes=1))

    decisions, requester = confirm(tmp_path, [pair], unavailable=frozenset({"later"}))

    assert decisions[0].same is False
    assert requester.arrangements == []


def test_a_qualified_pixel_distance_replaces_the_reverse_arrangement(tmp_path):
    pair = (candidate("earlier"), candidate("later", minutes=1))

    close, requester = confirm(tmp_path, [pair], distances=[SELECTS_MAX_CORROBORATION])
    assert [d.same for d in close] == [True]
    assert requester.arrangements == ["selects/pair/ab"]

    far, far_requester = confirm(tmp_path, [pair], distances=[SELECTS_MAX_CORROBORATION + 1])
    assert [d.same for d in far] == [True]
    assert far_requester.arrangements == ["selects/pair/ab", "selects/pair/ba"]


def test_no_nominated_pair_asks_nothing(tmp_path):
    decisions, requester = confirm(tmp_path, [])

    assert decisions == ()
    assert requester.arrangements == []


@pytest.mark.parametrize(
    "pairs,distances,match",
    [
        ([("one", "two")], [3, 4], "align"),
        ([("one", "one")], None, "two different assets"),
        ([("one", "two"), ("two", "one")], None, "unique"),
    ],
)
def test_malformed_nominations_are_refused_before_any_picture_is_judged(
    tmp_path, pairs, distances, match
):
    nominated = [(candidate(a), candidate(b, minutes=1)) for a, b in pairs]

    with pytest.raises(ValueError, match=match):
        confirm(tmp_path, nominated, distances=distances)
