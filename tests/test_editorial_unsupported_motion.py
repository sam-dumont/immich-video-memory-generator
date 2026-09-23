"""A motion sentence is evidence only where the companion's measured motion supports it (#1118).

The fixtures are generated: a flat "room" drawn in a few grey bands, and the same room with a
bright figure crossing it. The residual is the production optical-flow measurement run on them.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_motion_facts import measure_motion
from immich_memories.analysis.editorial_preparation_motion import (
    MOTION_PRODUCER,
    BankedMotionLines,
    missing_motion,
    motion_sources,
)
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_structure_budget import RESIDUAL_MIN
from immich_memories.analysis.editorial_structure_lines import LIVING, UnitLines
from immich_memories.store.editorial_preparation import initialize, private_database_path
from immich_memories.store.motion_lines import (
    DESCRIBED,
    MotionLine,
    initialize_motion_lines,
    remember_motion_line,
)
from tests.test_editorial_preparation_motion import Seat, answer, picture, produce


def companion(tmp_path, *, figure: bool) -> bytes:
    """Sixteen frames of an empty room; with `figure`, somebody walks across it."""
    import cv2
    import numpy as np

    path = tmp_path / ("figure.avi" if figure else "empty.avi")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 12, (160, 120))
    try:
        for index in range(16):
            frame = np.full((120, 160, 3), 180, dtype=np.uint8)
            frame[80:, :] = 120  # floor
            frame[55:85, 20:70] = 60  # sofa
            if figure:
                x = 50 + index * 6
                frame[30:110, x - 40 : x] = 230
                frame[50:70, x - 30 : x - 10] = 90
            writer.write(frame)
    finally:
        writer.release()
    return path.read_bytes()


class PersuadedJudge:
    # WHY: the text model is the standing gate's only external boundary. This one believes
    # whatever a row says: it names as weak every row that shows nobody.
    def __init__(self):
        self.calls = []

    def ask(self, _stage, prompt, **_kwargs):
        self.calls.append(prompt)
        rows = re.findall(r"^(P\d+): (.*)$", prompt, re.MULTILINE)
        return json.dumps({"weak": {k: "nothing happens" for k, r in rows if not LIVING.search(r)}})


@pytest.fixture
def store(tmp_path):
    path = private_database_path(tmp_path / "annotations.sqlite")
    with sqlite3.connect(path) as connection:
        initialize(connection)
    return path


def admission(unit, text: UnitLines, lines: BankedMotionLines, *, pictures: int) -> StandingGate:
    asset = unit["asset_id"]
    return StandingGate(
        PersuadedJudge(),
        line_of=lambda _asset: text.line(unit),
        life=lambda _asset: text.shows_life(unit),
        unit_by_asset={asset: ("E1", unit)},
        pictures_of={"K01": pictures},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        motion_line=lines.observe,
    )


def test_a_caption_claiming_action_in_an_empty_room_cannot_admit_it(tmp_path, store):
    residual = measure_motion(companion(tmp_path, figure=False))["residual"]
    assert residual < RESIDUAL_MIN
    room = picture("room", live="room-companion")
    # A line an older pass banked for this picture, before this companion was measured.
    with sqlite3.connect(store) as connection:
        initialize_motion_lines(connection)
        remember_motion_line(
            connection,
            asset_id="room",
            producer=MOTION_PRODUCER,
            source_digest=source_metadata_digest(room),
            line=MotionLine(DESCRIBED, "A woman dances across the living room.", 3),
            bytes_read=0,
        )
    lines = BankedMotionLines(store_path=store, assets={"room": room}, described=True)
    text = UnitLines({"room": "2024-03-02 | An empty living room with a grey sofa."})
    live = {"asset_id": "room", "members": ["room"], "raw_seconds": 3.0, "favourite": False}
    unmeasured = live | {"kind": "live-motion", "residual": None, "motion_assessed": False}
    measured = live | {"kind": "live-still", "residual": residual, "motion_assessed": True}

    for unit in (unmeasured, measured):
        assert "dances" not in lines.observe(unit)
        assert not text.shows_life(unit)
        gate = admission(unit, text, lines, pictures=5)
        gate.ensure(["room"])
        assert not gate.stands("room", "minor", "K01")
        assert not gate.stands("room", "major", "K01")


def test_a_live_photo_whose_measured_action_differs_from_its_still_stays_usable(tmp_path, store):
    residual = measure_motion(companion(tmp_path, figure=True))["residual"]
    assert residual >= RESIDUAL_MIN
    swing = picture("swing", live="swing-companion")
    sources = motion_sources([swing], residual_of=lambda _asset: residual)
    produce(store, sources, seat=Seat(answer("A child runs in and jumps onto the swing.")))
    lines = BankedMotionLines(store_path=store, assets={"swing": swing}, described=True)
    text = UnitLines({"swing": "2024-03-02 | An empty swing in a garden."})
    unit = {
        "asset_id": "swing",
        "members": ["swing"],
        "raw_seconds": 3.0,
        "favourite": False,
        "kind": "live-motion",
        "residual": residual,
        "motion_assessed": True,
    }

    assert lines.observe(unit).startswith("A child runs in and jumps onto the swing.")
    assert text.shows_life(unit)
    # A story of one picture: the clip has to stand entirely alone, and does.
    gate = admission(unit, text, lines, pictures=1)
    gate.ensure(["swing"])
    assert gate.stands("swing", "major", "K01")


def test_a_prepared_line_records_the_evidence_that_admitted_it(store):
    from contextlib import closing

    from immich_memories.analysis.editorial_motion_facts import RESIDUAL_PRODUCER
    from tests.test_editorial_preparation_motion import video

    swing = picture("swing", live="swing-companion")
    sources = motion_sources(
        [swing, video("clip")], residual_of=lambda a: 2.1 if a.id == "swing" else None
    )
    produce(store, sources, seat=Seat(answer("A child runs.")))

    with closing(sqlite3.connect(store)) as connection:
        rows = dict(connection.execute("SELECT asset_id, provenance FROM motion_lines"))
    live, clip = json.loads(rows["swing"]), json.loads(rows["clip"])
    assert live["admitted_on"] == {"residual": 2.1, "producer": RESIDUAL_PRODUCER}
    assert clip["admitted_on"] == {"kind": "video"}
    assert live["keyframes_at"] == [1.0, 4.0, 7.0]
    assert live["prompt"] == clip["prompt"] != ""


def test_a_line_banked_before_provenance_is_counted_when_it_is_used(tmp_path):
    from contextlib import closing

    from tests.test_editorial_preparation_motion import video

    clip = video("clip")
    bank = tmp_path / "annotations.sqlite"
    with closing(sqlite3.connect(bank)) as connection, connection:
        # The table exactly as the first motion producer created and filled it.
        connection.execute(
            "CREATE TABLE motion_lines (asset_id TEXT NOT NULL, producer TEXT NOT NULL, "
            "source_digest TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL, "
            "frames INTEGER NOT NULL, bytes_read INTEGER NOT NULL, written_at TEXT NOT NULL, "
            "PRIMARY KEY(asset_id, producer))"
        )
        connection.execute(
            "INSERT INTO motion_lines VALUES (?,?,?,?,?,?,?,?)",
            (
                "clip",
                MOTION_PRODUCER,
                source_metadata_digest(clip),
                DESCRIBED,
                "A dog runs.",
                3,
                0,
                "2026-09-17T00:00:00Z",
            ),
        )
    lines = BankedMotionLines(store_path=bank, assets={"clip": clip}, described=True)

    # Read-only, before any preparation migrated the table: the old line still answers.
    assert lines.observe({"asset_id": "clip", "kind": "video"}).startswith("A dog runs.")
    assert lines.metrics()["unrecorded"] == 1

    with closing(sqlite3.connect(bank)) as connection:
        produce_into = motion_sources([video("fresh")], residual_of=lambda _a: None)
        initialize_motion_lines(connection)
        assert [s.asset_id for s in missing_motion(connection, produce_into)] == ["fresh"]
    produce(bank, produce_into, seat=Seat(answer("A cat jumps.")))
    fresh = BankedMotionLines(store_path=bank, assets={"fresh": video("fresh")}, described=True)
    assert fresh.observe({"asset_id": "fresh", "kind": "video"}).startswith("A cat jumps.")
    assert fresh.metrics()["unrecorded"] == 0
