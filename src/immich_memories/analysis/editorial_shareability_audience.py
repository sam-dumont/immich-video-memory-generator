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
from collections.abc import Callable, Mapping, Sequence
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
- breastfeeding_or_expressing_milk: breastfeeding, nursing, latching, or expressing/pumping breast milk is described, including under a cover.
- bathing: a person is described being bathed, bathing or showering in a bath, tub, sink or shower, even when water or clothing covers the body.
- toileting_or_changing: using a toilet/potty, diaper changing, or changing a baby on a changing table.
- intimate_hygiene: washing, wiping or other care of private body parts.
- nudity_shirtless_or_underwear: a person is described nude, shirtless/bare-torsoed, or wearing only underwear.
- identifying_record: an identity card, personal contact details, patient identifiers on a document/wristband, or readable private administrative records.
- graphic_medical_procedure: an invasive operation/surgery or delivery in progress, or visible open bloody wounds.
- sexual_content: sexual activity, sexting, or explicitly sexual posing, including underwear presented sexually.
- adult_changing: an adult undressing or changing clothes with their private body exposed. Ordinary shirtless adults, an adult helping a child dress, and changing a baby's diaper do not match this category.

Classify the depicted content, not the importance or personal sensitivity of the life event. There is no category for a private health result or an intimate family moment. A pregnancy result or birth announcement alone is not an identifying record, and neither is legible text on its own: a logo, a race bib or shirt number, a sign, a label or a screen title does not identify anyone. Clothed hospital visits, treatment, recovery, and holding a newborn are not intimate hygiene. A medical venue, bedroom, parenthood, or close physical contact alone does not establish a listed activity. A cover does not cancel a described breastfeeding, bathing, toileting or hygiene activity. Ordinary holding/play, haircuts and bottle feeding do not match these categories by themselves. An empty bathroom with nobody in it is not bathing. A pool, the sea, a lake, a river, a paddling pool, swimming or water play is never bathing, including a parent holding a baby in the water and a baby's swimming lesson. Holding a baby, even close to the chest or under a blanket, is not breastfeeding unless feeding is described, and an animal nursing its young is not breastfeeding. A sleeveless top, a tank top or a vest is clothing. A person at the beach whose clothing is not described is not nudity, and a statue or artwork is not a person's nudity.

Graphic medical content means the procedure or open wound itself is depicted. Ordinary clothed treatment, preparation for surgery, recovery afterward, a healed scar, or staff standing in an operating room do not establish a graphic medical procedure. Pregnancy results and birth announcements are not delivery in progress. Costume or fake blood and an animal eating are not a medical procedure.

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
    supported = _finding_supported(finding, evidence)
    verdict = (
        _UNSUPPORTED_VERDICT[finding]
        if not supported
        else "do_not_show"
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
    } | ({} if supported else {"supported": False})


