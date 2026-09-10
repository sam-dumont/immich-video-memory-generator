"""Selected observations conserve membership, source metadata and downstream evidence."""

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_picture_evidence import PictureEvidenceOverlay
from immich_memories.analysis.editorial_shareability import (
    FlagRow,
    check_audience,
    evidence_for_unit,
)


def annotation(asset_id, caption="A person holds a blanket."):
    return AssetAnnotationLine(
        asset_id,
        f"2020-01-01 | {caption} | setting: indoors | with Alex | STARRED by the photographer",
        description=caption,
        heads=(("nsfw_marqo", "no"),),
        stitching_burst_id="burst",
    )


def test_every_material_member_is_enriched_once_and_companion_warnings_survive():
    annotations = {i: annotation(i) for i in ("a", "b", "unoffered")}
    lines = {i: row.text for i, row in annotations.items()}
    calls = []

    def observe(asset_id):
        calls.append(asset_id)
        description = "A person wearing a coat." if asset_id == "a" else "A person is bathing."
        return {"status": "available", "description": description, "facts": {}, "identity": "fixed"}

    overlay = PictureEvidenceOverlay(annotations, lines, observe)
    unit = {"asset_id": "a", "members": ["a", "b"], "video_ids": ["motion"], "line": "stale"}
    overlay.enrich(unit)
    overlay.enrich({"asset_id": "b", "members": ["a", "b"]})
    assert calls == ["a", "b"]
    assert "bathing" in unit["line"] and "stale" not in unit["line"]
    assert "with Alex" in unit["line"] and "STARRED" in unit["line"]
    assert "holds a blanket" not in unit["line"] and "setting:" not in unit["line"]
    assert overlay.annotations["a"].stitching_burst_id == "burst"
    evidence = evidence_for_unit(
        unit,
        overlay.annotations,
        {
            "motion": (FlagRow("motion", "review", "exposure=possible", "exposure-source"),),
        },
        lines,
    )
    assert len(evidence["members"]) == 2
    assert evidence["members"][1]["caption"] == "A person is bathing."
    assert evidence["members"][0]["detectors"] == {"nsfw_marqo": "no"}
    assert evidence["companion_flags"][0]["reason"] == "exposure=possible"
    assert all("holds a blanket" in row.description for row in annotations.values())
    assert overlay.records["a"]["original_line"] == lines["a"]


def test_missing_new_observation_does_not_fall_back_to_old_safe_caption():
    row = annotation("a")
    overlay = PictureEvidenceOverlay(
        {"a": row}, {"a": row.text}, lambda _: {"status": "invalid", "description": None}
    )
    unit = {"asset_id": "a", "members": ["a"]}
    overlay.enrich(unit)
    evidence = evidence_for_unit(unit, overlay.annotations, {}, {"a": row.text})
    verdict = check_audience(None, evidence, "no-model-needed")
    assert verdict["finding"] == "unavailable_evidence"
    assert verdict["missing_members"] == ["p1"] and not verdict["parsed"]
    assert overlay.annotations["a"].heads == row.heads


def test_optional_port_preserves_existing_evidence_without_acquisition():
    row = annotation("a")
    overlay = PictureEvidenceOverlay({"a": row}, {"a": row.text}, None)
    unit = {"asset_id": "a", "line": "captured"}
    overlay.enrich(unit)
    assert unit["line"] == "captured" and not overlay.records
    assert overlay.line(unit) == row.text and overlay.annotations["a"] is row
