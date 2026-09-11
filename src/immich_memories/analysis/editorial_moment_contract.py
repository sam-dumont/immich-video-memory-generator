"""Stable text contract for the proven post-card moment editor."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from immich_memories.analysis.episode_scan_request import candidate_who_and_where
from immich_memories.analysis.selection_source_groups import EditorialGroup
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object

__all__ = [
    "MAX_REASON_CHARS",
    "MAX_THESIS_CHARS",
    "MAX_THREAD_CHARS",
    "SELECTION_PROMPT_VERSION",
    "SELECTION_SCHEMA",
    "THESIS_PROMPT_VERSION",
    "THESIS_SCHEMA",
    "Description",
    "ModelAnswer",
    "Moment",
    "MomentCard",
    "_card_line",
    "_read_selection",
    "_read_thesis",
    "_selection_prompt",
    "_thesis_prompt",
]

THESIS_SCHEMA = "description-memory-thesis-v2"
SELECTION_SCHEMA = "description-moment-selection-v2"
THESIS_PROMPT_VERSION = "description-memory-thesis-prompt-v5"
SELECTION_PROMPT_VERSION = "description-moment-selection-prompt-v4-audited"
MAX_THESIS_CHARS = 500
MAX_THREAD_CHARS = 220
MAX_REASON_CHARS = 400


@dataclass(frozen=True)
class Description:
    asset_id: str
    text: str


@dataclass(frozen=True)
class ModelAnswer:
    prompt: str
    raw: str
    wall_seconds: float


@dataclass(frozen=True)
class Moment:
    alias: str
    group: EditorialGroup
    descriptions: tuple[Description, ...]


@dataclass(frozen=True)
class MomentCard:
    moment: Moment
    summary: str
    answer: ModelAnswer | None
    people_metadata: tuple[dict[str, Any], ...] = ()


def _moment_context(moment: Moment) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            annotation
            for candidate in moment.group.candidates
            for annotation in candidate_who_and_where(candidate)
        )
    )


def _duration_label(seconds: float) -> str:
    rounded = max(0, round(seconds))
    minutes, remainder = divmod(rounded, 60)
    return f"{minutes}m{remainder:02d}s" if minutes else f"{remainder}s"


def _card_line(card: MomentCard) -> str:
    candidates = card.moment.group.candidates
    kinds = Counter(candidate.media_kind for candidate in candidates)
    media = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    favourites = sum(candidate.favourite for candidate in candidates)
    span = (candidates[-1].taken_at - candidates[0].taken_at).total_seconds()
    context = _moment_context(card.moment)
    context_field = f" | context {' ; '.join(context)}" if context else ""
    people_field = (
        " | people_metadata "
        + json.dumps(card.people_metadata, ensure_ascii=False, separators=(",", ":"))
        if card.people_metadata
        else ""
    )
    return (
        f"{card.moment.alias} | {candidates[0].taken_at.isoformat()} | "
        f"{len(candidates)} visuals ({media}) | {favourites} favourites | "
        f"span {_duration_label(span)}{context_field}{people_field} | {card.summary}"
    )


def _thesis_prompt(cards: tuple[MomentCard, ...], memory_type: str) -> str:
    wall = "\n".join(_card_line(card) for card in cards)
    shape = json.dumps(
        {
            "schema_version": THESIS_SCHEMA,
            "thesis": "plain statement of what this candidate set is about",
            "sustained_threads": [
                {
                    "summary": "thread visible across separated dates",
                    "evidence_moment_ids": ["M001", "M020"],
                }
            ],
            "turning_points": [
                {
                    "summary": "one-off fact that changes the reading",
                    "evidence_moment_ids": ["M030"],
                }
            ],
            "ordinary_texture": ["grounded contrast or texture"],
        },
        separators=(",", ":"),
    )
    return f"""You are preparing a {memory_type.strip()} from the complete chronological moment wall below.

Read the wall as a whole in two stages before writing the thesis:

1. Identify sustained threads evidenced by moments on separated dates. A dense named event on one
   day is one beat, not a sustained thread, unless the wall shows its preparation or aftermath.
   Before treating that event as the explanation for a recurring activity, test the chronology. If
   the same activity continues after the event, or the cards do not state a causal link, describe the
   activity as the broader thread and the event as one culmination within it. Sequence alone does not
   turn every earlier scene into preparation.
