"""What the audience reader is asked, and how its answers are read.

The captions are the last thing on the wire in both prompts: everything above them is
byte-identical per variant, so a server that reuses a prefix reads the vocabulary and the
policy once rather than once per carrier (#981).

The vocabulary below is owner-defined depicted content, not an audience decision: the reader
only names content and per-person coverage, and :mod:`editorial_shareability` maps those
answers onto ``share`` / ``family_only`` / ``do_not_show``. Detector activation decides which
members are put to the coverage reader at all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _exposure_flag(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("reason", ""))
    if re.search(r"\bexposure\s*[=:]\s*(?:none|no)\b", reason, re.IGNORECASE):
        return False
    return "exposure" in str(row.get("source", "")).casefold() or bool(
        re.search(r"\bexposure\s*[=:]", reason, re.IGNORECASE)
    )


def exposure_flagged(heads: Mapping[str, str]) -> bool:
    """Either exposure detector says a person is uncovered here.

    `nsfw_marqo` is the floor and stays it: it catches more. The distilled
    `uncovered_person` head is a second opinion that is quieter and more often right about
    why, and on the by-eye set it fired on none of the 26 pictures the judge called a wrong
    refusal, against the shipped detector's two. Neither ever clears the other, and a bank
    with no `uncovered_person` row reads exactly as it did before there was one.
    """
    return "yes" in (heads.get("nsfw_marqo"), heads.get("uncovered_person"))


def exposure_members(evidence: Mapping[str, Any]) -> set[str]:
    if any(_exposure_flag(row) for row in evidence.get("companion_flags", ())) or any(
        exposure_flagged(heads) for heads in evidence.get("companion_detectors", ())
    ):
        return {member["member"] for member in evidence.get("members", ())}
    return {
        member["member"]
        for member in evidence.get("members", ())
        if exposure_flagged(member.get("detectors", {}))
        or any(_exposure_flag(row) for row in member.get("flags", ()))
    }


ACTIVITY_CONTENT_PROMPT = """Read the image captions and classify the depicted content. Choose one finding from this closed vocabulary:

- none: no matching content is described.
- breastfeeding_or_expressing_milk: breastfeeding or expressing/pumping breast milk, including under a cover.
- bathing: a person bathing or showering, even when water or clothing covers the body.
- toileting_or_changing: using a toilet/potty, diaper changing, or changing a baby on a changing table.
- intimate_hygiene: washing, wiping or other care of private body parts.
- nudity_shirtless_or_underwear: a person nude, shirtless/bare-torsoed, or wearing only underwear.
- identifying_record: an identity card, personal contact details, patient identifiers on a document/wristband, or readable private administrative records.
- graphic_medical_procedure: an invasive operation/surgery or delivery in progress, or visible open bloody wounds.
- sexual_content: sexual activity, sexting, or explicitly sexual posing, including underwear presented sexually.
- adult_changing: an adult undressing or changing clothes with their private body exposed. Ordinary shirtless adults, an adult helping a child dress, and changing a baby's diaper do not match this category.

Classify the depicted content, not the importance or personal sensitivity of the life event. There is no category for a private health result or an intimate family moment. A pregnancy result or birth announcement alone is not an identifying record. Clothed hospital visits, treatment, recovery, and holding a newborn are not intimate hygiene. A medical venue, bedroom, parenthood, or close physical contact alone does not establish a listed activity. A cover does not cancel a described breastfeeding, bathing, toileting or hygiene activity. An empty bathroom/toilet, ordinary holding/play, haircuts and bottle feeding do not match these categories by themselves. Swimming is not bathing: a pool, a lake or the sea, including a parent holding a baby in the water and a baby's swimming lesson, does not match these categories.

Graphic medical content means the procedure or open wound itself is depicted. Ordinary clothed treatment, preparation for surgery, recovery afterward, a healed scar, or staff standing in an operating room do not establish a graphic medical procedure. Pregnancy results and birth announcements are not delivery in progress.

Any matching picture makes its finding apply to this group. If several match, choose the strongest supported category: sexual_content, adult_changing, graphic_medical_procedure, or identifying_record before any other category. Ordinary newborn care, breastfeeding, a baby's bath or diaper change, and a shirtless baby remain ordinary family content; they do not establish sexual content or adult changing. Use what the captions describe; do not invent a possible activity to explain the scene.

Return one JSON object only: {{"finding":"one category above","why":"brief described content supporting it, at most 12 words"}}. Do not make an audience/export verdict.

