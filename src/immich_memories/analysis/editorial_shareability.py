"""Shareability rules shared by the production structure editor and matrix adapter.

Two layers, in this order, both text-only:

1. Deterministic. An asset carrying a ``never_auto`` flag in the annotation store's ``flags``
   table, from any source, is never a carrier. A Live Photo burst with one flagged member is
   excluded whole (the most-constrained verdict rule). The asset remains evidence for the
   memory-worthy judgement; only a flag written by the owner (``source='owner'``,
   ``flag='cleared'``) lifts the exclusion. No model is asked.
2. Tighten-only check. Every selected carrier is put to the reader once, on its line and
   its flags (including the exposure-source flags the editorial line hides): ``share``,
   ``family_only`` or ``do_not_show``. Verdicts combine to the strictest; a sendable export keeps
   only ``share``. A refused carrier is replaced from the same anchor's shareable pool or its slot
   is dropped. Never a refill from another anchor.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_shareability_audience import (
    _clean,
    _exposure_flag,
    _exposure_members,
    _parse_exposure_verdict,
    audience_check_prompt,
    audience_exposure_prompt,
    parse_audience_verdict,
)
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure

NEVER_AUTO = "never_auto"
REVIEW = "review"
OWNER_SOURCE = "owner"
OWNER_CLEARED = "cleared"
VERDICTS = ("share", "family_only", "do_not_show")  # loosest to strictest
PROMPT_VERSION = "shareability-check-v5-family-milestones-and-private-content"
AUDIENCE_PROMPT_VERSION = "audience-evidence-v12-complete-activity-assessment"
AUDIENCE_CHECK_POLICY_VERSION = "all-captioned-carrier-members-v1"
_AUDIENCE_HEADS = frozenset(
    {"nsfw_marqo", "people", "children", "doc_docling", "swim", "venue", "location"}
)


@dataclass(frozen=True)
class FlagRow:
    asset_id: str
    flag: str
    reason: str
    source: str


def load_flags(store_path: Path | str, asset_ids: Iterable[str]) -> dict[str, tuple[FlagRow, ...]]:
    """Every flag row for the given assets, exposure sources included.

    The production line renderer hides exposure-source flags on purpose (they are noisy on
    landscapes); the shareability check must see them, so this reads the table directly.
    """
    ids = list(dict.fromkeys(asset_ids))
    out: dict[str, list[FlagRow]] = {}
    if not ids:
        return {}
    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = con.execute(
                f"select asset_id, flag, evidence, source from flags where asset_id in ({marks}) "  # noqa: S608
                "order by asset_id, flag, source",
                chunk,
            )
            for asset_id, flag, evidence, source in rows:
                out.setdefault(str(asset_id), []).append(
                    FlagRow(str(asset_id), _clean(flag), _reason(evidence), _clean(source))
                )
    finally:
        con.close()
    return {k: tuple(v) for k, v in out.items()}


def never_auto_ids(flags: Mapping[str, Sequence[FlagRow]]) -> frozenset[str]:
    out = set()
    for asset_id, rows in flags.items():
        if any(r.source == OWNER_SOURCE and r.flag == OWNER_CLEARED for r in rows):
            continue
        if any(r.flag == NEVER_AUTO for r in rows):
            out.add(asset_id)
    return frozenset(out)


def unit_members(unit: Mapping[str, Any]) -> tuple[str, ...]:
    ids = [unit.get("asset_id"), *unit.get("members", ()), *unit.get("video_ids", ())]
    return tuple(dict.fromkeys(str(i) for i in ids if i))


_LINE_METADATA_PREFIXES = (
    "LIVE PHOTO",
    "VIDEO ",
    "at ",
    "with ",
    "setting:",
    "exposure:",
    "resolution:",
    "duration:",
    "FLAGGED ",
    "STARRED ",
    "SOFT ",
    "BLOWN OUT",
)
_HEAD_ALIASES = {"nsfw": "nsfw_marqo", "document": "doc_docling"}


def _is_line_metadata(part: str) -> bool:
    return bool(re.match(r"^\d{4}-\d\d-\d\d", part)) or part.startswith(_LINE_METADATA_PREFIXES)


def _audience_head_labels(labels: Sequence[tuple[str, str]]) -> dict[str, str]:
    named = ((_HEAD_ALIASES.get(head, head), label) for head, label in labels)
    return {head: label.strip() for head, label in named if head in _AUDIENCE_HEADS}


def _fallback_audience_annotation(line: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Project legacy captured lines without forwarding names, time or editorial metadata."""
    caption = ""
    heads: dict[str, str] = {}
    for part in (piece.strip() for piece in line.split(" | ")):
        if not part or _is_line_metadata(part):
            continue
        labels = re.findall(r"(?:^|,\s*)([a-z_]+)=([^,]+)", part)
        if labels:
            heads.update(_audience_head_labels(labels))
        elif not caption:
            caption = part
    return caption, tuple(sorted(heads.items()))


