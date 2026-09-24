"""A clip stands on what its sampled frames show, not on the one frame Immich previews."""

from types import SimpleNamespace

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


def _standing(line_of, *, favourite=False, kind="video"):

    from immich_memories.analysis.editorial_story_standing import StandingGate

    scored: list[str] = []

    def stands_on_its_facts(asset: str) -> int:
        # Every picture's own facts say it stands, so any refusal here is the clip's frames.
        scored.append(asset)
        return 2

    clip = {
        "asset_id": "clip",
        "kind": kind,
        "raw_seconds": 12.0,
        "members": ["clip"],
        "favourite": favourite,
    }
    gate = StandingGate(
        stands_on_its_facts,
        line_of=line_of,
        life=lambda _asset: True,
        unit_by_asset={"clip": ("E1", clip)},
        pictures_of={"S1": 5},
    )
    return gate, scored


def test_a_clip_whose_frames_often_miss_its_subject_does_not_stand_on_facts_that_stand():
    gate, scored = _standing(
        lambda _asset: "10:00 | VIDEO 12s raw | a child at a door | frames=subject_often_missing"
    )
    needs = {"clip": gate.needs("clip", "major", "S1")}
    gate.ensure(["clip"], needs)

    assert not gate.stands("clip", "major", "S1")
    # No answer can move it, so it is not even scored.
    assert needs == {"clip": 0} and scored == []


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


def test_a_clip_that_shows_its_moment_still_stands_on_facts_that_stand():
    gate, _judge = _standing(lambda _asset: "10:00 | VIDEO 12s raw | a child at a door")
    gate.ensure(["clip"], {"clip": gate.needs("clip", "major", "S1")})

    assert gate.stands("clip", "major", "S1")


def _rules(*, favourite=False, **heads):

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


def _live_unit(*, rules, favourite, clip_frames, residual=9.0):
    """One Live Photo through the unit builder both tiers plan on."""
    from datetime import UTC, datetime

    from immich_memories.analysis.editorial_structure_material import UnitBuilder
    from immich_memories.analysis.motion_rendering import motion_renderings
    from immich_memories.api.models import AssetType
    from immich_memories.config_loader import Config
    from tests.conftest import make_asset

    still = make_asset("still", is_favorite=favourite, duration=None)
    still.type = AssetType.IMAGE
    still.file_created_at = datetime(2024, 2, 7, 10, tzinfo=UTC)
    still.live_photo_video_id = "clip-of-still"
    companions = {"clip-of-still": make_asset("clip-of-still", duration=3.0)}
    config = Config()
    source = SimpleNamespace(
        assets={"still": still},
        motion_residuals={"still": {"residual": residual}},
        speech_regions={},
        config=config,
        pixel_facts={"still": (900.0, 118.0)},
        clip_frames=clip_frames,
    )
    wall = SimpleNamespace(event_assets={"F01": ["still"]}, moment_of_asset={"still": "M01"})
    ports = SimpleNamespace(
        resolve_motion=None,
        thumbnail_hash=lambda _asset_id: None,
        rules=object() if rules else None,
    )
    renderings = motion_renderings([still], config, companion_assets=companions)
    builder = UnitBuilder(
        source, ports, wall, renderings=renderings, never_auto=set(), document_sources={}
    )
    (unit,) = builder.units_of("F01")
    return unit


@pytest.mark.parametrize("rules", [True, False])
@pytest.mark.parametrize("favourite", [True, False])
def test_a_live_photo_whose_clip_misses_its_subject_plays_as_its_still(rules, favourite):
    # The owner's ruling: a Live Photo's favourite is on the still. A clip of wall costs the
    # picture its motion, never the picture.
    unit = _live_unit(
        rules=rules, favourite=favourite, clip_frames={"clip-of-still": SUBJECT_OFTEN_MISSING}
    )

    assert unit["asset_id"] == "still"
    assert unit["kind"] == "live-still"
    assert unit["favourite"] is favourite


@pytest.mark.parametrize("rules", [True, False])
def test_a_live_photo_with_an_interesting_clip_plays_as_motion(rules):
    shows = _live_unit(
        rules=rules, favourite=False, clip_frames={"clip-of-still": "shows_its_moment"}
    )
    quiet = _live_unit(
        rules=rules,
        favourite=False,
        clip_frames={"clip-of-still": "shows_its_moment"},
        residual=0.4,
    )

    assert shows["kind"] == "live-motion"
    # Interesting is measured motion AND the subject in frame; either alone is a still.
    assert quiet["kind"] == "live-still"


def test_a_live_photo_line_never_refuses_it_on_the_model_tier():
    # Even if a still's line ever carried its clip's reading, the gate refuses real videos
    # only: a Live Photo that plays is one whose clip was read as showing its subject.
    line = "10:00 | LIVE PHOTO | a child at a door | frames=subject_often_missing"
    gate, _judge = _standing(lambda _asset: line, kind="live-motion")
    gate.ensure(["clip"], {"clip": gate.needs("clip", "major", "S1")})

    assert gate.stands("clip", "major", "S1")
    assert not gate.rejected_motion("clip")


def test_a_film_reads_each_live_clips_banked_frames(tmp_path):
    from immich_memories.analysis.editorial_clip_frames import load_clip_frames
    from immich_memories.cache.embedding_cache import HeadFactStore

    store = HeadFactStore(tmp_path / "bank.sqlite")
    store.remember_facts("walled", [clip_frames_fact([MOMENT, NOTHING])], encoder_key="k")
    store.remember_facts("held", [clip_frames_fact([MOMENT] * 4)], encoder_key="k")
    store.close()

    frames = load_clip_frames(tmp_path / "bank.sqlite", ["walled", "held", "unread"])

    assert frames == {"walled": SUBJECT_OFTEN_MISSING, "held": "shows_its_moment"}
    assert load_clip_frames(tmp_path / "absent.sqlite", ["walled"]) == {}