2. Inspect every card for a one-off turning point that changes how the period is understood. Its
   importance is not proportional to how many pictures show it. It must add meaning beyond the
   sustained thread: a scheduled highlight or culmination inside that thread is not a separate
   turning point. For any memory, a visually quiet record may establish a consequential change whose
   importance far exceeds its spectacle.

Then state, plainly, what makes this candidate set specifically worth remembering. Integrate the
sustained and turning-point evidence when both exist; do not merely list topics.
The memory type changes the editorial question: a chronological recap needs representative life
texture, a trip memory needs the experience of the journey/place, a person souvenir needs that
person's relationships and change, and a surprise memory may follow an unexpected discovered thread.

Before answering, reject any proposed thesis that could describe almost any ordinary month. Frequency
is evidence of texture, not automatically of importance. For a chronological recap, do not let the
loudest or most densely photographed single event stand in for the whole month. A specific combination
can be the answer even when neither thread is sufficient alone. If no credible turning point exists,
return an empty turning_points list rather than inventing one.

This is a provisional orientation, not a keep/reject decision. Name ordinary texture that prevents the
thesis from flattening the period. Favourite counts are owner evidence, but do not let them invent
meaning. Use only facts stated in the cards. Prose values must use no double quotes or backslashes.

MOMENT WALL
{wall}

Return only one complete JSON object with exactly these keys:
{shape}
The entire answer is that single JSON object: no markdown fences, no headers, no prose before or
after it, and every keep entry is an object with moment_id and reason - never a bare ID string."""


def _selection_prompt(
    cards: tuple[MomentCard, ...],
    *,
    memory_type: str,
    thesis: dict[str, Any],
    capacity: int,
    required_ids: tuple[str, ...],
) -> str:
    wall = "\n".join(_card_line(card) for card in cards)
    thesis_text = json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))
    shape = json.dumps(
        {
            "schema_version": SELECTION_SCHEMA,
            "keep": [
                {
                    "moment_id": "M001",
                    "reason": "why this moment belongs, at most 15 words",
                }
            ],
            "audit_summary": ("concise explicit account of the allocation and its main tradeoff"),
            "comparisons": [
                {
                    "kept_moment_id": "M001",
                    "rejected_moment_id": "M002",
                    "reason": (
                        "why the retained moment has more on-screen value under scarcity, "
                        "at most 15 words"
                    ),
                }
            ],
            "overall_reason": "how the cut expresses this memory, at most 30 words",
        },
        separators=(",", ":"),
    )
    return f"""You are editing a {memory_type.strip()} from the complete chronological moment wall below.

The provisional reading of the same wall is:
{thesis_text}

The owner has already admitted these favourite-bearing moments: {json.dumps(required_ids)}. They
consume {len(required_ids)} of {capacity} slots. Do not return them. Choose at most
{capacity - len(required_ids)} ADDITIONAL whole moments from the rest. Fewer is allowed when the
material does not support filling capacity. A retained moment initially contributes one final visual;
its underlying assets remain available for the later fine cut. Do not sample or choose assets inside
a moment here.

Use the thesis as editorial orientation, not as a keyword or relevance gate. A moment may establish,
advance, complicate, contrast with, or give necessary ordinary texture to the thesis. Preserve the
specific beats that make the reading credible, not just repeated examples of its broad topics. Cover
the sustained thread across separated dates and preserve credible turning points. Do not spend several
slots on near-equivalent beats from one dense named event while quieter, personal, or separated moments
carry the same thread more fully.
Prefer a lived scene showing action, relationship, expression, place, or atmosphere over material
whose value is only to label, measure, summarize, or prove the same thread. An evidentiary record
earns a slot only when the card establishes a consequential fact that no lived scene can carry. Do
not treat all records as junk; apply the distinction to what each moment contributes.
Clear or distinctive is not enough by itself under scarcity. Do not invent people, relationships,
causality, or events. Keep moment IDs in chronological order. Reasons must use no double quotes or
backslashes.