def evidence_for_unit(
    unit: Mapping[str, Any],
    annotations: Mapping[str, Any],
    flags: Mapping[str, Sequence[FlagRow]],
    fallback_lines: Mapping[str, str],
    *,
    picture_records: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Conserve each rendered member's observations and detector provenance separately.

    IDs determine stable member aliases but never enter the returned model evidence. A Live
    Photo's uncaptioned video companion is covered by its documented still association; an
    uncaptioned primary asset or still member remains an explicit evidence gap.
    """
    material = {str(i) for i in (unit.get("asset_id"), *unit.get("members", ())) if i}
    companion_ids = {str(i) for i in unit.get("video_ids", ()) if i} - material
    records = picture_records or {}
    ordered = sorted(material | companion_ids)
    resolved = {
        asset_id: _member_annotation(annotations.get(asset_id), fallback_lines.get(asset_id, ""))
        for asset_id in ordered
    }
    # Preserve companion warnings even though such a companion has no independent caption.
    uncaptioned = [i for i in ordered if i in companion_ids and not resolved[i][0]]
    skipped = set(uncaptioned)
    detectors, companion_flags, warnings = _companion_evidence(
        uncaptioned, resolved, flags, records
    )
    evidence: dict[str, Any] = {
        "version": AUDIENCE_CHECK_POLICY_VERSION,
        "members": [
            _member_evidence(index, resolved[i], flags.get(i, ()), records.get(i))
            for index, i in enumerate((i for i in ordered if i not in skipped), start=1)
        ],
        "companion_detectors": detectors,
        "companion_flags": companion_flags,
    }
    # Leave unaffected evidence and request keys byte-identical. The scoped records bind
    # only warnings that a material still's body observation cannot resolve for its video.
    if warnings:
        evidence["companion_body_warnings"] = warnings
    return evidence


def _companion_evidence(
    uncaptioned: Sequence[str],
    resolved: Mapping[str, tuple[str, tuple[Any, ...]]],
    flags: Mapping[str, Sequence[FlagRow]],
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    """Detectors, flags and body warnings that an uncaptioned companion still contributes."""
    detectors = [_audience_detectors(resolved[i][1]) for i in uncaptioned if resolved[i][1]]
    rows = _flag_records([row for i in uncaptioned for row in flags.get(i, ())])
    warnings: list[dict[str, Any]] = []
    for asset_id in uncaptioned:
        warning = _companion_body_warning(
            asset_id, resolved[asset_id][1], flags.get(asset_id, ()), records, len(warnings) + 1
        )
        if warning is not None:
            warnings.append(warning)
    return detectors, rows, warnings


def _member_annotation(annotation: Any, fallback_line: str) -> tuple[str, tuple[Any, ...]]:
    if annotation is None:
        return _fallback_audience_annotation(fallback_line)
    return _clean(getattr(annotation, "description", None)), tuple(getattr(annotation, "heads", ()))


def _audience_detectors(heads: Sequence[Any]) -> dict[str, str]:
    return {str(head): _clean(label) for head, label in sorted(heads) if head in _AUDIENCE_HEADS}


def _flag_records(rows: Sequence[FlagRow]) -> list[dict[str, str]]:
    return [
        {"flag": row.flag, "reason": row.reason, "source": row.source}
        for row in sorted(rows, key=lambda row: (row.flag, row.source, row.reason))
    ]


def _member_evidence(
    index: int,
    annotation: tuple[str, tuple[Any, ...]],
    rows: Sequence[FlagRow],
    record: Mapping[str, Any] | None,
) -> dict[str, Any]:
    caption, heads = annotation
    member_evidence: dict[str, Any] = {
        "member": f"p{index}",
        "caption": caption,
        "detectors": _audience_detectors(heads),
        "flags": _flag_records(rows),
    }
    body_observation = _visual_body_observation(record)
    if body_observation is not None:
        member_evidence["body_observation"] = body_observation
    return member_evidence


def _companion_body_warning(
    asset_id: str,
    heads: Sequence[tuple[str, str]],
    flags: Sequence[FlagRow],
    picture_records: Mapping[str, Mapping[str, Any]],
    index: int,
) -> dict[str, Any] | None:
    """A clearance is local to the warned companion, never inherited from its still."""
    if any(row.source == OWNER_SOURCE and row.flag == OWNER_CLEARED for row in flags):
        return None
    detectors = _audience_detectors(heads)
    warnings = [record for record in _flag_records(flags) if _exposure_flag(record)]
    if detectors.get("nsfw_marqo") != "yes" and not warnings:
        return None
    return {
        "member": f"v{index}",
        "detectors": detectors,
        "flags": warnings,
        "body_observation": _visual_body_observation(picture_records.get(asset_id)),
        "scope": "uncaptioned video companion; material still observation is not its clearance",
    }


def _visual_body_observation(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Only the available five-field image producer can establish this body fact."""
    if not isinstance(record, Mapping) or record.get("status") != "available":
        return None
    from immich_memories.analysis import editorial_picture_facts as picture_facts

    producer = record.get("producer")
    if (
        not isinstance(producer, Mapping)
        or producer.get("pass_version") != picture_facts.STAGE_VERSION
        or producer.get("prompt_version") != picture_facts.PROMPT_VERSION
        or producer.get("schema_version") != picture_facts.SCHEMA_VERSION
        or producer.get("prompt_sha256")
        != hashlib.sha256(picture_facts.PROMPT.encode()).hexdigest()
        or producer.get("schema_sha256") != picture_facts._digest(picture_facts.RESPONSE_SCHEMA)
        or any(
            not isinstance(record.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", record[key])
            for key in ("identity", "input_sha256", "image_sha256")
        )
    ):
        return None
    facts = record.get("facts")
    state = facts.get("uncovered_person") if isinstance(facts, Mapping) else None
    return {
        "uncovered_person": state
        if isinstance(state, str) and state in picture_facts.BODY_STATES
        else "invalid",
        "record_identity": record["identity"],
        "input_sha256": record["input_sha256"],
        "image_sha256": record["image_sha256"],
        "producer_identity": picture_facts._digest(dict(producer)),
        "scope": "direct observation of the sampled picture; no unseen motion assertion",
    }


def terminal_body_hold(
    unit: Mapping[str, Any],
    picture_records: Mapping[str, Mapping[str, Any]],
    witness_id: str,
) -> dict[str, Any] | None:
    """A bound positive suffices to reject a sendable unit, not to clear its other members.

    The caller owns the export check. Unobserved members remain absent from the picture
    bank and must still be observed if another candidate needs them. This is an acquisition
    stop record, never a complete audience assessment or a fabricated model answer.
    """
    material = tuple(
        dict.fromkeys(str(i) for i in (unit.get("asset_id"), *unit.get("members", ())) if i)
    )
    body = _visual_body_observation(picture_records.get(witness_id))
    if witness_id not in material or body is None or body["uncovered_person"] != "yes":
        return None
    aliases = {asset_id: f"p{index + 1}" for index, asset_id in enumerate(material)}
    observations = {
        aliases[asset_id]: record
        for asset_id in material
        if (record := _visual_body_observation(picture_records.get(asset_id))) is not None
    }
    unit_identity = {
        "asset_id": unit.get("asset_id"),
        "material_members": material,
        "all_source_members": unit_members(unit),
        "kind": unit.get("kind"),
    }
    unobserved = [aliases[asset_id] for asset_id in material if asset_id not in picture_records]
    return {
        "policy": AUDIENCE_PROMPT_VERSION,
        "verdict": "family_only",
        "parsed": True,
        "finding": "nudity_shirtless_or_underwear",
        "why": "Direct picture observation identifies an uncovered person",
        "activity": None,
        "exposure": None,
        "body_observations": observations,
        "acquisition_stop": {
            "version": "bound-positive-body-stop-v1",
            "unit_sha256": hashlib.sha256(
                json.dumps(unit_identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "witness_member": aliases[witness_id],
            "material_member_count": len(material),
            "observed_members": [aliases[i] for i in material if i in picture_records],
            "unobserved_members": unobserved,
            "evidence_scope": "positive witness only; no clearance of other members or activities",
        },
    }


def audience_check_key(evidence: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"policy": AUDIENCE_PROMPT_VERSION, "evidence": evidence},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _body_state_hold(states: Sequence[Any]) -> dict[str, Any] | None:
    if any(not isinstance(state, str) or state not in {"yes", "no", "unclear"} for state in states):
        return {
            "parsed": False,
            "finding": "invalid_body_observation",
            "why": "Invalid direct body observation",
        }
    if "yes" in states:
        return {
            "parsed": True,
            "finding": "nudity_shirtless_or_underwear",
            "why": "Direct picture observation identifies an uncovered person",
        }
    if "unclear" in states:
        return {
            "parsed": False,
            "finding": "undecided_body_observation",
            "why": "Direct picture observation cannot resolve visible body coverage",
        }
    return None


def _companion_body_hold(warnings: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    unresolved = [
        warning["member"]
        for warning in warnings
        if not isinstance(warning.get("body_observation"), Mapping)
        or warning["body_observation"].get("uncovered_person") != "no"
    ]
    if not unresolved:
        return None
    return {
        "parsed": False,
        "finding": "unresolved_companion_exposure",
        "why": "A material still cannot clear the warned video's unobserved body coverage",
        "unresolved_companions": unresolved,
    }


def _observed_body(
    evidence: Mapping[str, Any], members: Sequence[Any]
) -> tuple[bool, dict[str, Any] | None, dict[str, Any]]:
    """Body coverage cannot establish the activity, so a hold never answers on its own.

    Even an uncovered person's caption may describe private care without naming it. A full
    audience assessment always asks the activity classifier; callers may use
    ``terminal_body_hold`` only when the body finding already excludes the carrier from
    their requested audience.
    """
    body = {member["member"]: member.get("body_observation") for member in members}
    if not all(isinstance(record, Mapping) for record in body.values()):
        return False, None, {}
    fields: dict[str, Any] = {"body_observations": body}
    hold = _body_state_hold([record.get("uncovered_person") for record in body.values()])
    companion_warnings = evidence.get("companion_body_warnings", ())
    if companion_warnings and hold is None:
        fields["companion_body_warnings"] = companion_warnings
        hold = _companion_body_hold(companion_warnings)
    return True, hold, fields


def check_audience(judge: Any, evidence: Mapping[str, Any], stage: str) -> dict[str, Any]:
    """Private activities have final authority; exposure review can only tighten a share."""
    members = evidence.get("members", ())
    missing = sorted(member["member"] for member in members if not member["caption"])
    result: dict[str, Any] = {
        "policy": AUDIENCE_PROMPT_VERSION,
        "verdict": "family_only",
        "parsed": False,
        "missing_members": missing,
        "activity": None,
        "exposure": None,
    }
    if missing or not members:
        return result | {
            "finding": "unavailable_evidence",
            "why": "rendered member has no caption evidence",
        }
    precise_body, body_hold, body_fields = _observed_body(evidence, members)
    result.update(body_fields)
    activity_stage = f"{stage}-activity"
    try:
        raw = judge.ask(
            activity_stage,
            audience_check_prompt(evidence, allow_nudity=not precise_body),
            max_tokens=120,
        )
    except TextCompletionFailure as exc:
        # The gateway has exhausted its bounded recovery; this carrier stays undecided.
        return result | {
            "finding": "undecided_activity",
            "why": "activity classification exhausted bounded completion attempts",
            "failed_stage": activity_stage,
            "completion_failure": exc.as_record(),
        }
    activity = parse_audience_verdict(raw, evidence, allow_nudity=not precise_body)
    if activity is None:
        return result | {
            "finding": "invalid_activity_verdict",
            "why": "invalid content classification",
            "activity": {"raw": raw, "parsed": False},
        }
    result.update(parsed=True, activity=activity[1], verdict=activity[0], why=activity[1]["why"])
    if activity[0] != "share":
        return result | {"finding": "private_activity"}
    if body_hold is not None:
        # Activity can tighten a body finding, but never clear it. In particular, an uncovered
        # newborn during a delivery cannot hide the procedure behind a family-only body verdict.
        return result | body_hold | {"verdict": "family_only"}
    if precise_body:
        return result | {
            "finding": "none",
            "exposure": {
                "parsed": True,
                "basis": "direct_visual_body_observations",
                "clearances": [
                    {"member": member["member"], "basis": "uncovered_person_no"}
                    for member in members
                ],
                "unresolved_members": [],
            },
        }
    positive = sorted(_exposure_members(evidence))
    if not positive:
        return result | {"finding": "none"}
    return _review_exposure(judge, evidence, stage, result, positive)


def _review_exposure(
    judge: Any,
    evidence: Mapping[str, Any],
    stage: str,
    result: dict[str, Any],
    positive: Sequence[str],
) -> dict[str, Any]:
    result["exposure"] = {"parsed": True, "groups": [], "clearances": [], "unresolved_members": []}
    for start in range(0, len(positive), 2):
        group = positive[start : start + 2]
        exposure_stage = f"{stage}-exposure-{start // 2 + 1}"
        try:
            raw = judge.ask(
                exposure_stage,
                audience_exposure_prompt(evidence, group),
                max_tokens=120,
            )
        except TextCompletionFailure as exc:
            result["exposure"]["parsed"] = False
            result["exposure"]["groups"].append(
                {"parsed": False, "status": "failed", "requested_members": group}
            )
            result["exposure"]["unresolved_members"] = group
            result["exposure"]["unchecked_members"] = positive[start + 2 :]
            return result | {
                "verdict": "family_only",
                "parsed": False,
                "finding": "undecided_exposure",
                "why": "caption coverage classification exhausted bounded completion attempts",
                "failed_stage": exposure_stage,
                "completion_failure": exc.as_record(),
            }
        exposure = _parse_exposure_verdict(raw, evidence, group)
        if exposure is None:
            result["exposure"]["parsed"] = False
            result["exposure"]["groups"].append(
                {"raw": raw, "parsed": False, "requested_members": group}
            )
            return result | {
                "verdict": "family_only",
                "parsed": False,
                "finding": "invalid_exposure_verdict",
                "why": "invalid caption coverage classification",
            }
        result["exposure"]["groups"].append(exposure)
        result["exposure"]["clearances"].extend(exposure["clearances"])
        if exposure["unresolved_members"]:
            result["exposure"]["unresolved_members"] = exposure["unresolved_members"]
            result["exposure"]["unchecked_members"] = positive[start + 2 :]
            return result | {
                "verdict": "family_only",
                "finding": "unresolved_exposure",
                "why": "positive exposure signal remains unexplained by caption evidence",
            }
    return result | {"finding": "none"}


def partition_units(
    units: Sequence[Mapping[str, Any]], never_auto: frozenset[str] | set[str]
) -> tuple[list, list]:
    """Split units into (shareable, excluded); a unit is excluded when ANY member is flagged."""
    kept: list = []
    excluded: list = []
    for unit in units:
        (excluded if any(m in never_auto for m in unit_members(unit)) else kept).append(unit)
    return kept, excluded


def tighten(*verdicts: str | None) -> str:
    """The strictest of the given verdicts; nothing known means 'share'."""
    known = [v for v in verdicts if v in VERDICTS]
    if not known:
        return VERDICTS[0]
    return max(known, key=VERDICTS.index)


def allowed(verdict: str, audience: str = "family") -> bool:
    if verdict == "share":
        return True
    if verdict == "family_only":
        return audience == "family"
    return False


def _first_shareable(
    pool: Sequence[Mapping[str, Any]],
    used: set[str],
    verdict_of: Callable[[Mapping[str, Any]], str | None],
    audience: str,
) -> tuple[Mapping[str, Any] | None, int]:
    """The first unused pool unit that passes its own gate, and the checks it cost."""
    checked = 0
    for unit in pool:
        if str(unit.get("asset_id")) in used:
            continue
        verdict = verdict_of(unit)
        if verdict is not None:
            checked += 1
        if verdict is None or allowed(verdict, audience):
            return unit, checked
    return None, checked


def apply_gate(
    carriers: Sequence[dict],
    *,
    verdict_of: Callable[[Mapping[str, Any]], str | None],
    pool_for: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]],
    audience: str = "family",
) -> tuple[list[dict], dict[str, Any]]:
    """Keep, replace or drop each carrier by its shareability verdict.

    ``verdict_of`` returns the (already tightened) verdict for a unit or None when no check applies.
    ``pool_for`` returns the same anchor's remaining shareable units, in preference order.
    A refused carrier is replaced by the first pool unit that itself passes; otherwise its slot is
    dropped. The film may shrink; no other anchor fills the gap.
    """
    kept: list[dict] = []
    used = {str(c.get("asset_id")) for c in carriers}
    log: dict[str, Any] = {
        "audience": audience,
        "checked": 0,
        "tightened": [],
        "substituted": [],
        "dropped": [],
    }
    for carrier in carriers:
        verdict = verdict_of(carrier)
        if verdict is not None:
            log["checked"] += 1
        if verdict is None or allowed(verdict, audience):
            kept.append(carrier)
            continue
        log["tightened"].append(
            {"asset_id": carrier.get("asset_id"), "event": carrier.get("event"), "verdict": verdict}
        )
        replacement, checked = _first_shareable(pool_for(carrier), used, verdict_of, audience)
        log["checked"] += checked
        if replacement is None:
            log["dropped"].append(
                {
                    "asset_id": carrier.get("asset_id"),
                    "event": carrier.get("event"),
                    "verdict": verdict,
                }
            )
            continue
        used.add(str(replacement.get("asset_id")))
        new = {
            **carrier,
            **replacement,
            "why": f"{replacement.get('why') or 'shareable rung'} (replaces an unshareable carrier)",
        }
        kept.append(new)
        log["substituted"].append(
            {
                "from": carrier.get("asset_id"),
                "to": replacement.get("asset_id"),
                "event": carrier.get("event"),
                "verdict": verdict,
            }
        )
    kept.sort(key=lambda x: str(x.get("taken", "")))
    return kept, log


def _reason(evidence: object) -> str:
    text = _clean(evidence)
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return text[:120]
        if isinstance(obj, dict):
            bits = []
            for key, value in obj.items():
                if value in (None, "", [], False):
                    continue
                rendered = (
                    ",".join(str(v) for v in value) if isinstance(value, list) else str(value)
                )
                bits.append(f"{key}={rendered}")
            return " ".join(bits)[:120]
    return text[:120]
