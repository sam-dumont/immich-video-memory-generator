#!/usr/bin/env python3
"""Run the thesis-guided editor on a private real-library case matrix.

The script composes production acquisition/Cull/Structure with the measured
400px description -> moment card -> thesis -> moment cut prototype. Selects is
demand-driven in the refined design, so the obsolete corpus-wide position is
available only as an explicit baseline. Private media, IDs, descriptions,
prompts, answers, and the case manifest can only be read or written below
``~/.immich-memories-matrix``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from datetime import time as datetime_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import probe_description_moment_cut as prototype
from probe_description_allocation import DescriptionWorkprint, build_description_workprint
from probe_people_context import PersonFact, load_person_facts

from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.live_photo_pipeline import drop_live_photo_components
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.period_insight import run_period_insight
from immich_memories.analysis.selection_cull import run_cull
from immich_memories.analysis.selection_descriptions import describe_editorial_assets
from immich_memories.analysis.selection_flow import run_editorial_selection
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialGroup,
    EditorialSelectionRequest,
    SourceScope,
    build_episode_groups,
    build_moment_groups,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_structure import build_structure_workprint
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object
from immich_memories.analysis.thumbnail_prefetch import (
    THUMBNAIL_SIZE,
    ThumbnailPrefetcher,
    cached_preview_bytes,
)
from immich_memories.api.person_scope import videos_in_window
from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.cache.judgment_cache import (
    JudgmentCache,
    judgment_key,
    verdicts_beside,
)
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.cli._asset_fetch import fetch_photos
from immich_memories.cli._trip_generation import _filter_photos_near_trip
from immich_memories.config import get_config
from immich_memories.filename_builder import get_divider_mode
from immich_memories.processing.timeline_budget import plan_timeline
from immich_memories.timeperiod import DateRange

DEFAULT_OUT = Path.home() / ".immich-memories-matrix" / "smart-edit-case-matrix"
DEFAULT_CASES_FILE = Path.home() / ".immich-memories-matrix" / "smart-edit-cases.json"
DEFAULT_VISION_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
DEFAULT_TEXT_MODEL = DEFAULT_VISION_MODEL
FLAT_WALL_MAX_CARDS = 160
CHAPTER_MAX_CARDS = 120
ALLOCATION_SCHEMA = "description-chapter-allocation-v1"
DISPLAY_DOCTRINE = """Separate a life from evidence that the life happened. For a sustained
thread, prefer a lived scene showing action, relationship, expression, place, or atmosphere over
gear, metrics, a route, a certificate, or a screen that merely documents the same thread. An
object, document, or screen earns a slot only when the card itself establishes an irreplaceable
consequential fact that no lived scene can carry. Equipment ownership and routine statistics do
not qualify merely because they are legible. Ordinary
texture means lived atmosphere or relationship, not arbitrary household inventory. When setup
evidence and the event it sets up are both present, do not spend two slots unless each adds a
different necessary beat."""


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    product: str
    ranges: tuple[DateRange, ...]
    target_seconds: float
    brief: str
    people: tuple[str, ...] = ()
    trip: bool = False


@dataclass(frozen=True)
class TextCall:
    prompt: str
    raw: str
    wall_seconds: float
    cache_hit: bool


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    label: str
    cards: tuple[prototype.MomentCard, ...]


def _progress_duration(seconds: float) -> str:
    rounded = max(0, round(seconds))
    minutes, remainder = divmod(rounded, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{remainder:02d}s" if minutes else f"{remainder}s"


class _ProgressGateway:
    """Compact live progress around the unchanged production visual gateway."""

    def __init__(
        self,
        gateway: VisualEditorialGateway,
        *,
        expected_tiles: dict[str, int] | None = None,
    ) -> None:
        self._gateway = gateway
        self._expected_tiles = expected_tiles or {}
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "started": 0,
                "completed": 0,
                "active": 0,
                "cache_hits": 0,
                "actual_calls": 0,
                "submitted_tiles": 0,
                "completed_tiles": 0,
                "failures": 0,
                "started_at": 0.0,
            }
        )
        self._stopped = threading.Event()
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

    def ask(self, request: Any) -> Any:
        name = request.pass_name
        tiles = sum(len(page.tile_refs) for page in request.pages)
        with self._lock:
            state = self._states[name]
            if state["started"] == 0:
                state["started_at"] = time.monotonic()
            state["started"] += 1
            state["active"] += 1
            state["submitted_tiles"] += tiles
            first = state["started"] == 1
        if first:
            print(f"{name}: started ({tiles} tiles in first request)", flush=True)
        try:
            answer = self._gateway.ask(request)
        except Exception:
            with self._lock:
                state = self._states[name]
                state["active"] -= 1
                state["failures"] += 1
            self._print_state(name, suffix="request failed")
            raise
        with self._lock:
            state = self._states[name]
            state["active"] -= 1
            state["completed"] += 1
            state["cache_hits"] += int(answer.request_trace.cache_hit)
            state["actual_calls"] += answer.request_trace.actual_calls
            state["completed_tiles"] += tiles
            completed = state["completed"]
        every = 100 if name == "asset-description" else 10
        if completed == 1 or completed % every == 0:
            self._print_state(name)
        return answer

    def close(self) -> None:
        self._stopped.set()
        self._heartbeat.join(timeout=1.0)
        with self._lock:
            names = tuple(self._states)
        for name in names:
            self._print_state(name, suffix="done")

    def _heartbeat_loop(self) -> None:
        while not self._stopped.wait(30.0):
            with self._lock:
                active = tuple(name for name, state in self._states.items() if state["active"])
            for name in active:
                self._print_state(name, suffix="working")

    def _print_state(self, name: str, *, suffix: str | None = None) -> None:
        with self._lock:
            state = dict(self._states[name])
        elapsed = max(0.0, time.monotonic() - float(state["started_at"]))
        expected = self._expected_tiles.get(name)
        completed_tiles = int(state["completed_tiles"])
        progress = ""
        if expected:
            percent = min(100.0, completed_tiles / expected * 100)
            eta = (
                elapsed * (expected - completed_tiles) / completed_tiles
                if completed_tiles
                else None
            )
            eta_text = _progress_duration(eta) if eta is not None else "calculating"
            progress = (
                f", {completed_tiles}/{expected} tiles complete ({percent:.1f}%), "
                f"elapsed {_progress_duration(elapsed)}, ETA {eta_text}"
            )
        else:
            progress = (
                f", {state['completed_tiles']}/{state['submitted_tiles']} tiles complete, "
                f"elapsed {_progress_duration(elapsed)}"
            )
        tail = f" | {suffix}" if suffix else ""
        print(
            f"{name}: {state['completed']} completed, {state['active']} active, "
            f"{state['cache_hits']} cache hits, {state['actual_calls']} actual calls, "
            f"{state['failures']} failures{progress}{tail}",
            flush=True,
        )


def _case_boundary(value: object, *, end: bool) -> datetime:
    text = str(value)
    if len(text) == 10:
        return datetime.combine(
            date.fromisoformat(text),
            datetime_time.max if end else datetime_time.min,
        )
    return datetime.fromisoformat(text)


def _load_cases(path: Path) -> tuple[Case, ...]:
    payload = json.loads(path.read_text())
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("case manifest must contain a non-empty cases list")

    cases: list[Case] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("each case must be a JSON object")
        range_rows = row.get("ranges")
        if not isinstance(range_rows, list) or not range_rows:
            raise ValueError(f"case {row.get('key', '<unknown>')} has no date ranges")
        ranges = tuple(
            DateRange(
                _case_boundary(window["start"], end=False),
                _case_boundary(window["end"], end=True),
            )
            for window in range_rows
        )
        cases.append(
            Case(
                key=str(row["key"]),
                label=str(row["label"]),
                product=str(row["product"]),
                ranges=ranges,
                target_seconds=float(row["target_seconds"]),
                brief=str(row["brief"]),
                people=tuple(str(name) for name in row.get("people", ())),
                trip=bool(row.get("trip", False)),
            )
        )
    return tuple(cases)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    temporary.replace(path)


def _dedupe(assets: list[Any]) -> list[Any]:
    by_id: dict[str, Any] = {}
    for asset in assets:
        by_id.setdefault(asset.id, asset)
    return sorted(by_id.values(), key=lambda asset: (asset.file_created_at, asset.id))


def _fetch_assets(
    client: SyncImmichClient, config: Any, case: Case, people: dict[str, Any]
) -> list[Any]:
    person_ids = [people[name].id for name in case.people]
    photos = fetch_photos(
        client=client,
        date_ranges=list(case.ranges),
        person_ids=person_ids,
        merge_window_seconds=config.analysis.live_photo_merge_window_seconds,
    )
    videos = [
        asset for window in case.ranges for asset in videos_in_window(client, person_ids, window)
    ]
    if case.trip:
        photos = _filter_photos_near_trip(photos, SimpleNamespace(), config)
    return _dedupe([*drop_live_photo_components(_dedupe(videos), photos), *photos])


def _person_facts() -> dict[str, PersonFact]:
    return load_person_facts(include_derived=True)


def _age_label(born: str | None, when: datetime) -> str | None:
    if not born:
        return None
    try:
        birthday = date.fromisoformat(born)
    except ValueError:
        return None
    days = (when.date() - birthday).days
    if days < 0:
        return "capture predates recorded birth date"
    if days <= 1:
        return "newborn"
    if days < 60:
        return f"{days} days old"
    if days < 730:
        return f"{days // 30} months old"
    years = when.year - birthday.year - ((when.month, when.day) < (birthday.month, birthday.day))
    return f"aged {years}"


def _moment_people(moment: prototype.Moment, facts: dict[str, PersonFact]) -> tuple[str, ...]:
    seen: dict[str, datetime] = {}
    lines: list[str] = []
    for candidate in moment.group.candidates:
        for person in candidate.source.people or ():
            name = str(person.name or "").strip()
            if name:
                seen.setdefault(name, candidate.taken_at)

    present = set(seen)
    for name, taken_at in seen.items():
        fact = facts.get(name)
        if fact is None:
            lines.append(
                f"{name} [relationship=unconfirmed; birth_date=unknown; "
                "first_library_month=unknown]"
            )
            continue
        fields = [
            f"relationship={fact.relationship}",
            f"relationship_source={fact.relationship_source}",
            f"birth_date={fact.birth_date or 'unknown'}",
            f"first_library_month={fact.first_month or 'unknown'}",
            f"sustained_onset={fact.onset or 'unknown'}",
            f"library_tier={fact.tier or 'unknown'}",
        ]
        linked_here = [
            f"{link.kind} {link.target_name} ({link.source})"
            for link in fact.links
            if link.target_name in present
        ]
        if linked_here:
            fields.append(f"relationships_here={'; '.join(linked_here)}")
        if age := _age_label(fact.birth_date, taken_at):
            fields.append(f"age_at_capture={age}")
        lines.append(f"{name} [{'; '.join(fields)}]")
    return tuple(lines)


def _description_subset(
    prepared: Any,
    survivors: tuple[Any, ...],
    *,
    admitted_moments: tuple[Any, ...] | None = None,
) -> Any:
    survivor_ids = {candidate.asset_id for candidate in survivors}
    visual_sources = tuple(
        source for source in prepared.visual_sources if source.asset.id in survivor_ids
    )
    return replace(
        prepared,
        candidates=survivors,
        visual_sources=visual_sources,
        episode_groups=build_episode_groups(survivors),
        moment_groups=(
            tuple(
                EditorialGroup(moment.moment_id, moment.candidates) for moment in admitted_moments
            )
            if admitted_moments is not None
            else build_moment_groups(survivors)
        ),
    )


def _description_chapter_key(case: Case, moment: Any) -> tuple[int, ...]:
    taken = moment.candidates[0].taken_at
    if case.product == "monthly_highlights":
        iso = taken.isocalendar()
        return iso.year, iso.week
    if case.people:
        return taken.year, taken.month
    span_days = (case.ranges[-1].end - case.ranges[0].start).days
    return (taken.year,) if span_days > 550 else (taken.year, taken.month)


def _relationship_context_names(
    case: Case,
    facts: dict[str, PersonFact],
) -> tuple[str, ...]:
    subjects = set(case.people)
    return tuple(
        sorted(
            {
                link.target_name
                for subject in case.people
                if subject in facts
                for link in facts[subject].links
                if link.source in {"confirmed", "derived"} and link.target_name not in subjects
            }
        )
    )


def _description_allocation_record(
    workprint: DescriptionWorkprint | None,
) -> dict[str, Any] | None:
    if workprint is None:
        return None
    reasons = Counter(
        reason.split(":", 1)[0]
        for admission in workprint.admissions
        for reason in admission.reasons
    )
    return {
        "input_moments": workprint.input_moments,
        "admitted_moments": len(workprint.moments),
        "admitted_assets": len(workprint.candidates),
        "reason_counts": dict(sorted(reasons.items())),
    }


def _request_metrics(trace: Any) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for request in trace.requests:
        grouped[request.provenance.pass_name].append(request)
    return {
        name: {
            "requests": len(requests),
            "cache_hits": sum(request.cache_hit for request in requests),
            "actual_calls": sum(request.actual_calls for request in requests),
            "tiles": sum(request.tile_count for request in requests),
        }
        for name, requests in grouped.items()
    }


def _run_upstream(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
    *,
    config: Any,
    out: Path,
    corpus_selects: bool,
) -> Any:
    gateways: list[_ProgressGateway] = []
    expected_tile_counts: dict[str, int] = {}

    def gateway_factory(trace: Any) -> _ProgressGateway:
        gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=config.llm,
                cache_path=verdicts_beside(config.cache.cache_path),
                trace=trace,
            ),
            expected_tiles=expected_tile_counts,
        )
        gateways.append(gateway)
        return gateway

    try:
        if corpus_selects:
            return run_editorial_selection(
                request,
                dependencies,
                gateway_factory=gateway_factory,
                sheet_output_dir=out / "upstream-sheets",
                frame_cache_dir=config.cache.cache_path / "editorial-frames",
                review_output_dir=out / "cull-review",
            )

        prepared = prepare_editorial_source(request, dependencies)
        expected_tile_counts["episode-scan"] = len(prepared.candidates)
        gateway = gateway_factory(prepared.trace)
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=out / "upstream-sheets",
            frame_cache_dir=config.cache.cache_path / "editorial-frames",
        )
        pass_one = run_cull(prepared, pass_zero, review_output_dir=out / "cull-review")
        structure_workprint = build_structure_workprint(
            prepared,
            pass_one.survivors,
            atlas=pass_zero.atlas,
            output_dir=out / "upstream-sheets" / "structure",
        )
        return SimpleNamespace(
            prepared=prepared,
            pass_zero=pass_zero,
            pass_one=pass_one,
            structure_workprint=structure_workprint,
            pass_two=None,
        )
    finally:
        for gateway in gateways:
            gateway.close()


def _capacity(config: Any, case: Case, survivors: tuple[Any, ...]) -> dict[str, Any]:
    start = min(window.start for window in case.ranges).date()
    end = max(window.end for window in case.ranges).date()
    mode = get_divider_mode(case.product, start, end)
    titles = SimpleNamespace(
        enabled=config.title_screens.enabled,
        title_duration=config.title_screens.title_duration,
        ending_duration=config.title_screens.ending_duration,
        month_divider_duration=config.title_screens.month_divider_duration,
        month_divider_threshold=config.title_screens.month_divider_threshold,
        show_month_dividers=mode == "month",
        show_location_cards=True,
        divider_mode=mode,
    )
    plan = plan_timeline(
        [candidate.source for candidate in survivors],
        titles,
        case.target_seconds,
        case.product,
        expected_clip_duration=4.0,
        transition_mode="none",
    )
    return {
        "target_seconds": case.target_seconds,
        "title_seconds": plan.title_duration,
        "ending_seconds": plan.ending_duration,
        "divider_mode": mode,
        "eligible_dividers": plan.eligible_dividers,
        "content_seconds": plan.content_budget,
        "expected_seconds_per_visual": 4.0,
        "moment_capacity": math.floor(plan.content_budget / 4.0),
        "trip_location_cards_resolve_after_selection": case.trip,
    }


async def _ask_text(
    prompt: str,
    *,
    llm_config: Any,
    cache_path: Path,
    max_tokens: int,
    timeout_seconds: int,
) -> TextCall:
    key = judgment_key(model=llm_config.model, prompt=prompt, thinking=False)
    cache_hit = JudgmentCache(cache_path).answer_for(key) is not None
    started = time.monotonic()
    raw = await query_llm(
        prompt,
        llm_config,
        temperature=0.0,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        thinking=False,
        cache_path=cache_path,
        require_complete=True,
    )
    return TextCall(prompt, raw, time.monotonic() - started, cache_hit)


def _enriched_line(card: prototype.MomentCard, facts: dict[str, PersonFact]) -> str:
    people = _moment_people(card.moment, facts)
    suffix = f" | people_metadata {' ; '.join(people)}" if people else ""
    return prototype._card_line(card) + suffix


def _enrich_wall_prompt(
    prompt: str,
    cards: tuple[prototype.MomentCard, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
) -> str:
    old_wall = "\n".join(prototype._card_line(card) for card in cards)
    new_wall = "\n".join(_enriched_line(card, facts) for card in cards)
    prompt = prompt.replace(f"MOMENT WALL\n{old_wall}", f"MOMENT WALL\n{new_wall}")
    return prompt.replace(
        "\nMOMENT WALL\n",
        f"\nEDITORIAL BRIEF\n{case.brief}\n\nDISPLAY DOCTRINE\n{DISPLAY_DOCTRINE}\n\nMOMENT WALL\n",
    )


async def _build_cards(
    moments: tuple[prototype.Moment, ...],
    *,
    facts: dict[str, PersonFact],
    llm_config: Any,
    cache_path: Path,
    concurrency: int,
    timeout_seconds: int,
) -> tuple[tuple[prototype.MomentCard, ...], tuple[TextCall, ...]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def build(moment: prototype.Moment) -> tuple[prototype.MomentCard, TextCall]:
        metadata = _moment_people(moment, facts)
        prompt = prototype._summary_prompt(moment)
        if metadata:
            block = "\n".join(f"- {line}" for line in metadata)
            prompt = prompt.replace(
                "\nOBSERVATIONS\n",
                "\nGROUND-TRUTH PEOPLE METADATA\n" + block + "\n\nOBSERVATIONS\n",
            )
        async with semaphore:
            call = await _ask_text(
                prompt,
                llm_config=llm_config,
                cache_path=cache_path,
                max_tokens=900,
                timeout_seconds=timeout_seconds,
            )
        return prototype.MomentCard(moment, prototype._card_summary(call.raw), None), call

    pairs = await asyncio.gather(*(build(moment) for moment in moments))
    return tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs)


def _chapter_key(case: Case, card: prototype.MomentCard) -> tuple[Any, ...]:
    taken = card.moment.group.candidates[0].taken_at
    if case.product == "monthly_highlights":
        iso = taken.isocalendar()
        return (iso.year, iso.week)
    scope_days = (case.ranges[-1].end - case.ranges[0].start).days
    if case.product == "person_spotlight" and scope_days > 730:
        return (taken.year,)
    return (taken.year, taken.month)


def _chapter_label(case: Case, key: tuple[Any, ...]) -> str:
    if case.product == "monthly_highlights":
        return f"ISO week {key[0]}-{int(key[1]):02d}"
    if len(key) == 1:
        return str(key[0])
    return f"{key[0]}-{int(key[1]):02d}"


def _chapters(case: Case, cards: tuple[prototype.MomentCard, ...]) -> tuple[Chapter, ...]:
    grouped: dict[tuple[Any, ...], list[prototype.MomentCard]] = defaultdict(list)
    for card in cards:
        grouped[_chapter_key(case, card)].append(card)
    chapters: list[Chapter] = []
    for key, members in grouped.items():
        parts = [
            members[index : index + CHAPTER_MAX_CARDS]
            for index in range(0, len(members), CHAPTER_MAX_CARDS)
        ]
        for part_number, part in enumerate(parts, start=1):
            suffix = f" part {part_number}/{len(parts)}" if len(parts) > 1 else ""
            chapters.append(
                Chapter(
                    chapter_id=f"C{len(chapters) + 1:03d}",
                    label=_chapter_label(case, key) + suffix,
                    cards=tuple(part),
                )
            )
    return tuple(chapters)


async def _read_thesis(
    cards: tuple[prototype.MomentCard, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any], TextCall]:
    prompt = _enrich_wall_prompt(
        prototype._thesis_prompt(cards, case.product), cards, case=case, facts=facts
    )
    call = await _ask_text(
        prompt,
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=1800,
        timeout_seconds=timeout_seconds,
    )
    aliases = frozenset(card.moment.alias for card in cards)
    return prototype._read_thesis(call.raw, aliases), call


def _global_thesis_prompt(
    case: Case,
    chapter_readings: tuple[tuple[Chapter, dict[str, Any]], ...],
) -> str:
    material = [
        {"chapter_id": chapter.chapter_id, "label": chapter.label, **reading}
        for chapter, reading in chapter_readings
    ]
    shape = {
        "schema_version": prototype.THESIS_SCHEMA,
        "thesis": "plain statement of what the complete candidate set is about",
        "sustained_threads": [{"summary": "thread", "evidence_moment_ids": ["M001"]}],
        "turning_points": [{"summary": "turning point", "evidence_moment_ids": ["M010"]}],
        "ordinary_texture": ["grounded contrast or texture"],
    }
    return f"""You are preparing {case.label}, a {case.product}.

