"""Printed portraits cannot enter through either draft or recovery admission."""

import pytest

from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources
from immich_memories.analysis.editorial_picture_admission import PictureAdmission
from immich_memories.analysis.editorial_story_standing import StandingGate
from tests.annotation_rows import add_rows, annotation_store
from tests.test_annotation_lines import candidate, reader


def test_a_corroborated_document_is_refused_in_draft_and_recovery():
    versions = {"frame_kind": "public-v1", "people": "public-v1", "doc_docling": "det-v2"}
    store = annotation_store()
    add_rows(
        store,
        "head_facts",
        *(
            {
                "asset_id": "printed",
                "head": head,
                "version": versions[head],
                "label": label,
                "confidence": confidence,
            }
            for head, label, confidence in (
                ("frame_kind", "screen_or_document", 0.8),
                ("people", "two", 0.8),
                ("doc_docling", "photograph", 0.4),
            )
        ),
    )
    batch = reader(store, candidate("printed"), head_versions=versions).lines_for(("printed",))
    assert not batch.missing_asset_ids
    lines = batch.as_mapping()
    excluded = excluded_carrier_sources(lines, evidence=batch.records_by_id())
    shot = {
        "asset_id": "printed",
        "taken": "2024-02-04T09:00:00+00:00",
        "kind": "still",
        "members": ["printed"],
        "seconds": 3.5,
        "story_episode": "story",
        "story_weight": "major",
        "moment": "moment",
    }
    # Strong standing and a major story's life/context allowance must not undo source evidence.
    standing = StandingGate(
        lambda _asset: 2,
        line_of=lines.get,
        life=lambda _asset: True,
        unit_by_asset={"printed": ("family", shot)},
        pictures_of={"story": 5},
    )
    admission = PictureAdmission(standing, None, None, excluded=excluded)

    kept, refusals = admission.admit([shot], tier_of={})
    recovery = admission.admits(shot, cut=[], tier_of={}, recovering=True)

    assert not kept
    assert refusals[0].rule == "source"
    assert recovery is not None and recovery.rule == "source"
    assert recovery.detail == "document-corroborated"


@pytest.mark.parametrize("protection", ["none", "favourite", "required"])
def test_planner_material_uses_corroborated_documents_before_family_seats(tmp_path, protection):
    from dataclasses import replace

    from immich_memories.analysis.editorial_rule_reader import NoModelJudge
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_material import build_material, read_wall
    from tests.test_editorial_duration_planner_integration import source

    prepared = source(tmp_path, seconds=24, pictures=5)
    asset_id = "picture-000"
    records = dict(prepared.audience_annotations)
    records[asset_id] = replace(
        records[asset_id],
        heads=(("frame_kind", "screen_or_document"), ("doc_docling", "photograph")),
        head_confidences=(("frame_kind", 0.8), ("doc_docling", 0.4)),
    )
    prepared = replace(prepared, audience_annotations=records)
    if protection == "favourite":
        prepared.assets[asset_id].is_favorite = True
    elif protection == "required":
        prepared = replace(prepared, owner_required_asset_ids=(asset_id,))
    material = build_material(
        prepared,
        StructurePlannerPorts(judge=NoModelJudge(), thumbnail_hash=lambda _asset: None),
        read_wall(prepared),
    )

    expected = {asset_id: "document-corroborated"} if protection == "none" else {}
    assert material.document_sources == expected
    selectable = {unit["asset_id"] for units in material.units.values() for unit in units}
    assert (asset_id in selectable) == (protection != "none")


@pytest.mark.parametrize(
    ("kind", "frame_probability", "photograph_probability", "refused"),
    [
        ("screen_or_document", 0.8, 0.4, True),
        ("people_moment", 0.8, 0.4, False),
        ("screen_or_document", 0.8, 0.9, False),
        ("screen_or_document", 0.8, None, False),
        ("screen_or_document", None, 0.4, False),
        ("screen_or_document", 0.2, 0.4, False),
        ("screen_or_document", 0.5, 0.4, False),
        ("screen_or_document", 0.8, 0.5, False),
    ],
)
def test_ambiguous_or_missing_evidence_does_not_refuse_a_picture(
    kind, frame_probability, photograph_probability, refused
):
    from immich_memories.analysis.annotation_lines import AssetAnnotationLine

    record = AssetAnnotationLine(
        "photo",
        "2024-02-04T09:00 | people=two",
        heads=(("frame_kind", kind), ("doc_docling", "photograph")),
        head_confidences=tuple(
            (head, probability)
            for head, probability in (
                ("frame_kind", frame_probability),
                ("doc_docling", photograph_probability),
            )
            if probability is not None
        ),
    )

    excluded = excluded_carrier_sources({"photo": record.text}, evidence={"photo": record})

    assert ("photo" in excluded) == refused


def test_late_preparation_refuses_a_corroborated_document_and_refills(tmp_path):
    import hashlib
    import json
    from dataclasses import replace

    import numpy as np

    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.editorial_story_fixtures import ControlledStoryJudge
    from tests.test_editorial_duration_planner_integration import source

    captured = source(tmp_path, seconds=60, pictures=20)
    inspected = set()

    # WHY: the preparation boundary supplies classifier evidence for a candidate that was
    # unread when the draft began; the real planner and admission decide the refill.
    def prepare(rows):
        fresh = {row["asset_id"] for row in rows} - inspected
        inspected.update(fresh)
        if "picture-015" in fresh:
            record = captured.audience_annotations["picture-015"]
            captured.audience_annotations["picture-015"] = replace(
                record,
                heads=(("frame_kind", "screen_or_document"), ("doc_docling", "photograph")),
                head_confidences=(("frame_kind", 0.8), ("doc_docling", 0.4)),
            )
        return bool(fresh)

    prints = {f"picture-{n:03}": np.eye(20)[0 if n == 1 else n] for n in range(20)}
    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ControlledStoryJudge(),
            thumbnail_hash=lambda a: hashlib.sha256(a.encode()).hexdigest()[:16],
            scene_print=prints.get,
            prepare_candidates=prepare,
        ),
    ).plan

    kept = {row["asset_id"] for row in plan["carriers"]}
    assert "picture-015" not in kept
    assert "picture-016" in kept
    audit = json.loads(
        (captured.artifact_dir / "derived-decisions/picture-admission.private.json").read_text()
    )
    assert {"rule": "source", "detail": "document-corroborated"}.items() <= next(
        row for row in audit["checks"] if row["asset_id"] == "picture-015"
    ).items()