# What a category the reader names stands on. The 30B read any legible text -- a bib, a logo,
# a sign -- as an identifying record (0 of 23 right, 09-21), and a shirtless adult holding a
# child as adult changing; the reader's own `why` is not evidence. A category with nothing
# under it falls back to what the evidence does show: an unsupported record holds nothing, an
# unsupported undressing is still an uncovered adult and stays in the family. Detector, body
# and chain holds are applied after this and are untouched by it.
_UNSUPPORTED_VERDICT = {
    "identifying_record": "share",
    "adult_changing": "family_only",
    "breastfeeding_or_expressing_milk": "share",
    "bathing": "share",
    "nudity_shirtless_or_underwear": "share",
    "sexual_content": "share",
    "intimate_hygiene": "share",
    "graphic_medical_procedure": "share",
    "toileting_or_changing": "share",
}
_NOT_A_RECORD_LABELS = frozenset({"photograph", "logo", "icon"})
_RECORD_TEXT = re.compile(
    r"\b(?:id(?:entity)? cards?|passports?|driver'?s? licen[cs]es?|"
    r"(?:hospital|patient|name) (?:wristbands?|bracelets?|labels?)|wristbands? with|"
    r"patient (?:identifiers?|details|records?|names?)|medical records?|prescriptions?|"
    r"boarding pass(?:es)?|(?:bank|credit|debit|insurance) (?:cards?|statements?)|"
    r"personal (?:details|information|data)|contact details|phone numbers?|"
    r"(?:home|postal|street) address(?:es)?|addressed envelopes?)\b",
    re.IGNORECASE,
)
_UNDRESSING_TEXT = re.compile(
    r"\b(?:undress\w*|naked|nude|nudity|genitals?|private (?:body )?parts|"
    r"strip(?:s|ped|ping)?|underwear|(?:chang\w*|(?:takes?|taking|took) off) (?:\w+ )?"
    r"(?:clothes|clothing|underwear|pants|trousers|bra|dress|swimsuit|swimwear))\b",
    re.IGNORECASE,
)
# The 30B read "a woman holding a baby close to her chest" as breastfeeding, a mother owl nursing
# her owlets too, and a baby in a paddling pool, a river or an empty bathroom as bathing (09-24).
# Breastfeeding needs a breast word and a person; bathing needs a person in a bath or shower. A
# nsfw head still holds on its own.
_ANIMAL = (
    r"(?:gorillas?|monkeys?|apes?|owls?|owlets?|birds?|chicks?|hens?|ducks?|swans?|cats?|kittens?|"
    r"dogs?|pupp(?:y|ies)|cows?|calf|calves|goats?|sheep|lambs?|pigs?|piglets?|horses?|foals?|"
    r"elephants?|lions?|lionesses?|tigers?|bears?|deer|fawns?|rabbits?|seals?|whales?|dolphins?|"
    r"kangaroos?|pandas?|animals?|mammals?)"
)
# A person, and not the first word of "mother owl" or "baby elephant".
_PERSON_TEXT = re.compile(
    r"\b(?:person|people|man|men|woman|women|adults?|parents?|mother|mom|mum|father|dad|"
    r"bab(?:y|ies)|infants?|newborns?|child|children|kids?|toddlers?|boys?|girls?|sons?|"
    r"daughters?|patients?|surgeons?|doctors?|someone)\b(?!\s+" + _ANIMAL + r"\b)",
    re.IGNORECASE,
)
# A sleeveless top, a tank top, a vest or a dress is clothing (09-24): nudity needs the body.
_UNCOVERED_TEXT = re.compile(
    r"\b(?:shirtless|topless|bare[\s-]?(?:chested|torso\w*|skin\w*|back|breast\w*|bottom\w*)|"
    r"nude|naked|nudity|unclothed|undress\w*|underwear|bra|briefs|boxers|panties|lingerie|"
    r"diapers?|nappy|nappies|bikinis?|swim\w*|trunks|towels?|uncovered|"
    r"expos(?:ed|ing)(?:\s+\w+){0,2}\s+(?:chest|breasts?|torso|genitals?|bottom|buttocks|body|skin))\b",
    re.IGNORECASE,
)
# "Feeding a child" at a table is a spoon or a hand (09-24): only a breast, nursing, latching or
# pumping word describes breastfeeding.
_BREAST_TEXT = re.compile(
    r"\b(?:breast\w*|nurs(?:es|ed|ing)|latch\w*|pump(?:s|ed|ing)?|express\w* (?:\w+ )?milk)\b",
    re.IGNORECASE,
)
# "Bathroom" is a room, not a bath; a pool, the sea, a lake or a river is never bathing (09-24).
_BATH_TEXT = re.compile(
    r"\b(?:bath(?!rooms?\b)\w*|bathe\w*|tubs?|shower\w*|sinks?|basins?)\b", re.IGNORECASE
)
_OPEN_WATER_TEXT = re.compile(
    r"\b(?:pools?|paddling|sea|seaside|ocean|lakes?|rivers?|streams?|ponds?|beach\w*|swim\w*)\b",
    re.IGNORECASE,
)
# The 30B read kisses, a wedding, dancing and costumes as sexual content (09-24): only a sexual
# act or an exposed intimate body part described in the caption supports it.
_SEXUAL_TEXT = re.compile(
    r"\b(?:sex|sexual\w*|intercourse|masturbat\w*|oral sex|porn\w*|erotic\w*|explicit\w*|"
    r"genitals?|genitalia|penis|vagina|vulva|naked|nude|topless|lingerie)\b",
    re.IGNORECASE,
)
# Hand washing, tooth brushing and a face cloth were read as intimate hygiene (09-24): the care
# has to reach a private body part, a nappy, or be wiping or toilet use.
_INTIMATE_TEXT = re.compile(
    r"\b(?:genital\w*|private (?:body )?parts?|bottoms?|buttocks|groin|vulva|penis|"
    r"wip(?:e|es|ed|ing)|(?:nappy|nappies|diapers?) chang\w*|chang\w* (?:[\w']+ ){0,2}(?:nappy|nappies|diapers?)|"
    r"on (?:the|a) (?:toilet|potty))\b",
    re.IGNORECASE,
)
# A coffin, molten lava, a blood-stained race number and a newborn in a hospital bed were read as
# graphic medical content (09-24): an injury, a wound, surgery or blood on a person has to be named.
_MEDICAL_TEXT = re.compile(
    r"\b(?:surg(?:ery|eries|ical)|operat\w* on|incisions?|stitch(?:es|ed|ing)|sutur\w*|"
    r"wounds?|wounded|injur(?:y|ies|ed)|bleed\w*|gash(?:es)?|fractures?|"
    r"covered in blood|blood (?:on|from|pour\w*|drip\w*|runs?|running)|bloody|"
    r"giving birth|deliver\w* (?:a|the|her) baby)\b",
    re.IGNORECASE,
)
# An empty toilet, urinals and a MEN sign were read as toileting (09-24): somebody has to be on
# the toilet or potty, using it, or having a nappy changed.
_TOILETING_TEXT = re.compile(
    r"\b(?:(?:(?:sits?|sitting|sat|seated|squat\w*) )?on (?:the |a |an |his |her |their )?"
    r"(?:\w+ )?(?:toilet|potty|loo)|us(?:es|ing|ed) (?:the |a )?(?:toilet|potty|loo)|"
    r"potty[\s-]training|pee(?:s|ing)?|poo(?:p|ping|ped)?|urinat\w*|"
    r"(?:nappy|nappies|diapers?) chang\w*|chang\w* (?:[\w']+ ){0,2}(?:nappy|nappies|diapers?))\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:no|not|none|without|nor|never)\b", re.IGNORECASE)