EDITORIAL BRIEF
{case.brief}

Below are grounded readings of every chronological chapter. No chapter was sampled or omitted.
Read them as one complete memory. Find sustained threads across separated chapters, inspect every
chapter for one-off turning points whose consequence exceeds visual spectacle, and retain ordinary
texture that prevents the thesis from flattening the period. Density is not importance. Use only
the supplied facts and cite original moment IDs as evidence.

CHAPTER READINGS
{json.dumps(material, ensure_ascii=False, separators=(",", ":"))}

Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}"""


async def _hierarchical_thesis(
    chapters: tuple[Chapter, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    llm_config: Any,
    cache_path: Path,
    concurrency: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any], tuple[tuple[Chapter, dict[str, Any]], ...], tuple[TextCall, ...]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def read(chapter: Chapter) -> tuple[Chapter, dict[str, Any], TextCall]:
        async with semaphore:
            thesis, call = await _read_thesis(
                chapter.cards,
                case=case,
                facts=facts,
                llm_config=llm_config,
                cache_path=cache_path,
                timeout_seconds=timeout_seconds,
            )
        return chapter, thesis, call

    rows = await asyncio.gather(*(read(chapter) for chapter in chapters))
    readings = tuple((chapter, thesis) for chapter, thesis, _call in rows)
    call = await _ask_text(
        _global_thesis_prompt(case, readings),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=2200,
        timeout_seconds=timeout_seconds,
    )
    aliases = frozenset(card.moment.alias for chapter in chapters for card in chapter.cards)
    thesis = prototype._read_thesis(call.raw, aliases)
    return thesis, readings, (*tuple(row[2] for row in rows), call)


def _selection_prompt(
    cards: tuple[prototype.MomentCard, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    capacity: int,
) -> str:
    prompt = prototype._selection_prompt(
        cards,
        memory_type=case.product,
        thesis=thesis,
        capacity=capacity,
        required_ids=(),
    )
    law = (
        "Favourite counts are direct owner evidence, not automatic admission of every whole "
        "moment. Runtime may omit a favourite-bearing moment. If such a moment is retained, its "
        "later representative must be a favourite rather than an unstarred neighbour.\n\n"
    )
    prompt = prompt.replace(
        "\nUse the thesis as editorial orientation",
        "\n" + law + "Use the thesis as editorial orientation",
    )
    prompt = prompt.replace(
        "\nMOMENT WALL\n",
        "\nBefore answering, audit the tentative cut: for every choice that is only gear, "
        "statistics, a screen, a document, setup evidence, or household inventory, identify the "
        "lived moment it beats. If it beats none, drop it; if a lived alternative carries the same "
        "thread, substitute that alternative.\n\nMOMENT WALL\n",
    )
    return _enrich_wall_prompt(prompt, cards, case=case, facts=facts)


def _local_thesis(
    thesis: dict[str, Any], cards: tuple[prototype.MomentCard, ...]
) -> dict[str, Any]:
    """Keep global meaning while making only this wall's IDs actionable."""
    local_ids = {card.moment.alias for card in cards}

    def localize(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        return [
            {
                **row,
                "evidence_moment_ids": [
                    moment_id
                    for moment_id in row.get("evidence_moment_ids", [])
                    if moment_id in local_ids
                ],
            }
            for row in rows
            if isinstance(row, dict)
        ]

    return {
        **thesis,
        "sustained_threads": localize(thesis.get("sustained_threads")),
        "turning_points": localize(thesis.get("turning_points")),
    }


async def _select_cards(
    cards: tuple[prototype.MomentCard, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    capacity: int,
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any], TextCall | None]:
    if len(cards) <= capacity:
        return {
            "keep": [
                {
                    "moment_id": card.moment.alias,
                    "reason": "The complete wall fits the duration-derived capacity.",
                }
                for card in cards
            ],
            "overall_reason": "Every surviving moment fits without a scarcity cut.",
        }, None
    call = await _ask_text(
        _selection_prompt(cards, case=case, facts=facts, thesis=thesis, capacity=capacity),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=4000,
        timeout_seconds=timeout_seconds,
    )
    aliases = frozenset(card.moment.alias for card in cards)
    return prototype._read_selection(call.raw, aliases, capacity), call


def _allocation_prompt(
    case: Case,
    thesis: dict[str, Any],
    readings: tuple[tuple[Chapter, dict[str, Any]], ...],
    capacity: int,
) -> str:
    chapters = [
        {
            "chapter_id": chapter.chapter_id,
            "label": chapter.label,
            "available_moments": len(chapter.cards),
            "reading": reading,
        }
        for chapter, reading in readings
    ]
    shape = {
        "schema_version": ALLOCATION_SCHEMA,
        "allocations": [{"chapter_id": "C001", "slots": 2, "reason": "why"}],
        "overall_reason": "how scarcity is distributed",
    }
    return f"""Allocate at most {capacity} whole-moment slots across every chronological chapter
of {case.label}. This is a scarcity decision, not an equal-period quota and not an instruction to
fill every slot. A chapter may receive zero. Dense photography is not importance. Give room to the
chapters needed to establish, advance, complicate, contrast with, or add ordinary texture to the
global thesis. Preserve credible one-off turning points. Never allocate more than a chapter's
available_moments. Keep chapter IDs chronological and include every chapter exactly once.

EDITORIAL BRIEF
{case.brief}

GLOBAL THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

CHAPTERS
{json.dumps(chapters, ensure_ascii=False, separators=(",", ":"))}

Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}"""


def _read_allocation(raw: str, chapters: tuple[Chapter, ...], capacity: int) -> dict[str, Any]:
    payload = final_json_object(raw)
    if payload is None or set(payload) != {"schema_version", "allocations", "overall_reason"}:
        raise ValueError("chapter allocation has the wrong envelope")
    if payload.get("schema_version") != ALLOCATION_SCHEMA:
        raise ValueError("chapter allocation has the wrong schema version")
    rows = payload.get("allocations")
    if not isinstance(rows, list) or len(rows) != len(chapters):
        raise ValueError("chapter allocation must account for every chapter")
    by_id = {chapter.chapter_id: chapter for chapter in chapters}
    parsed = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"chapter_id", "slots", "reason"}:
            raise ValueError("chapter allocation row has the wrong shape")
        chapter_id, slots = row.get("chapter_id"), row.get("slots")
        reason = bounded_model_text(row.get("reason"), max_chars=400)
        if chapter_id not in by_id or not isinstance(slots, int) or isinstance(slots, bool):
            raise ValueError("chapter allocation row is not grounded")
        if slots < 0 or slots > len(by_id[chapter_id].cards) or reason is None:
            raise ValueError("chapter allocation exceeds its chapter")
        parsed.append({"chapter_id": chapter_id, "slots": slots, "reason": reason})
    expected = [chapter.chapter_id for chapter in chapters]
    if [row["chapter_id"] for row in parsed] != expected or sum(
        row["slots"] for row in parsed
    ) > capacity:
        raise ValueError("chapter allocation is unordered or exceeds total capacity")
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=500)
    if overall is None:
        raise ValueError("chapter allocation overall reason is unsafe")
    return {"allocations": parsed, "overall_reason": overall}


