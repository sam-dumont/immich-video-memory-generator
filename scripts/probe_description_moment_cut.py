#!/usr/bin/env python3
"""Prototype a text-only two-pass edit over banked 400px descriptions.

The production source filter and moment grouping define the candidate set. The
model first condenses every moment into a factual card, then reads the complete
card wall twice:

1. state what this candidate set is about for the requested kind of memory;
2. choose whole moments under the duration-derived capacity, using that thesis
   as orientation rather than as a relevance gate.

All descriptions, IDs, prompts, answers, and cards are private. ``--out`` is
therefore restricted to ``~/.immich-memories-matrix``. Answers are cached after
every call, while the complete replay record is written to ``result.json``.

The ``raw`` input shape preserves the earlier prototype which nested every
literal asset description inside its production moment. The default ``cards``
shape removes the accidental advantage enjoyed by moments with many assets.
Neither shape samples assets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from immich_memories.analysis.episode_scan_request import candidate_who_and_where
from immich_memories.analysis.llm_query import DEFAULT_TEMPERATURE, query_llm
from immich_memories.analysis.selection_source import EditorialGroup
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object

DEFAULT_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
CARD_SCHEMA = "description-moment-card-v2"
THESIS_SCHEMA = "description-memory-thesis-v2"
SELECTION_SCHEMA = "description-moment-selection-v2"
THESIS_PROMPT_VERSION = "description-memory-thesis-prompt-v5"
SELECTION_PROMPT_VERSION = "description-moment-selection-prompt-v4-audited"
MAX_CARD_CHARS = 700
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


def _private_output(path: Path, parser: argparse.ArgumentParser) -> Path:
    resolved = path.expanduser().resolve()
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    if not resolved.is_relative_to(matrix):
        parser.error("--out must be inside ~/.immich-memories-matrix")
    result_path = resolved / "result.json"
    if result_path.exists():
        parser.error(f"refusing to overwrite existing result: {result_path}")
    return resolved


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", required=True, type=Path, help="Private description result.json")
    parser.add_argument("--out", required=True, type=Path, help="Private output directory")
    parser.add_argument(
        "--memory-type",
        required=True,
        help=(
            "Editorial product being made, e.g. chronological recap, trip memory, "
            "person souvenir, or surprise memory"
        ),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Use reasoning for the thesis and selection calls; fused/card construction stays fast",
    )
    parser.add_argument(
        "--thinking-budget-tokens",
        type=int,
        default=4096,
        help="oMLX top-level thinking budget for each reasoning call",
    )
    parser.add_argument("--input-shape", choices=("cards", "raw"), default="cards")
    parser.add_argument(
        "--fused-cards",
        type=Path,
        help="Private fused-moment-card result.json; skips the old text card calls",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Optional local judgment cache to reuse across prototype output directories",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Independent moment-card calls in flight; oMLX supports up to 8",
    )
    parser.add_argument("--target-duration-seconds", type=float, default=60.0)
    parser.add_argument(
        "--expected-seconds-per-final-visual",
        type=float,
        default=4.0,
        help="Expected duration of the one final visual a retained moment initially contributes",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    args.out = _private_output(args.out, parser)
    if args.fused_cards is not None:
        args.fused_cards = args.fused_cards.expanduser().resolve()
        matrix = (Path.home() / ".immich-memories-matrix").resolve()
        if not args.fused_cards.is_relative_to(matrix):
            parser.error("--fused-cards must be inside ~/.immich-memories-matrix")
    if args.target_duration_seconds <= 0:
        parser.error("--target-duration-seconds must be positive")
    if args.expected_seconds_per_final_visual <= 0:
        parser.error("--expected-seconds-per-final-visual must be positive")
    if args.thinking_budget_tokens < 1:
        parser.error("--thinking-budget-tokens must be positive")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    if not args.memory_type.strip():
        parser.error("--memory-type cannot be blank")
    return args


def _load_bank(path: Path) -> tuple[datetime, datetime, tuple[Description, ...], dict[str, Any]]:
    bank_path = path.expanduser().resolve()
    payload = json.loads(bank_path.read_text())
    scope = payload.get("scope")
    rows = payload.get("descriptions")
    if not isinstance(scope, dict) or not isinstance(rows, list):
        raise ValueError("description bank needs scope and descriptions")
    start = datetime.fromisoformat(str(scope["start"]))
    end = datetime.fromisoformat(str(scope["end"]))
    descriptions = tuple(
        Description(asset_id=str(row["asset_id"]), text=str(row["text"])) for row in rows
    )
    if len({description.asset_id for description in descriptions}) != len(descriptions):
        raise ValueError("description bank contains duplicate asset IDs")
    if any(not description.text.strip() for description in descriptions):
        raise ValueError("description bank contains blank descriptions")
    return start, end, descriptions, payload


def _prepare_source(start: datetime, end: datetime) -> tuple[Any, Any, int]:
    from immich_memories.analysis.selection_source import (
        EditorialDependencies,
        EditorialSelectionRequest,
        SourceScope,
        prepare_editorial_source,
    )
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.config import get_config
    from immich_memories.timeperiod import DateRange

    config = get_config()
    requested = DateRange(start, end)
    scope = SourceScope(
        start_at=start,
        end_at=end,
        excluded_filename_patterns=tuple(config.analysis.exclude_filename_patterns),
        stills_need_a_camera=config.analysis.exclude_stills_without_camera_exif,
        min_source_short_side=config.analysis.min_source_short_side,
        include_off_timeline=False,
    )
    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        assets = tuple(client.get_assets_for_date_range(requested))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=scope),
        EditorialDependencies(source_fetcher=lambda _scope: assets),
    )
    return config, prepared, len(assets)


def _moments(prepared: Any, descriptions: tuple[Description, ...]) -> tuple[Moment, ...]:
    by_id = {description.asset_id: description for description in descriptions}
    prepared_ids = set(prepared.candidate_ids)
    bank_ids = set(by_id)
    if prepared_ids != bank_ids:
        raise ValueError(
            "bank no longer matches the production source corpus: "
            f"{len(prepared_ids - bank_ids)} missing and {len(bank_ids - prepared_ids)} extra"
        )
    return tuple(
        Moment(
            alias=f"M{index:03d}",
            group=group,
            descriptions=tuple(by_id[candidate.asset_id] for candidate in group.candidates),
        )
        for index, group in enumerate(prepared.moment_groups, start=1)
    )


def _cards_from_fused(path: Path, moments: tuple[Moment, ...]) -> tuple[MomentCard, ...]:
    payload = json.loads(path.read_text())
    rows = payload.get("cards")
    if not isinstance(rows, list):
        raise ValueError("fused card bank needs a cards list")
    by_id = {str(row.get("moment_id")): row for row in rows if isinstance(row, dict)}
    if len(by_id) != len(rows):
        raise ValueError("fused card bank contains duplicate or malformed moment IDs")
    expected = {moment.alias for moment in moments}
    if set(by_id) != expected:
        raise ValueError(
            "fused card bank no longer matches the production moment wall: "
            f"{len(expected - set(by_id))} missing and {len(set(by_id) - expected)} extra"
        )
    cards: list[MomentCard] = []
    for moment in moments:
        row = by_id[moment.alias]
        asset_ids = tuple(str(asset_id) for asset_id in row.get("asset_ids") or ())
        if asset_ids != moment.group.candidate_ids:
            raise ValueError(f"fused card membership changed for {moment.alias}")
        summary = bounded_model_text(row.get("fused_summary"), max_chars=MAX_CARD_CHARS)
        if summary is None or row.get("error") is not None:
            raise ValueError(f"fused card unavailable for {moment.alias}")
        raw_people = row.get("people_metadata")
        if raw_people is None:
            raw_people = []
        if not isinstance(raw_people, list) or any(
            not isinstance(item, dict) for item in raw_people
        ):
            raise ValueError(f"fused people metadata malformed for {moment.alias}")
        cards.append(MomentCard(moment, summary, None, tuple(raw_people)))
    return tuple(cards)


def _summary_prompt(moment: Moment) -> str:
    observations = "\n".join(
        f"V{index:02d} [{_observation_context(candidate)}]: {description.text}"
        for index, (candidate, description) in enumerate(
            zip(moment.group.candidates, moment.descriptions, strict=True), start=1
        )
    )
    shape = json.dumps(
        {"schema_version": CARD_SCHEMA, "summary": "literal inventory of the moment"},
        separators=(",", ":"),
    )
    return f"""Build one compact factual card from every observation below.

