"""A picture the duplicate review brings in to refill a slot passes the audience gate first."""

from __future__ import annotations

from types import SimpleNamespace

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_structure_audience import AudienceBank, AudienceGate
from immich_memories.analysis.editorial_structure_finishing import PlanRun, final_duplicate_review
from tests.editorial_thin_fixtures import CountingJudge

DAY = "2024-02-04"
HASHES = {"keeper": "0000000000000000", "repeat": "0000000000000000", "held": "00000000ffffffff"}
HASHES |= {"clean": "ffffffff00000000"}


def _shot(asset_id: str, minute: int) -> dict:
    return {
        "asset_id": asset_id,
        "taken": f"{DAY}T09:{minute:02d}:00+00:00",
        "kind": "still",
        "members": [asset_id],
        "seconds": 3.5,
        "story_episode": "S001",
        "moment": "M001",
    }


def _gate(tmp_path, *, exposed: set[str]) -> AudienceGate:
    lines = dict.fromkeys(HASHES, f"{DAY}T09:00 | people at a table")
    # WHY: the exposure head's reading is a banked annotation; the gate reads it off the line.
    annotations = {
        a: AssetAnnotationLine(
            a,
            f"{DAY}T09:00 | A clothed person at a table. | activity=eating",
            description="A clothed person at a table.",
            heads=(("nsfw_marqo", "yes" if a in exposed else "no"),),
        )
        for a in HASHES
    }
    return AudienceGate(
        CountingJudge(),
        audience="sendable",
        annotations=annotations,
        flag_rows={},
        lines=lines,
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer="full|model-a"),
    )


def _review(tmp_path, offers: list[dict], *, exposed: set[str]) -> PlanRun:
    run = PlanRun(carriers=[_shot("keeper", 0), _shot("repeat", 5)], final_content_cap=60.0)
    ports = SimpleNamespace(
        thumbnail_hash=HASHES.get,
        scene_print=None,
        rules=object(),
        resolve_motion=None,
    )
    final_duplicate_review(
        run,
        ports,
        prior=None,
        prior_assets=set(),
        replacements_for=lambda _c: [("moment", o) for o in offers],
        gate=_gate(tmp_path, exposed=exposed),
    )
    return run


def test_a_refill_the_exposure_head_holds_never_ships_and_the_next_one_does(tmp_path):
    run = _review(tmp_path, [_shot("held", 6), _shot("clean", 7)], exposed={"held"})

    assert [c["asset_id"] for c in run.carriers] == ["keeper", "clean"]


def test_a_slot_whose_every_refill_is_held_stays_empty(tmp_path):
    run = _review(tmp_path, [_shot("held", 6)], exposed={"held"})

    assert [c["asset_id"] for c in run.carriers] == ["keeper"]
