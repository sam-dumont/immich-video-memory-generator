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

from immich_memories.analysis.annotation_line_fields import content_of
from immich_memories.analysis.editorial_exposure_chains import ChainHold
from immich_memories.analysis.editorial_shareability_audience import (
    _clean,
    _exposure_flag,
    _parse_exposure_verdict,
    audience_check_prompt,
    audience_exposure_prompt,
    exposure_flagged,
    exposure_members,
    parse_audience_verdict,
)
from immich_memories.analysis.editorial_story_shortlist import capture_space_available
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure

NEVER_AUTO = "never_auto"
REVIEW = "review"
OWNER_SOURCE = "owner"
OWNER_CLEARED = "cleared"
VERDICTS = ("share", "family_only", "do_not_show")  # loosest to strictest
PROMPT_VERSION = "shareability-check-v5-family-milestones-and-private-content"
AUDIENCE_PROMPT_VERSION = "audience-evidence-v17-every-finding-needs-its-activity"
AUDIENCE_CHECK_POLICY_VERSION = "all-captioned-carrier-members-v1"
_AUDIENCE_HEADS = frozenset(
    {"nsfw_marqo", "uncovered_person", "people", "children", "doc_docling", "venue", "location"}
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


def load_detector_heads(
    store_path: Path | str, asset_ids: Iterable[str], head_versions: Mapping[str, str]
) -> dict[str, dict[str, str]]:
    """The audience heads banked for these sources, at the versions this run reads.

    An attached clip has no annotation line -- nothing describes it, nothing selects it --
    so its detector rows are read from the bank directly. A source with no row is absent,
    which is what every clip looked like before anything read one.
    """
    ids = list(dict.fromkeys(asset_ids))
    wanted = {head: version for head, version in head_versions.items() if head in _AUDIENCE_HEADS}
    out: dict[str, dict[str, str]] = {}
    if not ids or not wanted:
        return out
    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = con.execute(
                f"select asset_id, head, version, label from head_facts where asset_id in ({marks}) "  # noqa: S608
                "order by asset_id, head, version",
                chunk,
            )
            for asset_id, head, version, label in rows:
                if wanted.get(str(head)) == str(version) and _clean(label):
                    out.setdefault(str(asset_id), {})[str(head)] = _clean(label)
    finally:
        con.close()
    return out


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


_HEAD_BITS = re.compile(r"^[a-z_]+=")
_HEAD_ALIASES = {"nsfw": "nsfw_marqo", "document": "doc_docling"}


def _is_line_metadata(part: str) -> bool:
    """A tag the pipeline wrote other than the detector heads, which are read next, or a
    caption field that is not the caption itself (#1256)."""
    if _HEAD_BITS.match(part):
        return False
    return not content_of(part) or part.startswith(("setting:", "exposure:"))


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
    chains: Mapping[str, ChainHold] | None = None,
    companion_heads: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Conserve each rendered member's observations and detector provenance separately.

    IDs determine stable member aliases but never enter the returned model evidence. An
    uncaptioned primary asset or still member remains an explicit evidence gap. A Live
    Photo's clip has no annotation line at all -- nothing describes it -- so ``companion_heads``
    carries what the detectors banked about it, read straight from the store.
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
        uncaptioned, resolved, flags, records, companion_heads or {}
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
    chain = _chain_evidence(ordered, chains or {})
    if chain is not None:
        evidence["exposure_chain"] = chain
    return evidence


def _chain_evidence(
    asset_ids: Sequence[str], chains: Mapping[str, ChainHold]
) -> dict[str, Any] | None:
    """The densest flagged capture run any member of this unit sits in, without naming ids."""
    holds = [chains[asset_id] for asset_id in asset_ids if asset_id in chains]
    if not holds:
        return None
    return max(holds, key=lambda hold: (hold.flagged, hold.size)).as_evidence()


def _companion_evidence(
    uncaptioned: Sequence[str],
    resolved: Mapping[str, tuple[str, tuple[Any, ...]]],
    flags: Mapping[str, Sequence[FlagRow]],
    records: Mapping[str, Mapping[str, Any]],
    banked: Mapping[str, Mapping[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    """Detectors, flags and body warnings that an uncaptioned companion contributes."""
    heads = {i: _companion_heads(resolved[i][1], banked.get(i, {})) for i in uncaptioned}
    detectors = [heads[i] for i in uncaptioned if heads[i]]
    rows = _flag_records([row for i in uncaptioned for row in flags.get(i, ())])
    warnings: list[dict[str, Any]] = []
    for asset_id in uncaptioned:
        warning = _companion_body_warning(
            asset_id, heads[asset_id], flags.get(asset_id, ()), records, len(warnings) + 1
        )
        if warning is not None:
            warnings.append(warning)
    return detectors, rows, warnings


def _companion_heads(line_heads: Sequence[Any], banked: Mapping[str, str]) -> dict[str, str]:
    """The clip's own detector answers; a banked row is added, never overwritten."""
    return _audience_detectors(line_heads) | {
        head: _clean(label) for head, label in sorted(banked.items()) if head in _AUDIENCE_HEADS
    }


def _member_annotation(annotation: Any, fallback_line: str) -> tuple[str, tuple[Any, ...]]:
    """The caption and the heads this member contributes."""
    if annotation is None:
        return _fallback_audience_annotation(fallback_line)
    return (
        _clean(getattr(annotation, "description", None)),
        tuple(getattr(annotation, "heads", ())),
    )


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
    detectors: Mapping[str, str],
    flags: Sequence[FlagRow],
    picture_records: Mapping[str, Mapping[str, Any]],
    index: int,
) -> dict[str, Any] | None:
    """A clearance is local to the warned companion, never inherited from its still."""
    if any(row.source == OWNER_SOURCE and row.flag == OWNER_CLEARED for row in flags):
        return None
    warnings = [record for record in _flag_records(flags) if _exposure_flag(record)]
    if not exposure_flagged(detectors) and not warnings:
        return None
    return {
        "member": f"v{index}",
        "detectors": dict(detectors),
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


def check_audience(
    judge: Any, evidence: Mapping[str, Any], stage: str, *, activity_answer: str | None = None
) -> dict[str, Any]:
    """Private activities have final authority; exposure review can only tighten a share.

    `activity_answer` is this carrier's answer to the activity question when it was already
    asked in a batch; it is read exactly as the reply to a single question would be.
    """
    return floors_under(evidence, _read_audience(judge, evidence, stage, activity_answer))


def activity_question(evidence: Mapping[str, Any]) -> bool | None:
    """Whether the check asks the activity question of this evidence at all (None when it does
    not: a member has no caption), and if so whether the nudity finding may be answered."""
    members = evidence.get("members", ())
    if not members or any(not member["caption"] for member in members):
        return None
    precise_body, _hold, _fields = _observed_body(evidence, members)
    return not precise_body


def floors_under(evidence: Mapping[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Holds no reading can lift: a model reading only ever adds holds.

    A detector that flagged a still, a video's frames or a Live Photo's clip keeps the unit
    in the family whatever the captions or a body observation say afterwards: the owner
    prefers a false positive to a miss. Nor can the reader see the three minutes around a
    capture. All of these only ever take a unit further from `share`.
    """
    if result["verdict"] != "share":
        return result
    if finding := _head_hold(evidence):
        return result | {"verdict": "family_only", "finding": finding, "why": _HEAD_WHY[finding]}
    chain = evidence.get("exposure_chain")
    if not chain:
        return result
    return result | {
        "verdict": "family_only",
        "finding": "exposure_chain",
        "why": "most of this capture run is flagged for exposure",
        "exposure_chain": chain,
    }


_HEAD_WHY = {
    "exposure_evidence": "an exposure detector flagged this picture",
    "clip_exposure": "the attached clip is flagged for exposure",
}


def _head_hold(evidence: Mapping[str, Any]) -> str:
    """Which detector hold stands; only the owner's own clearance on the pool page lifts one."""
    if any(
        exposure_flagged(member.get("detectors", {})) and not _owner_cleared(member)
        for member in evidence.get("members", ())
    ):
        return "exposure_evidence"
    # A companion warning is only written for a clip the owner has not cleared.
    if any(
        exposure_flagged(warning.get("detectors", {}))
        for warning in evidence.get("companion_body_warnings", ())
    ):
        return "clip_exposure"
    return ""


def _owner_cleared(member: Mapping[str, Any]) -> bool:
    return any(
        row.get("source") == OWNER_SOURCE and row.get("flag") == OWNER_CLEARED
        for row in member.get("flags", ())
    )


def _read_audience(
    judge: Any, evidence: Mapping[str, Any], stage: str, activity_answer: str | None = None
) -> dict[str, Any]:
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
    if activity_answer is not None:
        result["activity_asked_in_batch"] = True
    try:
        raw = (
            activity_answer
            if activity_answer is not None
            else judge.ask(
                activity_stage,
                audience_check_prompt(evidence, allow_nudity=not precise_body),
                max_tokens=120,
            )
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
    positive = sorted(exposure_members(evidence))
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
    occupied: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, int]:
    """The first unused pool unit that passes its own gate, and the checks it cost."""
    checked = 0
    for unit in pool:
        if str(unit.get("asset_id")) in used or not capture_space_available(unit, occupied):
            continue
        verdict = verdict_of(unit)
        if verdict is not None:
            checked += 1
        if verdict is None or allowed(verdict, audience):
            return unit, checked
    return None, checked


# The story-level editorial context a refused carrier may lend its replacement. Everything
# else — its standing, its depicted moment, its motion/timing/frame/speech/evidence fields —
# is the refused asset's own; a replacement that inherited it would be judged and rendered
# as a material it is not.
STORY_CONTEXT_KEYS = ("story_episode", "story_role", "story_weight")


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
    Reserve every surviving carrier before choosing replacements. A replacement must fit the
    same capture spacing as normal selection before buying its audience verdict. If none fits
    and passes, the slot is dropped; no other anchor fills the gap.
    """
    verdicts = [verdict_of(carrier) for carrier in carriers]
    kept = [
        carrier
        for carrier, verdict in zip(carriers, verdicts, strict=True)
        if verdict is None or allowed(verdict, audience)
    ]
    used = {str(c.get("asset_id")) for c in carriers}
    log: dict[str, Any] = {
        "audience": audience,
        "checked": sum(verdict is not None for verdict in verdicts),
        "tightened": [],
        "substituted": [],
        "dropped": [],
    }
    for carrier, verdict in zip(carriers, verdicts, strict=True):
        if verdict is None or allowed(verdict, audience):
            continue
        log["tightened"].append(
            {"asset_id": carrier.get("asset_id"), "event": carrier.get("event"), "verdict": verdict}
        )
        replacement, checked = _first_shareable(pool_for(carrier), used, verdict_of, audience, kept)
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
        new = (
            {key: carrier[key] for key in STORY_CONTEXT_KEYS if key in carrier}
            | dict(replacement)
            | {"why": replacement.get("why") or "Audience-safe alternative within the same story"}
        )
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