def _finding_supported(finding: str, evidence: Mapping[str, Any]) -> bool:
    supports = _SUPPORT.get(finding)
    if supports is None:
        return True
    return any(supports(member) for member in evidence.get("members", ()))


def _with_person(pattern: re.Pattern[str]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda member: _states(pattern, member) and _states(_PERSON_TEXT, member)


def _bathing(member: Mapping[str, Any]) -> bool:
    """A person in a bath, tub, sink or shower, and no open water in the caption."""
    caption = str(member.get("caption", ""))
    return (
        _states(_BATH_TEXT, member)
        and _states(_PERSON_TEXT, member)
        and not _OPEN_WATER_TEXT.search(caption)
    )


def _document_label(member: Mapping[str, Any]) -> bool:
    label = member.get("detectors", {}).get("doc_docling")
    return bool(label) and label not in _NOT_A_RECORD_LABELS


def _states(pattern: re.Pattern[str], member: Mapping[str, Any]) -> bool:
    """The caption names the fact, and the few words before it do not deny it."""
    caption = str(member.get("caption", ""))
    for match in pattern.finditer(caption):
        clause = re.split(r"[.;:,]", caption[: match.start()])[-1]
        if not _NEGATION.search(" ".join(clause.split()[-4:])):
            return True
    return False


_SUPPORT: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "identifying_record": lambda m: _document_label(m) or _states(_RECORD_TEXT, m),
    "adult_changing": lambda m: _states(_UNDRESSING_TEXT, m),
    "breastfeeding_or_expressing_milk": _with_person(_BREAST_TEXT),
    "nudity_shirtless_or_underwear": lambda m: _states(_UNCOVERED_TEXT, m),
    "sexual_content": lambda m: _states(_SEXUAL_TEXT, m),
    "intimate_hygiene": _with_person(_INTIMATE_TEXT),
    "graphic_medical_procedure": _with_person(_MEDICAL_TEXT),
    "toileting_or_changing": _with_person(_TOILETING_TEXT),
    "bathing": _bathing,
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
