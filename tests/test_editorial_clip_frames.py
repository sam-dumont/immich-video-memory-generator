"""A clip stands on what its sampled frames show, not on the one frame Immich previews."""

import pytest

from immich_memories.analysis.editorial_clip_frames import (
    CLIP_FRAMES_HEAD,
    SUBJECT_OFTEN_MISSING,
    clip_frames_fact,
)

MOMENT = "people_moment"
NOTHING = "lone_everyday_object"


@pytest.mark.parametrize(
    ("kinds", "missing"),
    [
        # Measured on real clips: the two a reviewer called "mostly wall, subject at the edge"
        # read five of eight frames as a moment; every clip kept beside them read six or more.
        ([MOMENT] * 5 + [NOTHING] * 3, True),
        ([MOMENT] * 6 + ["body_part_closeup"] * 2, False),
        (["place_or_scenery"] * 8, False),
        ([MOMENT, NOTHING], True),
    ],
)
def test_a_clip_whose_frames_often_show_nothing_says_so(kinds, missing):
    fact = clip_frames_fact(kinds)

    assert fact is not None and fact.head == CLIP_FRAMES_HEAD
    assert (fact.label == SUBJECT_OFTEN_MISSING) is missing
    assert fact.confidence == pytest.approx(
        sum(k in (MOMENT, "place_or_scenery") for k in kinds) / len(kinds)
    )


def test_a_clip_with_no_readable_frame_decides_nothing():
    assert clip_frames_fact([]) is None


def _standing(line_of, *, favourite=False):
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_story_standing import StandingGate
    from immich_memories.config_models_llm import LLMConfig

    class Approves:
        # WHY: the text model is the standing gate's only external boundary; it approves
        # every row, so any refusal here is the clip's frames and not a vote.
        config = SimpleNamespace(llm=LLMConfig(model="model-a"))

        def __init__(self):
            self.calls: list[str] = []

        def ask(self, _stage, prompt, **_kwargs):
            self.calls.append(prompt)
            return '{"weak": {}}'

    clip = {
        "asset_id": "clip",
        "kind": "video",
        "raw_seconds": 12.0,
        "members": ["clip"],
        "favourite": favourite,
    }
    judge = Approves()
    gate = StandingGate(
        judge,
        line_of=line_of,
        life=lambda _asset: True,
        unit_by_asset={"clip": ("E1", clip)},
        pictures_of={"S1": 5},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
    )
    return gate, judge


def test_a_clip_whose_frames_often_miss_its_subject_does_not_stand_on_approving_votes():
    gate, judge = _standing(
        lambda _asset: "10:00 | VIDEO 12s raw | a child at a door | frames=subject_often_missing"
    )
    needs = {"clip": gate.needs("clip", "major", "S1")}
    gate.ensure(["clip"], needs)

    assert not gate.stands("clip", "major", "S1")
    # No answer can move it, so nobody is asked about it.
    assert needs == {"clip": 0} and judge.calls == []


def test_a_favourite_clip_whose_frames_often_miss_its_subject_still_stands():
    # The owner's ruling: a favourite showing a wall means something happened there.
    line = "10:00 | VIDEO 12s raw | a child at a door | frames=subject_often_missing"
    favourite, _judge = _standing(lambda _asset: line, favourite=True)
    favourite.ensure(["clip"], {"clip": favourite.needs("clip", "major", "S1")})
    other, _judge = _standing(lambda _asset: line)
    other.ensure(["clip"], {"clip": other.needs("clip", "major", "S1")})

    assert favourite.stands("clip", "major", "S1")
    assert not favourite.rejected_motion("clip")
    assert not other.stands("clip", "major", "S1")


def test_a_clip_that_shows_its_moment_still_stands_on_approving_votes():
    gate, _judge = _standing(lambda _asset: "10:00 | VIDEO 12s raw | a child at a door")
    gate.ensure(["clip"], {"clip": gate.needs("clip", "major", "S1")})

    assert gate.stands("clip", "major", "S1")


def _rules(*, favourite=False, **heads):
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_rule_reader import RuleStructureReader

    source = SimpleNamespace(
        assets={"a": SimpleNamespace(is_favorite=favourite, people=())},
        audience_annotations={"a": SimpleNamespace(heads=tuple(heads.items()))},
        annotations={"a": ""},
        intent=SimpleNamespace(product="month"),
        audience="family",
    )
    return RuleStructureReader(source)


def test_the_rules_reader_does_not_stand_a_clip_whose_frames_often_miss_its_subject():
    heads = {
        "frame_kind": "people_moment",
        "people": "one",
        CLIP_FRAMES_HEAD: SUBJECT_OFTEN_MISSING,
    }

    assert _rules(**heads).standing("a") == 0
    # The favourite still wins it, exactly as it wins a still the frame head calls nothing.
    assert _rules(favourite=True, **heads).standing("a") == 2
    assert _rules(**(heads | {CLIP_FRAMES_HEAD: "shows_its_moment"})).standing("a") == 2


def test_the_producer_banks_each_clips_reading_of_its_own_frames(tmp_path):
    import sqlite3

    import numpy as np
    from PIL import Image

    from immich_memories.analysis.editorial_preparation_heads import prepare_clip_frames
    from immich_memories.triage.heads import HeadBundle, HeadWeights, PcaWeights

    pack_dim = 6 * 384
    kinds = ("lone_everyday_object", "people_moment")
    HeadBundle(
        encoder_key="a" * 64,
        pca=PcaWeights(
            mean=np.zeros(pack_dim, dtype=np.float32),
            components=np.zeros((4, pack_dim), dtype=np.float32),
        ),
        heads=(
            HeadWeights(
                name="frame_kind",
                version="public-v1",
                classes=kinds,
                coef=np.zeros((2, 4), dtype=np.float32),
                # Every frame reads as the first kind: a clip that shows nothing.
                intercept=np.array([1.0, 0.0], dtype=np.float32),
            ),
        ),
    ).save(tmp_path / "heads.npz")
    frames = []
    for index in range(4):
        path = tmp_path / f"frame-{index}.jpg"
        Image.new("RGB", (32, 24), (index * 40, 90, 90)).save(path)
        frames.append(path)

    class Encoder:
        # WHY: the DINOv2 ONNX export is an 88 MB download no test environment holds; the
        # packs it would produce are replaced, everything that reads them is real.
        key = "a" * 64

        def embed(self, batch):
            return np.zeros((len(batch), pack_dim), dtype=np.float32)

    failures = prepare_clip_frames(
        frame_paths={"clip": frames, "unreadable": [tmp_path / "absent.jpg"]},
        store_path=tmp_path / "facts.sqlite",
        bundle_path=tmp_path / "heads.npz",
        encoder_path=tmp_path / "encoder.onnx",
        check_cancelled=lambda: None,
        open_encoder=lambda _path, **_kwargs: Encoder(),
    )

    with sqlite3.connect(tmp_path / "facts.sqlite") as connection:
        rows = connection.execute(
            "SELECT asset_id, head, version, label, confidence FROM head_facts"
        ).fetchall()
    assert rows == [
        ("clip", CLIP_FRAMES_HEAD, "frame_kind-public-v1/8-frames", SUBJECT_OFTEN_MISSING, 0.0)
    ]
    assert set(failures) == {"unreadable"}