async def _hierarchical_selection(
    chapters: tuple[Chapter, ...],
    readings: tuple[tuple[Chapter, dict[str, Any]], ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    capacity: int,
    llm_config: Any,
    cache_path: Path,
    concurrency: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], tuple[TextCall, ...]]:
    allocation_call = await _ask_text(
        _allocation_prompt(case, thesis, readings, capacity),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=3000,
        timeout_seconds=timeout_seconds,
    )
    allocation = _read_allocation(allocation_call.raw, chapters, capacity)
    slots = {row["chapter_id"]: row["slots"] for row in allocation["allocations"]}
    semaphore = asyncio.Semaphore(concurrency)

    async def cut(chapter: Chapter) -> tuple[dict[str, Any], TextCall | None]:
        if slots[chapter.chapter_id] == 0:
            return {"keep": [], "overall_reason": "This chapter received no scarce slots."}, None
        async with semaphore:
            return await _select_cards(
                chapter.cards,
                case=case,
                facts=facts,
                thesis=_local_thesis(thesis, chapter.cards),
                capacity=slots[chapter.chapter_id],
                llm_config=llm_config,
                cache_path=cache_path,
                timeout_seconds=timeout_seconds,
            )

    cuts = await asyncio.gather(*(cut(chapter) for chapter in chapters))
    by_id = {row["moment_id"]: row for selection, _call in cuts for row in selection["keep"]}
    ordered_cards = [card for chapter in chapters for card in chapter.cards]
    selection = {
        "keep": [by_id[card.moment.alias] for card in ordered_cards if card.moment.alias in by_id],
        "overall_reason": allocation["overall_reason"],
    }
    calls = (allocation_call, *tuple(call for _selection, call in cuts if call is not None))
    return selection, allocation, calls