Captions:
{captions}"""

ACTIVITY_CONTENT_FINDINGS = frozenset(
    {
        "none",
        "breastfeeding_or_expressing_milk",
        "bathing",
        "toileting_or_changing",
        "intimate_hygiene",
        "nudity_shirtless_or_underwear",
        "identifying_record",
        "graphic_medical_procedure",
        "sexual_content",
        "adult_changing",
    }
)
DO_NOT_SHOW_FINDINGS = frozenset(
    {
        "breastfeeding_or_expressing_milk",
        "bathing",
        "toileting_or_changing",
        "intimate_hygiene",
        "graphic_medical_procedure",
        "identifying_record",
        "sexual_content",
        "adult_changing",
    }
)


def audience_check_prompt(evidence: Mapping[str, Any], *, allow_nudity: bool = True) -> str:
    """Classify owner-defined depicted content; code owns its audience meaning."""
    captions = [
        {"picture": member["member"], "caption": member["caption"]}
        for member in evidence.get("members", ())
    ]
    prompt = ACTIVITY_CONTENT_PROMPT
    if not allow_nudity:
        prompt = "\n".join(
            line
            for line in prompt.splitlines()
            if not line.startswith("- nudity_shirtless_or_underwear:")
        )
    return prompt.format(captions=json.dumps(captions, ensure_ascii=False, separators=(",", ":")))


def _first_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model answer, ignoring whatever follows it."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_audience_verdict(
    raw: str, evidence: Mapping[str, Any], *, allow_nudity: bool = True
) -> tuple[str, dict[str, Any]] | None:
    """Map only owner-defined content categories; extra response metadata has no authority."""
    obj = _first_json_object(raw.strip())
    if (
        obj is None
        or not isinstance(obj.get("finding"), str)
        or obj["finding"] not in ACTIVITY_CONTENT_FINDINGS
        or (not allow_nudity and obj["finding"] == "nudity_shirtless_or_underwear")
        or not isinstance(obj.get("why"), str)
    ):
        return None
    finding = obj["finding"]
    verdict = (
        "do_not_show"
        if finding in DO_NOT_SHOW_FINDINGS
        else "share"
        if finding == "none"
        else "family_only"
    )
    return verdict, {
        "scope": "activity-content",
        "parsed": True,
        "finding": finding,
        "verdict": verdict,
        "why": _clean(obj["why"]),
    }


PERSON_COVERAGE_PROMPT = """Extract the humans mentioned in each caption and record the clothing or body covering described for each human separately. Use one row per person or plural group. A person referred to through a hand or arm is still a human mention.

Use exactly one observation per row:
- clothing: the caption names clothing for this person or explicitly calls this person clothed.
- body_cover: the caption explicitly describes this person wrapped or covered, such as in a blanket.
- unstated: the caption supplies neither clothing nor body-covering information for this person.

Record stated attributes, not typical or implied clothing. Clothing described for one person is that person's attribute. Holding, contact, age, relation and location are not clothing descriptions. If the caption mentions no humans, return an empty row list for that picture. Do not make a privacy, safety or export decision.

Return one JSON object only. observations maps every picture to rows of [human mention, observation]. Keep human mentions short. Use this shape:
{{"observations":{{"p1":[["a human mention","clothing|body_cover|unstated"]]}}}}

Captions:
{captions}"""


def audience_exposure_prompt(
    evidence: Mapping[str, Any],
    member_aliases: Sequence[str] | None = None,
) -> str:
    """Extract caption attributes; detector activation and audience mapping stay in code."""
    positive = set(member_aliases) if member_aliases is not None else exposure_members(evidence)
    captions = [
        {"picture": member["member"], "caption": member["caption"]}
        for member in evidence.get("members", ())
        if member["member"] in positive
    ]
    return PERSON_COVERAGE_PROMPT.format(
        captions=json.dumps(captions, ensure_ascii=False, separators=(",", ":"))
    )


_COVERAGE_OBSERVATIONS = frozenset({"clothing", "body_cover", "unstated"})


def _coverage_row(row: Any) -> bool:
    return (
        isinstance(row, list)
        and len(row) == 2
        and isinstance(row[0], str)
        and bool(row[0].strip())
        and isinstance(row[1], str)
        and row[1] in _COVERAGE_OBSERVATIONS
    )


def _parse_exposure_verdict(
    raw: str,
    evidence: Mapping[str, Any],
    member_aliases: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    obj = _first_json_object(raw)
    if obj is None or not isinstance(obj.get("observations"), dict):
        return None
    members = {member["member"]: member for member in evidence.get("members", ())}
    positive = set(member_aliases) if member_aliases is not None else exposure_members(evidence)
    observations = obj["observations"]
    if set(observations) != positive:
        return None
    for rows in observations.values():
        if not isinstance(rows, list) or not all(_coverage_row(row) for row in rows):
            return None
    unresolved = {
        alias for alias, rows in observations.items() if any(row[1] == "unstated" for row in rows)
    }
    return {
        "scope": "model-caption-person-coverage",
        "parsed": True,
        "observations": observations,
        "observation_basis": "model interpretation; person inventory and attributes are unverified",
        "captions": {alias: members[alias]["caption"] for alias in sorted(positive)},
        "clearances": [
            {
                "member": alias,
                "basis": "no_person" if not observations[alias] else "clothed_or_covered",
            }
            for alias in sorted(positive)
            if alias not in unresolved
        ],
        "requested_members": sorted(positive),
        "unresolved_members": sorted(unresolved),
    }
