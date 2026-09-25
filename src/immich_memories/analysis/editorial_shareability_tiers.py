"""Which audience check a preparation tier is entitled to, and why a reduced tier gets less.

The gate may only ever tighten, so each tier answers with the evidence it actually prepared:

* **full** keeps the model check. Captions exist, so the reader can be asked what a picture
  depicts and the exposure review can run.
* **no_captions** keeps the same detector evidence -- ``nsfw_marqo`` and the exposure-source
  flags -- and reads it with rules instead of sentences. Without
  this the model check refuses every uncaptioned member as ``unavailable_evidence``, which
  holds a whole cut to the family for want of a producer the tier deliberately did not run.
  It matches the model check on what it refuses and, like the tier below, never clears:
  eight findings are named only by a description, so a head seeing nothing is not a
  clearance. What it adds is a finding that says the description was absent.
* **metadata_only** prepared nothing that looked at the picture, so it holds every unit to the
  family and never says ``share``. "Nothing objected" is not a clearance when nothing looked.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from immich_memories.analysis.editorial_shareability import REVIEW, check_audience
from immich_memories.analysis.editorial_shareability_audience import exposure_members

RULE_AUDIENCE_POLICY = "audience-rules-v1-detector-heads-and-flags"
NO_EVIDENCE_AUDIENCE_POLICY = "audience-withheld-v1-no-content-evidence"

AudienceCheck = Callable[[Any, Mapping[str, Any], str], dict[str, Any]]

_HELD = {
    "exposure_evidence": "a detector or exposure flag marks this unit",
    "exposure_chain": "most of this capture run is flagged for exposure",
    "owner_review_flag": "the owner left a review flag on this unit",
}


def _flag_rows(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    members = evidence.get("members", ())
    return [
        *(row for member in members for row in member.get("flags", ())),
        *evidence.get("companion_flags", ()),
    ]


def _hold(evidence: Mapping[str, Any]) -> str:
    """The first deterministic reason to keep this unit in the family, or an empty string."""
    if exposure_members(evidence):
        return "exposure_evidence"
    if evidence.get("exposure_chain"):
        return "exposure_chain"
    if any(row.get("flag") == REVIEW for row in _flag_rows(evidence)):
        return "owner_review_flag"
    return ""


def rule_audience(_judge: Any, evidence: Mapping[str, Any], _stage: str) -> dict[str, Any]:
    """The audience verdict from detector heads and flags alone, with no model asked.

    Identical to the model check on what it *refuses* -- the same detector positives and
    the same exposure flags. It never clears, because eight of the findings that refuse a
    unit are named only by a written description: bathing, toileting, intimate hygiene, a
    medical procedure, an identifying record, and the rest. The heads cannot see them, so
    "no detector objected" is not a clearance, and the gate may only ever tighten.

    A unit with nothing against it therefore stays `family_only` and says why. To earn a
    `shareable` export, run the tier that does the reading.
    """
    result: dict[str, Any] = {
        "policy": RULE_AUDIENCE_POLICY,
        "verdict": "family_only",
        "parsed": True,
        "missing_members": [],
        "activity": None,
        "exposure": None,
    }
    if not evidence.get("members"):
        return result | {"finding": "unavailable_evidence", "why": "no rendered member to read"}
    held = _hold(evidence)
    if held:
        chain = evidence.get("exposure_chain")
        return (
            result
            | {"finding": held, "why": _HELD[held]}
            | ({"exposure_chain": chain} if chain else {})
        )
    return result | {
        "finding": "unread_private_activity",
        "why": "no detector head objected, but no description was read to clear it",
    }


CLEAN_EVIDENCE_POLICY = "audience-rules-v2-clean-evidence-under-strict-sharing"
# A room a picture can be private in whatever is in frame; the rules reader keeps the same set.
_PRIVATE_VENUES = frozenset({"bedroom", "medical", "private_facility"})
_PHOTOGRAPH = "photograph"


def _clean_heads(heads: Mapping[str, str]) -> bool:
    """Every head that could object read this frame and none did."""
    return (
        heads.get("nsfw_marqo") == heads.get("uncovered_person", "no") == "no"
        and heads.get("doc_docling", _PHOTOGRAPH) == _PHOTOGRAPH
        and heads.get("venue") not in _PRIVATE_VENUES
    )


def clean_evidence(evidence: Mapping[str, Any]) -> bool:
    """Nothing marked any picture of this unit, and the nudity detector read every one of them.

    A picture nothing looked at is not clean: an unread member, a Live clip whose detector row
    is missing a head, any flag at all or a flagged capture run keeps it in the family.
    """
    members = evidence.get("members", ())
    return (
        bool(members)
        and all(_clean_heads(member.get("detectors", {})) for member in members)
        and all(_clean_heads(heads) for heads in evidence.get("companion_detectors", ()))
        and not _flag_rows(evidence)
        and not evidence.get("exposure_chain")
        and not evidence.get("companion_body_warnings")
    )


def rule_audience_with_clean_share(
    judge: Any, evidence: Mapping[str, Any], stage: str
) -> dict[str, Any]:
    """The rules check for a film shared outside the household, with strict sharing on.

    Where every head that could object read the unit and none did, and nothing flagged it,
    the unit is `share`: strict sharing already keeps out anything a head or a flag marked, so
    what is left is what no detector saw anything in. A private moment only a caption would
    name stays possible here; that is the price of a shareable film with no captions, and the
    reason a model tier, when there is one, still reads the captions first.
    """
    result = rule_audience(judge, evidence, stage)
    if result["finding"] != "unread_private_activity" or not clean_evidence(evidence):
        return result
    return result | {
        "policy": CLEAN_EVIDENCE_POLICY,
        "verdict": "share",
        "finding": "clean_evidence",
        "why": "every detector read it and none objected, and nothing flagged it",
    }


def withheld_audience(_judge: Any, _evidence: Mapping[str, Any], _stage: str) -> dict[str, Any]:
    """Family viewing for every unit, because nothing in this tier looked at the picture."""
    return {
        "policy": NO_EVIDENCE_AUDIENCE_POLICY,
        "verdict": "family_only",
        "parsed": True,
        "missing_members": [],
        "activity": None,
        "exposure": None,
        "finding": "no_content_evidence",
        "why": "prepared without the detector evidence the gate reads",
    }


def audience_check_for(tier: str, *, strict_sharing: bool = False) -> AudienceCheck:
    """The check this preparation tier may use; anything unrecognised gets the strictest.

    `strict_sharing` is set for a shareable film under `editorial.strict_sharing`: then the
    rules tier may clear a unit on clean evidence alone.
    """
    if tier == "full":
        return check_audience
    if tier == "no_captions":
        return rule_audience_with_clean_share if strict_sharing else rule_audience
    return withheld_audience


def sharing_refusal(config: Any, level: str | None = None) -> str | None:
    """Why this install can't cut a shareable film, before the cut starts; None when it can.

    Every tier with the detectors can: a NAS clears what they read as clean. `metadata_only`
    ran none of them, so nothing it holds can ever be cleared, and the film would be empty.
    """
    chosen = level or config.defaults.sharing
    if chosen != "shareable" or config.editorial.preparation.demands_models:
        return None
    return (
        "A shareable film needs the detectors, and this install prepares at the "
        f"{config.editorial.preparation.tier} tier, which runs none. Set "
        "advanced.editorial.preparation.tier to no_captions (and run `immich-memories models "
        "fetch`), or cut a family film."
    )