def _call_record(call: TextCall) -> dict[str, Any]:
    return asdict(call)


async def _text_phase(
    case: Case,
    prepared: Any,
    descriptions: tuple[prototype.Description, ...],
    *,
    facts: dict[str, PersonFact],
    config: Any,
    out: Path,
    text_model: str,
    concurrency: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    moments = prototype._moments(prepared, descriptions)
    cache_path = out.parent / "text-judgments.db"
    llm_config = config.llm.model_copy(update={"model": text_model})
    cards_started = time.monotonic()
    cards, card_calls = await _build_cards(
        moments,
        facts=facts,
        llm_config=llm_config,
        cache_path=cache_path,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
    )
    cards_seconds = time.monotonic() - cards_started
    _atomic_json(
        out / "cards.json",
        {
            "cards": [
                {
                    **prototype._card_record(card),
                    "people_metadata": list(_moment_people(card.moment, facts)),
                    "card_line": _enriched_line(card, facts),
                    "summary_call": _call_record(call),
                }
                for card, call in zip(cards, card_calls, strict=True)
            ]
        },
    )
    capacity = _capacity(config, case, prepared.candidates)
    moment_capacity = int(capacity["moment_capacity"])
    thesis_started = time.monotonic()
    if len(cards) <= FLAT_WALL_MAX_CARDS:
        thesis, thesis_call = await _read_thesis(
            cards,
            case=case,
            facts=facts,
            llm_config=llm_config,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
        )
        chapter_readings: tuple[tuple[Chapter, dict[str, Any]], ...] = ()
        thesis_calls = (thesis_call,)
        chapters: tuple[Chapter, ...] = ()
        shape = "flat"
    else:
        chapters = _chapters(case, cards)
        thesis, chapter_readings, thesis_calls = await _hierarchical_thesis(
            chapters,
            case=case,
            facts=facts,
            llm_config=llm_config,
            cache_path=cache_path,
            concurrency=concurrency,
            timeout_seconds=timeout_seconds,
        )
        shape = "hierarchical"
    thesis_seconds = time.monotonic() - thesis_started
    selection_started = time.monotonic()
    allocation = None
    if shape == "flat":
        selection, selection_call = await _select_cards(
            cards,
            case=case,
            facts=facts,
            thesis=thesis,
            capacity=moment_capacity,
            llm_config=llm_config,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
        )
        selection_calls = () if selection_call is None else (selection_call,)
    else:
        selection, allocation, selection_calls = await _hierarchical_selection(
            chapters,
            chapter_readings,
            case=case,
            facts=facts,
            thesis=thesis,
            capacity=moment_capacity,
            llm_config=llm_config,
            cache_path=cache_path,
            concurrency=concurrency,
            timeout_seconds=timeout_seconds,
        )
    selection_seconds = time.monotonic() - selection_started
    kept = {row["moment_id"] for row in selection["keep"]}
    favourite_moments = {
        card.moment.alias
        for card in cards
        if any(candidate.favourite for candidate in card.moment.group.candidates)
    }
    all_calls = (*card_calls, *thesis_calls, *selection_calls)
    return {
        "configuration": {
            "text_model": text_model,
            "temperature": 0.0,
            "thinking": False,
            "concurrency": concurrency,
            "shape": shape,
            "flat_wall_max_cards": FLAT_WALL_MAX_CARDS,
            "chapter_max_cards": CHAPTER_MAX_CARDS,
            "capacity": capacity,
        },
        "counts": {
            "moments": len(cards),
            "chapters": len(chapters),
            "selected_moments": len(kept),
            "favourite_moments": len(favourite_moments),
            "selected_favourite_moments": len(kept & favourite_moments),
        },
        "cache": {
            "text_calls": len(all_calls),
            "text_cache_hits": sum(call.cache_hit for call in all_calls),
        },
        "timings": {
            "cards_seconds": cards_seconds,
            "thesis_seconds": thesis_seconds,
            "selection_seconds": selection_seconds,
        },
        "thesis": thesis,
        "chapter_readings": [
            {"chapter_id": chapter.chapter_id, "label": chapter.label, **reading}
            for chapter, reading in chapter_readings
        ],
        "allocation": allocation,
        "selection": selection,
        "thesis_calls": [_call_record(call) for call in thesis_calls],
        "selection_calls": [_call_record(call) for call in selection_calls],
    }


def _run_case(
    client: SyncImmichClient,
    config: Any,
    case: Case,
    people: dict[str, Any],
    facts: dict[str, PersonFact],
    args: argparse.Namespace,
) -> None:
    out = args.out / case.key
    if (out / "result.json").exists():
        print(f"{case.key}: complete result already exists; skipping", flush=True)
        return
    out.mkdir(parents=True, exist_ok=True)
    total_started = time.monotonic()
    fetch_started = time.monotonic()
    assets = _fetch_assets(client, config, case, people)
    fetch_seconds = time.monotonic() - fetch_started
    print(f"{case.key}: fetched {len(assets)} assets", flush=True)
    if not assets:
        _atomic_json(
            out / "result.json",
            {
                "case": asdict(case),
                "status": "empty",
                "counts": {"fetched": 0},
                "timings": {"fetch_seconds": fetch_seconds, "total_seconds": fetch_seconds},
            },
        )
        return

    thumbnail_cache = ThumbnailCache(
        cache_dir=config.cache.cache_path / "thumbnails",
        max_size_mb=config.cache.thumbnail_cache_max_size_mb,
    )
    asset_ids = {asset.id for asset in assets}
    cached_before = thumbnail_cache.cached_ids(asset_ids, THUMBNAIL_SIZE)
    thumb_started = time.monotonic()
    ThumbnailPrefetcher.from_client(
        client,
        thumbnail_cache,
        api_policy=config.immich.api_version,
        max_workers=config.analysis.download_workers,
    ).ensure_cached([SimpleNamespace(asset=asset) for asset in assets])
    thumbnail_seconds = time.monotonic() - thumb_started
    cached_after = thumbnail_cache.cached_ids(asset_ids, THUMBNAIL_SIZE)

    scope = SourceScope(
        excluded_filename_patterns=tuple(config.analysis.exclude_filename_patterns),
        stills_need_a_camera=config.analysis.exclude_stills_without_camera_exif,
        min_source_short_side=config.analysis.min_source_short_side,
        include_off_timeline=False,
    )
    dependencies = EditorialDependencies(
        source_fetcher=lambda _scope: assets,
        preview_jpeg=lambda asset: cached_preview_bytes(thumbnail_cache, asset.id),
    )
    upstream_started = time.monotonic()
    upstream = _run_upstream(
        EditorialSelectionRequest(scope=scope),
        dependencies,
        config=config,
        out=out,
        corpus_selects=args.corpus_selects,
    )
    upstream_seconds = time.monotonic() - upstream_started
    description_workprint = None
    if upstream.pass_two is not None:
        survivors = upstream.pass_two.survivors
        subset = _description_subset(upstream.prepared, survivors)
    else:
        description_workprint = build_description_workprint(
            upstream.structure_workprint,
            chapter_key=lambda moment: _description_chapter_key(case, moment),
            relationship_names=_relationship_context_names(case, facts),
        )
        survivors = description_workprint.candidates
        subset = _description_subset(
            upstream.prepared,
            survivors,
            admitted_moments=description_workprint.moments,
        )
    pipeline_order = "as-built-corpus-selects" if args.corpus_selects else "refined-pre-cut"
    _atomic_json(
        out / "upstream.json",
        {
            "case": asdict(case),
            "pipeline_order": pipeline_order,
            "counts": {
                "fetched": len(assets),
                "source_eligible": len(upstream.prepared.candidates),
                "cull_survivors": len(upstream.pass_one.survivors),
                "structure_representatives": len(upstream.structure_workprint.moments),
                "corpus_selects_survivors": (
                    len(upstream.pass_two.survivors) if upstream.pass_two is not None else None
                ),
                "editor_input_assets": len(survivors),
                "editor_input_moments": len(subset.moment_groups),
            },
            "description_allocation": _description_allocation_record(description_workprint),
            "cache": {
                "thumbnail_hits": len(cached_before),
                "thumbnail_misses": len(asset_ids - cached_before),
                "thumbnails_available_after": len(cached_after),
                "visual_requests": _request_metrics(upstream.prepared.trace),
            },
            "timings": {
                "fetch_seconds": fetch_seconds,
                "thumbnail_seconds": thumbnail_seconds,
                "upstream_seconds": upstream_seconds,
            },
            "trace": upstream.prepared.trace.as_dict(),
        },
    )
    print(
        f"{case.key}: {len(upstream.prepared.candidates)} eligible -> "
        f"{len(upstream.pass_one.survivors)} Cull -> "
        f"{len(subset.moment_groups)} moments -> {len(survivors)} description assets "
        f"[{pipeline_order}]",
        flush=True,
    )
    if description_workprint is not None:
        allocation = _description_allocation_record(description_workprint) or {}
        reason_counts = allocation.get("reason_counts", {})
        print(
            f"{case.key}: admission reasons: "
            f"{reason_counts.get('favourite-moment', 0)} favourite moments, "
            f"{reason_counts.get('unstarred-chapter', 0)} moments from unstarred chapters, "
            f"{reason_counts.get('relationship-context', 0)} relationship repairs, "
            f"{reason_counts.get('first-copresence', 0)} first appearances, "
            f"{reason_counts.get('resumption', 0)} resumptions",
            flush=True,
        )

    description_started = time.monotonic()
    gateway = _ProgressGateway(
        VisualEditorialGateway(
            llm_config=config.llm,
            cache_path=verdicts_beside(config.cache.cache_path),
            trace=upstream.prepared.trace,
        ),
        expected_tiles={"asset-description": len(subset.candidates)},
    )
    try:
        described = describe_editorial_assets(
            subset,
            requester=gateway,
            output_dir=out / "description-sheets",
            frame_cache_dir=config.cache.cache_path / "editorial-frames",
            atlas=upstream.pass_zero.atlas,
            concurrency=args.concurrency,
        )
    finally:
        gateway.close()
    by_id = {item.asset_id: item for item in described.descriptions}
    descriptions = tuple(
        prototype.Description(
            candidate.asset_id,
            by_id[candidate.asset_id].text
            if candidate.asset_id in by_id
            else "[visual description unavailable]",
        )
        for candidate in subset.candidates
    )
    description_seconds = time.monotonic() - description_started
    _atomic_json(
        out / "description-bank.json",
        {
            "privacy": "private lifetime asset descriptions; do not commit",
            "descriptions": [asdict(description) for description in descriptions],
            "warnings": list(described.warnings),
            "cache": {
                "hits": sum(item.provenance.cache_hit for item in described.descriptions),
                "actual_calls": sum(
                    request.actual_calls
                    for request in upstream.prepared.trace.requests
                    if request.provenance.pass_name == "asset-description"  # noqa: S105
                ),
            },
            "timings": {"description_seconds": description_seconds},
        },
    )
    print(
        f"{case.key}: described {len(described.descriptions)}/{len(subset.candidates)} "
        f"in {description_seconds:.1f}s",
        flush=True,
    )
    text_result = asyncio.run(
        _text_phase(
            case,
            subset,
            descriptions,
            facts=facts,
            config=config,
            out=out,
            text_model=args.text_model,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
        )
    )
    result = {
        "schema_version": "smart-edit-matrix-case-v1",
        "privacy": "private matrix artifact; do not commit IDs, names, descriptions, or answers",
        "case": asdict(case),
        "vision_model": config.llm.model,
        "source": {
            "pipeline_order": pipeline_order,
            "fetched": len(assets),
            "source_eligible": len(upstream.prepared.candidates),
            "cull_survivors": len(upstream.pass_one.survivors),
            "structure_representatives": len(upstream.structure_workprint.moments),
            "corpus_selects_survivors": (
                len(upstream.pass_two.survivors) if upstream.pass_two is not None else None
            ),
            "editor_input_assets": len(survivors),
            "editor_input_moments": len(subset.moment_groups),
        },
        "description_allocation": _description_allocation_record(description_workprint),
        "cache": {
            "thumbnail_hits": len(cached_before),
            "thumbnail_misses": len(asset_ids - cached_before),
            "visual_requests": _request_metrics(upstream.prepared.trace),
            **text_result["cache"],
        },
        "timings": {
            "fetch_seconds": fetch_seconds,
            "thumbnail_seconds": thumbnail_seconds,
            "upstream_seconds": upstream_seconds,
            "description_seconds": description_seconds,
            **text_result["timings"],
            "total_seconds": time.monotonic() - total_started,
        },
        "edit": {
            key: value for key, value in text_result.items() if key not in {"cache", "timings"}
        },
    }
    _atomic_json(out / "result.json", result)
    print(
        f"{case.key}: selected {text_result['counts']['selected_moments']}/"
        f"{text_result['counts']['moments']} moments in {result['timings']['total_seconds']:.1f}s",
        flush=True,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Case key; repeat to run several")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    parser.add_argument("--text-model", default=DEFAULT_TEXT_MODEL)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--corpus-selects",
        action="store_true",
        help="replay the obsolete as-built Selects-before-cut order as a cost baseline",
    )
    args = parser.parse_args()
    args.out = args.out.expanduser().resolve()
    args.cases_file = args.cases_file.expanduser().resolve()
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    if not args.out.is_relative_to(matrix):
        parser.error("--out must be inside ~/.immich-memories-matrix")
    if not args.cases_file.is_relative_to(matrix):
        parser.error("--cases-file must be inside ~/.immich-memories-matrix")
    if not args.cases_file.is_file():
        parser.error(f"case manifest does not exist: {args.cases_file}")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    return args


def main() -> int:
    args = _arguments()
    cases = _load_cases(args.cases_file)
    wanted = set(args.case or ())
    unknown = wanted - {case.key for case in cases}
    if unknown:
        raise SystemExit(f"unknown case(s): {', '.join(sorted(unknown))}")
    selected = tuple(case for case in cases if not wanted or case.key in wanted)
    config = get_config()
    config.llm = config.llm.model_copy(update={"model": args.vision_model})
    facts = _person_facts()
    names = {name for case in selected for name in case.people}
    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        people = {}
        for name in sorted(names):
            person = client.get_person_by_name(name)
            if person is None:
                raise RuntimeError(f"person not found: {name}")
            people[name] = person
        for index, case in enumerate(selected, start=1):
            print(f"matrix {index}/{len(selected)}: {case.key}", flush=True)
            _run_case(client, config, case, people, facts, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