Preserve every distinct visible subject, action, object, and setting. Ground recognized people and
places in the supplied metadata when relevant. A name says who Immich recognized, not their
relationship; do not invent one. Collapse only genuine
repetition. A rare object in one frame must survive even when the other frames repeat a common
scene. Do not rank, score, select, interpret significance, infer relationships, or invent context.
Do not mention the observation labels. Use one line without double quotes or backslashes.

OBSERVATIONS
{observations}

Return only one complete JSON object with exactly these keys:
{shape}
The schema_version value must be exactly {CARD_SCHEMA}; do not shorten or paraphrase it."""


def _observation_context(candidate: Any) -> str:
    grounded = candidate_who_and_where(candidate)
    return " | ".join((candidate.media_kind, *grounded))


def _moment_context(moment: Moment) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            annotation
            for candidate in moment.group.candidates
            for annotation in candidate_who_and_where(candidate)
        )
    )


async def _ask(
    prompt: str,
    *,
    llm_config: Any,
    cache_path: Path,
    max_tokens: int,
    timeout_seconds: int,
    thinking: bool,
) -> ModelAnswer:
    started = time.monotonic()
    raw = await query_llm(
        prompt,
        llm_config,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        thinking=thinking,
        cache_path=cache_path,
        require_complete=True,
    )
    return ModelAnswer(prompt, raw, time.monotonic() - started)


def _card_summary(raw: str) -> str:
    payload = final_json_object(raw)
    if payload is None or set(payload) != {"schema_version", "summary"}:
        raise ValueError("moment card answer is not the exact JSON envelope")
    if payload.get("schema_version") != CARD_SCHEMA:
        raise ValueError("moment card answer has the wrong schema version")
    summary = bounded_model_text(payload.get("summary"), max_chars=MAX_CARD_CHARS)
    if summary is None:
        raise ValueError("moment card summary is not safe bounded text")
    return summary


async def _build_cards(
    moments: tuple[Moment, ...],
    *,
    input_shape: str,
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
    concurrency: int,
) -> tuple[MomentCard, ...]:
    completed = 0
    semaphore = asyncio.Semaphore(concurrency)

    async def _build_one(moment: Moment) -> MomentCard:
        nonlocal completed
        if input_shape == "raw":
            summary = " ; ".join(
                f"{candidate.media_kind}{' [FAVOURITE]' if candidate.favourite else ''}: "
                f"{description.text}"
                for candidate, description in zip(
                    moment.group.candidates, moment.descriptions, strict=True
                )
            )
            answer = None
        else:
            async with semaphore:
                answer = await _ask(
                    _summary_prompt(moment),
                    llm_config=llm_config,
                    cache_path=cache_path,
                    max_tokens=900,
                    timeout_seconds=timeout_seconds,
                    thinking=False,
                )
            summary = _card_summary(answer.raw)
        card = MomentCard(moment, summary, answer)
        completed += 1
        if completed == 1 or completed % 10 == 0 or completed == len(moments):
            print(f"cards: {completed}/{len(moments)}", flush=True)
        return card

    return tuple(await asyncio.gather(*(_build_one(moment) for moment in moments)))


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
   turning point. For a personal chronological recap, an ordinary-looking object, note, or test may
   document a private life change whose consequence far exceeds its visual spectacle.

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
{shape}"""