Treat the result as a sequence a person will watch, not an evidence packet that explains the thesis.
Before answering, audit the non-favourite draft under scarcity. For each retained moment, ask whether
its value is present on screen or exists mainly in the reason you wrote for it. Compare the weakest
retained moment with rejected lived moments carrying the same thread. Replace explanatory evidence
when a rejected moment expresses that thread through human action, relationship, or emotion. A known
relationship can make a lived moment more specific, but a recognized name alone does not earn a slot.
Prefer a moment that performs several necessary editorial jobs at once over separate one-purpose
moments: for example, one lived scene may carry a sustained thread, a relationship, and ordinary
texture together. Do not infer that combination; every contribution must be stated in its card.

Make the allocation inspectable. In audit_summary, state the main tradeoff you made under scarcity.
In comparisons, give up to eight decisive head-to-head choices from the non-favourite pool: one
moment you kept, the strongest plausible alternative it displaced, and the visible reason the kept
moment wins. Return an empty comparisons list when no non-favourite moment is retained or when every
available non-favourite moment earns runtime and therefore none was rejected. This is a concise
evidence-backed rationale, not hidden chain-of-thought. Comparison IDs must be different;
kept_moment_id must appear in keep and
rejected_moment_id must not.

MOMENT WALL
{wall}

Return only one complete JSON object with exactly these keys:
{shape}
The entire answer is that single JSON object: no markdown fences, no headers, no prose before or
after it, and every keep entry is an object with moment_id and reason - never a bare ID string."""


def _bounded_list(value: object, *, max_chars: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("model text list has the wrong shape")
    parsed = tuple(bounded_model_text(item, max_chars=max_chars) for item in value)
    if any(item is None for item in parsed):
        raise ValueError("model text list contains unsafe text")
    return tuple(item for item in parsed if item is not None)


def _exact_envelope(raw: str, *, keys: set[str], schema: str, refusal: str) -> dict[str, Any]:
    payload = final_json_object(raw)
    if payload is None or set(payload) != keys or payload.get("schema_version") != schema:
        raise ValueError(refusal)
    return payload


def _read_thesis(
    raw: str,
    valid_ids: frozenset[str],
    *,
    require_sustained: bool = True,
) -> dict[str, Any]:
    payload = _exact_envelope(
        raw,
        keys={
            "schema_version",
            "thesis",
            "sustained_threads",
            "turning_points",
            "ordinary_texture",
        },
        schema=THESIS_SCHEMA,
        refusal="memory thesis answer is not the exact JSON envelope",
    )
    thesis = bounded_model_text(payload.get("thesis"), max_chars=MAX_THESIS_CHARS)
    sustained = _read_grounded_threads(payload.get("sustained_threads"), valid_ids=valid_ids)
    turning = _read_grounded_threads(payload.get("turning_points"), valid_ids=valid_ids)
    texture = _bounded_list(payload.get("ordinary_texture"), max_chars=MAX_THREAD_CHARS)
    if thesis is None or (require_sustained and not sustained):
        raise ValueError("memory thesis needs a thesis and at least one sustained thread")
    return {
        "thesis": thesis,
        "sustained_threads": sustained,
        "turning_points": turning,
        "ordinary_texture": list(texture),
    }


def _read_grounded_threads(
    value: object,
    *,
    valid_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("grounded thesis list has the wrong shape")
    rows: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"summary", "evidence_moment_ids"}:
            raise ValueError("grounded thesis row has the wrong shape")
        summary = bounded_model_text(row.get("summary"), max_chars=MAX_THREAD_CHARS)
        evidence = row.get("evidence_moment_ids")
        if summary is None or not isinstance(evidence, list) or not evidence:
            raise ValueError("grounded thesis row needs prose and evidence")
        # A duplicated ID is unambiguous — dedupe mechanically instead of
        # refusing (a temp-0 model repeated one twice through its repair,
        # killing a case; prefer-don't-refuse). Unknown IDs stay a raise:
        # that is a real grounding failure.
        evidence_ids = tuple(dict.fromkeys(str(item) for item in evidence))
        if not set(evidence_ids) <= valid_ids:
            raise ValueError("grounded thesis evidence must be unique known moment IDs")
        rows.append({"summary": summary, "evidence_moment_ids": list(evidence_ids)})
    return rows


def _read_keep_rows(
    rows: object,
    *,
    valid_ids: frozenset[str],
    capacity: int,
    excluded_ids: frozenset[str],
) -> list[dict[str, str]]:
    if not isinstance(rows, list) or len(rows) > capacity:
        raise ValueError("moment selection exceeds capacity or has the wrong shape")
    keep: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"moment_id", "reason"}:
            raise ValueError("moment selection row has the wrong shape")
        moment_id = row.get("moment_id")
        reason = bounded_model_text(row.get("reason"), max_chars=MAX_REASON_CHARS)
        if (
            not isinstance(moment_id, str)
            or moment_id not in valid_ids
            or moment_id in excluded_ids
            or reason is None
        ):
            raise ValueError("moment selection row is not grounded")
        keep.append({"moment_id": moment_id, "reason": reason})
    return keep


def _read_comparisons(
    rows: list[Any],
    *,
    keep_ids: tuple[str, ...],
    valid_ids: frozenset[str],
    excluded_ids: frozenset[str],
) -> list[dict[str, str]]:
    comparisons: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "kept_moment_id",
            "rejected_moment_id",
            "reason",
        }:
            raise ValueError("moment selection comparison has the wrong shape")
        kept_id = row.get("kept_moment_id")
        rejected_id = row.get("rejected_moment_id")
        comparison_reason = bounded_model_text(row.get("reason"), max_chars=MAX_REASON_CHARS)
        if (
            not isinstance(kept_id, str)
            or kept_id not in keep_ids
            or not isinstance(rejected_id, str)
            or rejected_id not in valid_ids
            or rejected_id in keep_ids
            or rejected_id in excluded_ids
            or rejected_id == kept_id
            or comparison_reason is None
        ):
            raise ValueError("moment selection comparison is not grounded")
        comparisons.append(
            {
                "kept_moment_id": kept_id,
                "rejected_moment_id": rejected_id,
                "reason": comparison_reason,
            }
        )
    return comparisons


def _read_selection(
    raw: str,
    valid_ids: frozenset[str],
    capacity: int,
    *,
    excluded_ids: frozenset[str] = frozenset(),
    allow_empty_comparisons: bool = False,
) -> dict[str, Any]:
    payload = _exact_envelope(
        raw,
        keys={
            "schema_version",
            "keep",
            "audit_summary",
            "comparisons",
            "overall_reason",
        },
        schema=SELECTION_SCHEMA,
        refusal="moment selection answer is not the exact JSON envelope",
    )
    keep = _read_keep_rows(
        payload.get("keep"),
        valid_ids=valid_ids,
        capacity=capacity,
        excluded_ids=excluded_ids,
    )
    keep_ids = tuple(row["moment_id"] for row in keep)
    if len(set(keep_ids)) != len(keep_ids):
        raise ValueError("moment selection contains duplicate IDs")
    expected_order = {card_id: index for index, card_id in enumerate(sorted(valid_ids))}
    if tuple(sorted(keep_ids, key=expected_order.__getitem__)) != keep_ids:
        raise ValueError("moment selection is not chronological")
    audit_summary = bounded_model_text(payload.get("audit_summary"), max_chars=MAX_THESIS_CHARS)
    raw_comparisons = payload.get("comparisons")
    rejected_ids = (valid_ids - excluded_ids) - set(keep_ids)
    if (
        audit_summary is None
        or not isinstance(raw_comparisons, list)
        or len(raw_comparisons) > 8
        or (keep_ids and rejected_ids and not raw_comparisons and not allow_empty_comparisons)
    ):
        raise ValueError("moment selection audit has the wrong shape")
    comparisons = _read_comparisons(
        raw_comparisons,
        keep_ids=keep_ids,
        valid_ids=valid_ids,
        excluded_ids=excluded_ids,
    )
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=MAX_THESIS_CHARS)
    if overall is None:
        raise ValueError("moment selection overall reason is unsafe")
    return {
        "keep": keep,
        "audit_summary": audit_summary,
        "comparisons": comparisons,
        "overall_reason": overall,
    }