def _bounded_list(value: object, *, max_items: int, max_chars: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError("model text list has the wrong shape")
    parsed = tuple(bounded_model_text(item, max_chars=max_chars) for item in value)
    if any(item is None for item in parsed):
        raise ValueError("model text list contains unsafe text")
    return tuple(item for item in parsed if item is not None)


def _read_thesis(raw: str, valid_ids: frozenset[str]) -> dict[str, Any]:
    payload = final_json_object(raw)
    expected = {
        "schema_version",
        "thesis",
        "sustained_threads",
        "turning_points",
        "ordinary_texture",
    }
    if (
        payload is None
        or set(payload) != expected
        or payload.get("schema_version") != THESIS_SCHEMA
    ):
        raise ValueError("memory thesis answer is not the exact JSON envelope")
    thesis = bounded_model_text(payload.get("thesis"), max_chars=MAX_THESIS_CHARS)
    sustained = _read_grounded_threads(
        payload.get("sustained_threads"), valid_ids=valid_ids, max_items=5
    )
    turning = _read_grounded_threads(
        payload.get("turning_points"), valid_ids=valid_ids, max_items=5
    )
    texture = _bounded_list(
        payload.get("ordinary_texture"), max_items=8, max_chars=MAX_THREAD_CHARS
    )
    if thesis is None or not sustained:
        raise ValueError("memory thesis needs a thesis and at least one sustained thread")
    return {
        "thesis": thesis,
        "sustained_threads": sustained,
        "turning_points": turning,
        "ordinary_texture": list(texture),
    }


def _read_grounded_threads(
    value: object, *, valid_ids: frozenset[str], max_items: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError("grounded thesis list has the wrong shape")
    rows: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"summary", "evidence_moment_ids"}:
            raise ValueError("grounded thesis row has the wrong shape")
        summary = bounded_model_text(row.get("summary"), max_chars=MAX_THREAD_CHARS)
        evidence = row.get("evidence_moment_ids")
        if summary is None or not isinstance(evidence, list) or not evidence:
            raise ValueError("grounded thesis row needs prose and evidence")
        evidence_ids = tuple(str(item) for item in evidence)
        if len(set(evidence_ids)) != len(evidence_ids) or not set(evidence_ids) <= valid_ids:
            raise ValueError("grounded thesis evidence must be unique known moment IDs")
        rows.append({"summary": summary, "evidence_moment_ids": list(evidence_ids)})
    return rows


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
            "keep": [{"moment_id": "M001", "reason": "why this moment belongs"}],
            "audit_summary": "concise explicit account of the allocation and its main tradeoff",
            "comparisons": [
                {
                    "kept_moment_id": "M001",
                    "rejected_moment_id": "M002",
                    "reason": "why the retained moment has more on-screen value under scarcity",
                }
            ],
            "overall_reason": "how the cut expresses this memory",
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
Prefer a lived scene showing action, relationship, expression, place, or atmosphere over packaging,
equipment, metrics, a route, a screen, or setup evidence that merely documents the same thread. An
object or record earns a slot only when the card establishes a consequential fact that no lived scene
can carry. Do not treat all objects as junk; apply the distinction to what each moment contributes.
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
In comparisons, give between one and eight decisive head-to-head choices from the non-favourite pool:
one moment you kept, the strongest plausible alternative it displaced, and the visible reason the kept
moment wins. This is a concise evidence-backed rationale, not hidden chain-of-thought. Comparison IDs
must be different; kept_moment_id must appear in keep and rejected_moment_id must not.

MOMENT WALL
{wall}

Return only one complete JSON object with exactly these keys:
{shape}"""


def _read_selection(
    raw: str,
    valid_ids: frozenset[str],
    capacity: int,
    *,
    excluded_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    payload = final_json_object(raw)
    expected = {"schema_version", "keep", "audit_summary", "comparisons", "overall_reason"}
    if (
        payload is None
        or set(payload) != expected
        or payload.get("schema_version") != SELECTION_SCHEMA
    ):
        raise ValueError("moment selection answer is not the exact JSON envelope")
    rows = payload.get("keep")
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
    keep_ids = tuple(row["moment_id"] for row in keep)
    if len(set(keep_ids)) != len(keep_ids):
        raise ValueError("moment selection contains duplicate IDs")
    expected_order = {card_id: index for index, card_id in enumerate(sorted(valid_ids))}
    if tuple(sorted(keep_ids, key=expected_order.__getitem__)) != keep_ids:
        raise ValueError("moment selection is not chronological")
    audit_summary = bounded_model_text(payload.get("audit_summary"), max_chars=MAX_THESIS_CHARS)
    raw_comparisons = payload.get("comparisons")
    if (
        audit_summary is None
        or not isinstance(raw_comparisons, list)
        or not 1 <= len(raw_comparisons) <= 8
    ):
        raise ValueError("moment selection audit has the wrong shape")
    comparisons: list[dict[str, str]] = []
    for row in raw_comparisons:
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
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=MAX_THESIS_CHARS)
    if overall is None:
        raise ValueError("moment selection overall reason is unsafe")
    return {
        "keep": keep,
        "audit_summary": audit_summary,
        "comparisons": comparisons,
        "overall_reason": overall,
    }


def _admit_favourites(
    selection: dict[str, Any],
    *,
    required_ids: tuple[str, ...],
    cards: tuple[MomentCard, ...],
) -> dict[str, Any]:
    selected_by_id = {row["moment_id"]: row for row in selection["keep"]}
    selected_by_id.update(
        {
            moment_id: {
                "moment_id": moment_id,
                "reason": "Contains direct owner evidence through one or more favourites.",
            }
            for moment_id in required_ids
        }
    )
    return {
        "keep": [
            selected_by_id[card.moment.alias]
            for card in cards
            if card.moment.alias in selected_by_id
        ],
        "audit_summary": selection["audit_summary"],
        "comparisons": selection["comparisons"],
        "overall_reason": selection["overall_reason"],
    }


def _capacity(config: Any, target: float, seconds_per_visual: float) -> dict[str, float | int]:
    title_config = config.title_screens
    opening = float(title_config.title_duration) if title_config.enabled else 0.0
    ending = float(title_config.ending_duration) if title_config.enabled else 0.0
    overhead = min(target, opening + ending)
    content = max(0.0, target - overhead)
    return {
        "target_duration_seconds": target,
        "opening_seconds": opening,
        "ending_seconds": ending,
        "overhead_seconds": overhead,
        "content_budget_seconds": content,
        "expected_seconds_per_final_visual": seconds_per_visual,
        "moment_capacity": math.floor(content / seconds_per_visual),
    }


def _answer_record(answer: ModelAnswer | None) -> dict[str, Any] | None:
    if answer is None:
        return None
    return {"prompt": answer.prompt, "raw": answer.raw, "wall_seconds": answer.wall_seconds}


def _card_record(card: MomentCard) -> dict[str, Any]:
    return {
        "moment_id": card.moment.alias,
        "production_group_id": card.moment.group.group_id,
        "asset_ids": list(card.moment.group.candidate_ids),
        "taken_at": card.moment.group.candidates[0].taken_at.isoformat(),
        "visual_count": len(card.moment.group.candidates),
        "favourite_count": sum(candidate.favourite for candidate in card.moment.group.candidates),
        "grounded_context": list(_moment_context(card.moment)),
        "people_metadata": list(card.people_metadata),
        "summary": card.summary,
        "summary_call": _answer_record(card.answer),
        "card_line": _card_line(card),
    }


async def _run(
    args: argparse.Namespace,
    *,
    start: datetime,
    end: datetime,
    descriptions: tuple[Description, ...],
    bank_payload: dict[str, Any],
    config: Any,
    prepared: Any,
    fetched: int,
) -> dict[str, Any]:
    moments = _moments(prepared, descriptions)
    args.out.mkdir(parents=True, exist_ok=True)
    cache_path = args.cache.expanduser().resolve() if args.cache else args.out / "judgments.db"
    thinking_params = dict(config.llm.thinking_params)
    if args.thinking:
        template_kwargs = dict(thinking_params.get("chat_template_kwargs") or {})
        template_kwargs["enable_thinking"] = True
        thinking_params["chat_template_kwargs"] = template_kwargs
        thinking_params["thinking_budget"] = args.thinking_budget_tokens
    llm_config = config.llm.model_copy(
        update={
            "model": args.model,
            "thinking": args.thinking,
            "thinking_params": thinking_params,
        }
    )
    cards = (
        _cards_from_fused(args.fused_cards, moments)
        if args.fused_cards is not None
        else await _build_cards(
            moments,
            input_shape=args.input_shape,
            llm_config=llm_config,
            cache_path=cache_path,
            timeout_seconds=args.timeout_seconds,
            concurrency=args.concurrency,
        )
    )
    capacity = _capacity(
        config, args.target_duration_seconds, args.expected_seconds_per_final_visual
    )
    moment_capacity = int(capacity["moment_capacity"])
    if moment_capacity < 1:
        raise ValueError("duration budget leaves no moment capacity")

    aliases = frozenset(moment.alias for moment in moments)
    thesis_answer = await _ask(
        _thesis_prompt(cards, args.memory_type),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=1800,
        timeout_seconds=args.timeout_seconds,
        thinking=args.thinking,
    )
    thesis = _read_thesis(thesis_answer.raw, aliases)
    print("thesis: complete", flush=True)
    required_ids = tuple(
        card.moment.alias
        for card in cards
        if any(candidate.favourite for candidate in card.moment.group.candidates)
    )
    if len(required_ids) > moment_capacity:
        raise ValueError("favourite-bearing moments exceed duration-derived capacity")
    additional_capacity = moment_capacity - len(required_ids)
    selection_answer: ModelAnswer | None = None
    if additional_capacity:
        selection_answer = await _ask(
            _selection_prompt(
                cards,
                memory_type=args.memory_type,
                thesis=thesis,
                capacity=moment_capacity,
                required_ids=required_ids,
            ),
            llm_config=llm_config,
            cache_path=cache_path,
            max_tokens=3000,
            timeout_seconds=args.timeout_seconds,
            thinking=args.thinking,
        )
        additional = _read_selection(
            selection_answer.raw,
            aliases,
            additional_capacity,
            excluded_ids=frozenset(required_ids),
        )
    else:
        additional = {
            "keep": [],
            "audit_summary": "No discretionary slots remained after deterministic favourites.",
            "comparisons": [],
            "overall_reason": "Favourite-bearing moments consume the available capacity.",
        }
    selection = _admit_favourites(additional, required_ids=required_ids, cards=cards)
    kept = {row["moment_id"] for row in selection["keep"]}
    favourite_moments = set(required_ids)
    print("selection: complete", flush=True)

    return {
        "schema_version": "description-two-pass-prototype-v1",
        "prototype": True,
        "privacy": "private matrix artifact; do not commit descriptions, IDs, prompts, or answers",
        "configuration": {
            "memory_type": args.memory_type.strip(),
            "model": args.model,
            "temperature": DEFAULT_TEMPERATURE,
            "thinking": args.thinking,
            "thinking_budget_tokens": (args.thinking_budget_tokens if args.thinking else None),
            "card_concurrency": args.concurrency,
            "input_shape": (
                "fused-400px-moment-cards" if args.fused_cards is not None else args.input_shape
            ),
            "thesis_prompt_version": THESIS_PROMPT_VERSION,
            "selection_prompt_version": SELECTION_PROMPT_VERSION,
            "cache_path": str(cache_path),
            "capacity": capacity,
            "deterministic_favourite_moments": len(required_ids),
        },
        "scope": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {
            "fetched": fetched,
            "source_eligible": len(prepared.candidates),
            "described": len(descriptions),
            "moments": len(moments),
            "selected_moments": len(kept),
            "favourite_moments": len(favourite_moments),
            "selected_favourite_moments": len(kept & favourite_moments),
            "bank_warnings": len(bank_payload.get("warnings", [])),
            "source_warnings": len(prepared.source_warnings),
        },
        "cards": [_card_record(card) for card in cards],
        "thesis": {
            **thesis,
            "call": _answer_record(thesis_answer),
        },
        "selection": {
            **selection,
            "call": _answer_record(selection_answer),
        },
    }


def main() -> int:
    args = _arguments()
    start, end, descriptions, bank_payload = _load_bank(args.bank)
    # SyncImmichClient owns a private event loop. Finish source acquisition
    # before asyncio starts the independent text-only model phase.
    config, prepared, fetched = _prepare_source(start, end)
    result = asyncio.run(
        _run(
            args,
            start=start,
            end=end,
            descriptions=descriptions,
            bank_payload=bank_payload,
            config=config,
            prepared=prepared,
            fetched=fetched,
        )
    )
    result_path = args.out / "result.json"
    temporary = args.out / "result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(result_path)
    print(
        json.dumps(
            {
                "result": str(result_path),
                "temperature": result["configuration"]["temperature"],
                "input_shape": result["configuration"]["input_shape"],
                "moments": result["counts"]["moments"],
                "selected_moments": result["counts"]["selected_moments"],
                "favourites": (
                    f"{result['counts']['selected_favourite_moments']}/"
                    f"{result['counts']['favourite_moments']}"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
