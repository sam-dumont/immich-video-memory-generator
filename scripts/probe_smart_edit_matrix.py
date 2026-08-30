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
import hashlib
import io
import json
import math
import os
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from datetime import time as datetime_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import probe_description_moment_cut as prototype
from PIL import Image, ImageStat
from probe_description_allocation import DescriptionWorkprint, build_description_workprint
from probe_people_context import PersonFact, load_person_facts
from probe_selection_final_cut import (
    AUDIT_MAX_TILES_PER_REQUEST,
    FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
    FINAL_VISUAL_POOL_GLOBAL_VALIDATION_SCHEMA,
    FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA,
    FineCutCandidate,
    apply_closer_luminance_swap,
    apply_final_asset_sequence_review,
    apply_final_moment_cap,
    final_asset_audit_prompt,
    final_asset_cut_prompt,
    final_asset_delta_validation_prompt,
    final_asset_reconsideration_prompt,
    final_asset_sequence_review_prompt,
    mark_closing_candidate,
    merge_final_asset_audits,
    read_final_asset_audit,
    read_final_asset_cut,
    read_final_asset_delta_validation,
    read_final_asset_reconsideration,
    read_visual_final_asset_audit,
    read_visual_final_pool_global_validation,
    read_visual_final_pool_reconsideration,
    runtime_final_asset_audit_findings,
    runtime_final_pool_findings,
    visual_final_asset_audit_groups,
    visual_final_asset_audit_prompt,
    visual_final_pool_global_validation_prompt,
    visual_final_pool_groups,
    visual_final_pool_reconsideration_prompt,
    visual_final_pool_request_groups,
)

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.album_source import fetch_album_media
from immich_memories.analysis.contact_sheets import build_contact_sheets
from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash, hamming_distance
from immich_memories.analysis.editorial_gateway import (
    VisualEditorialGateway,
    VisualEditorialRequest,
)
from immich_memories.analysis.live_photo_pipeline import drop_live_photo_components
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.period_insight import run_period_insight
from immich_memories.analysis.selection_cull import run_cull
from immich_memories.analysis.selection_descriptions import describe_editorial_assets
from immich_memories.analysis.selection_final_duplicates import (
    CopyEmbedder,
    FinalDuplicateReview,
    review_final_duplicates,
)
from immich_memories.analysis.selection_flow import run_editorial_selection
from immich_memories.analysis.selection_selects import SELECTS_MAX_CORROBORATION, run_selects
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
from immich_memories.analysis.visual_atlas import build_visual_atlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.api.person_scope import videos_in_window
from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.cache.judgment_cache import (
    JudgmentCache,
    judgment_key,
    verdicts_beside,
)
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.cache.video_cache import VideoDownloadCache
from immich_memories.cli._asset_fetch import fetch_photos
from immich_memories.cli._date_resolution import default_duration_for_type
from immich_memories.cli._trip_generation import _filter_photos_near_trip
from immich_memories.config import get_config
from immich_memories.config_models_llm import LLMConfig
from immich_memories.filename_builder import get_divider_mode
from immich_memories.processing.timeline_budget import plan_timeline
from immich_memories.timeperiod import DateRange

DEFAULT_OUT = Path.home() / ".immich-memories-matrix" / "smart-edit-case-matrix"
DEFAULT_CASES_FILE = Path.home() / ".immich-memories-matrix" / "smart-edit-cases.json"
DEFAULT_VISION_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
DEFAULT_TEXT_MODEL = DEFAULT_VISION_MODEL
_PROVIDER_PRICES_PER_MILLION = {
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "glm-4.6v": (0.30, 0.05, 0.90),
    "qwen3.8-27b": (0.40, 0.10, 2.40),
    "qwen3-vl-235b-a22b-instruct": (0.20, 0.20, 1.80),
    "qwen2.5-vl-72b-instruct": (0.25, 0.25, 0.75),
}
_PROVIDER_PRICE_CURRENCY = {
    "gpt-5.6-luna": "USD",
    "glm-4.6v": "USD",
    "qwen3.8-27b": "EUR",
    "qwen3-vl-235b-a22b-instruct": "EUR",
    "qwen2.5-vl-72b-instruct": "EUR",
}
FLAT_WALL_MAX_CARDS = 160
CHAPTER_MAX_CARDS = 120
# Frozen flat-wall controls remained useful through roughly 80k serialized wall
# characters. The failed 2007 year crossed 112k before prompt instructions and
# collapsed to 13/144 available moments. Keep margin for the thesis, schema, and
# output rather than spending the local model's whole context on card prose.
EDITORIAL_WALL_MAX_CHARS = 90_000
# The v28 October chapter kept three moments of one 2007-10-11 evening (19:20,
# 19:54, 23:00). The downstream asset cap reasons per moment, so it was already
# satisfied; the owner's rule is per occasion. A calendar day is the occasion
# baseline: it is the coarsest unit the moment wall already carries for free.
OCCASION_MOMENT_CAP = 2
ALLOCATION_SCHEMA = "description-chapter-allocation-v1"
FUSED_CARD_PASS_VERSION = "fused-moment-card-v2"  # noqa: S105 - wire identity
FUSED_CARD_PROMPT_VERSION = "fused-moment-card-prompt-v2"
FUSED_CARD_RENDER_VERSION = "fused-moment-400px-v1"
# Measured on 30 banked 2007 moments against the 27B: three guessable keys whose
# placeholder names its own out ("or insufficient evidence") moved people-free
# scenes from invented prose into a structured hedge, with zero invented names.
HEDGED_CARD_SHAPE = {
    "schema_version": prototype.CARD_SCHEMA,
    "summary": "literal inventory of the moment",
    "people": "who is visible, or insufficient evidence",
    "relations": "how they are related, or insufficient evidence",
    "activity": "what they are doing, or insufficient evidence",
}
HEDGED_CARD_SENTENCE = (
    "Write exactly insufficient evidence for people, relations, "
    "or activity the visuals do not show."
)
COMPACT_CARD_KEYS = frozenset({"schema_version", "summary"})
# Two of thirty answers echoed the retired card schema version that the banked
# upstream prose still carries. The envelope was otherwise sound, so the reader
# accepts it and records the echo instead of discarding a usable card.
RETIRED_CARD_SCHEMA = "description-moment-card-v1"  # noqa: S105 - wire identity
FUSED_CARD_RETRY_PASS_VERSION = "fused-moment-card-compact-retry-v1"  # noqa: S105
FUSED_CARD_RETRY_PROMPT_VERSION = "fused-moment-card-compact-retry-prompt-v1"
VISUAL_FINAL_AUDIT_PASS_NAME = "visual-final-wall-audit"  # noqa: S105 - wire identity
VISUAL_FINAL_AUDIT_PASS_VERSION = "visual-final-wall-audit-v1"  # noqa: S105
VISUAL_FINAL_AUDIT_PROMPT_VERSION = "visual-final-wall-audit-prompt-v4"
VISUAL_FINAL_AUDIT_RENDER_VERSION = "visual-final-wall-audit/preview-250px-v1"
VISUAL_FINAL_POOL_PASS_NAME = "visual-final-pool-reconsideration"  # noqa: S105
VISUAL_FINAL_POOL_PASS_VERSION = "visual-final-pool-reconsideration-v1"  # noqa: S105
VISUAL_FINAL_POOL_PROMPT_VERSION = "visual-final-pool-reconsideration-prompt-v1"
VISUAL_FINAL_POOL_RENDER_VERSION = "visual-final-pool/preview-180px-v1"
VISUAL_FINAL_POOL_GLOBAL_PASS_NAME = "visual-final-pool-global-validation"  # noqa: S105
VISUAL_FINAL_POOL_GLOBAL_PASS_VERSION = "visual-final-pool-global-validation-v1"  # noqa: S105
VISUAL_FINAL_POOL_GLOBAL_PROMPT_VERSION = "visual-final-pool-global-validation-prompt-v9"
VISUAL_FINAL_POOL_GLOBAL_RENDER_VERSION = "visual-final-pool-global/preview-250px-v3"
DISPLAY_DOCTRINE = """Separate lived experience from evidence that it happened. For a sustained
thread, prefer a lived scene showing action, relationship, expression, place, or atmosphere over
material whose value is only to label, measure, summarize, or prove the same thread. Evidentiary
material earns a slot only when it establishes an irreplaceable consequential fact that no lived
scene can carry. Ordinary texture means lived atmosphere or relationship, not arbitrary inventory.
When setup evidence and the event it sets up are both present, do not spend two slots unless each
adds a different necessary beat."""


def _required_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"one of {', '.join(names)} must be set")


def _hosted_llm_config(provider: str, model: str) -> LLMConfig:
    """Build one no-thinking provider config without exposing credentials."""
    if provider == "openai":
        return LLMConfig(
            provider="openai",
            model=model,
            api_key=_required_env("OPENAI_KEY", "OPENAI_API_KEY"),
            thinking=False,
            no_thinking_params={},
            max_tokens_param="max_completion_tokens",
            drop_params=["temperature"],
            extra_params={"reasoning_effort": "none"},
        )
    if provider == "zai":
        return LLMConfig(
            provider="zai",
            base_url=os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4"),
            model=model,
            api_key=_required_env("ZAI_API_KEY"),
            thinking=False,
            no_thinking_params={"thinking": {"type": "disabled"}},
            send_image_detail=False,
        )
    if provider == "melious":
        extra_params = {"reasoning_effort": "none"} if model == "qwen3.8-27b" else {}
        return LLMConfig(
            provider="openai-compatible",
            base_url=_required_env("MELIOUS_AI_BASE_URL"),
            model=model,
            api_key=_required_env("MELIOUS_AI_KEY"),
            thinking=False,
            no_thinking_params={},
            extra_params=extra_params,
        )
    raise ValueError(f"unsupported hosted provider: {provider}")


def _cold_config(config: Any, cache_root: Path) -> Any:
    """Move every cache derived from Config.cache below one fresh root."""
    cache = config.cache.model_copy(
        update={"directory": str(cache_root), "database": str(cache_root / "cache.db")}
    )
    return config.model_copy(update={"cache": cache})


def _estimated_provider_cost(model: str, metrics: dict[str, Any]) -> float | None:
    rates = _PROVIDER_PRICES_PER_MILLION.get(model)
    if rates is None:
        return None
    input_rate, cached_rate, output_rate = rates
    prompt_tokens = int(metrics.get("llm_prompt_tokens", 0))
    cached_tokens = int(metrics.get("llm_cached_prompt_tokens", 0))
    completion_tokens = int(metrics.get("llm_completion_tokens", 0))
    uncached_tokens = max(0, prompt_tokens - cached_tokens)
    return (
        uncached_tokens * input_rate + cached_tokens * cached_rate + completion_tokens * output_rate
    ) / 1_000_000


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    product: str
    ranges: tuple[DateRange, ...]
    target_seconds: float
    brief: str
    target_source: str = "manifest"
    people: tuple[str, ...] = ()
    person_match: str = "and"
    accept_any_provenance: bool = False
    trip: bool = False
    album_ref: str | None = None


@dataclass(frozen=True)
class TextCall:
    prompt: str
    raw: str
    wall_seconds: float
    cache_hit: bool
    thinking: bool
    warning: str | None = None
    # A vision pass assembles its wire prompt from more than this text, so a banked
    # card cannot be reproduced from `prompt` alone. Carry what the rest was.
    prompt_provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    label: str
    cards: tuple[prototype.MomentCard, ...]


@dataclass(frozen=True)
class LifecycleRequirement:
    """One graph-grounded onset the editorial model is not allowed to erase."""

    person_name: str
    relationship: str
    onset_month: str
    anchor_id: str
    eligible_ids: tuple[str, ...]
    fact: str


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
        product = str(row["product"])
        album_ref_value = row.get("album_ref")
        album_ref = str(album_ref_value).strip() if album_ref_value is not None else None
        if product == "album" and not album_ref:
            raise ValueError(
                f"case {row.get('key', '<unknown>')} is an album but has no album_ref"
            )
        if product != "album" and album_ref:
            raise ValueError(
                f"case {row.get('key', '<unknown>')} has album_ref but product is {product!r}"
            )
        person_match = str(row.get("person_match", "and"))
        if person_match not in {"and", "or"}:
            raise ValueError(
                f"case {row.get('key', '<unknown>')} has invalid person_match {person_match!r}"
            )
        target_value = row.get("target_seconds", "auto")
        target_source = "manifest"
        if target_value in {None, "auto"}:
            if len(ranges) != 1:
                raise ValueError(
                    f"case {row.get('key', '<unknown>')} needs an explicit target_seconds "
                    "for a non-contiguous scope"
                )
            target_value = default_duration_for_type(product, ranges[0])
            if target_value is None:
                raise ValueError(
                    f"case {row.get('key', '<unknown>')} has no production auto-duration"
                )
            target_source = "production-auto"
        cases.append(
            Case(
                key=str(row["key"]),
                label=str(row["label"]),
                product=product,
                ranges=ranges,
                target_seconds=float(target_value),
                brief=str(row["brief"]),
                target_source=target_source,
                people=tuple(str(name) for name in row.get("people", ())),
                person_match=person_match,
                accept_any_provenance=bool(row.get("accept_any_provenance", False)),
                trip=bool(row.get("trip", False)),
                album_ref=album_ref,
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
    if case.album_ref is not None:
        album = client.resolve_album(case.album_ref)
        media = fetch_album_media(
            client,
            album,
            config=config,
            use_live_photos=True,
            use_photos=True,
        )
        return _dedupe([*media.videos, *media.photos])

    person_ids = [people[name].id for name in case.people]
    photos = fetch_photos(
        client=client,
        date_ranges=list(case.ranges),
        person_ids=person_ids,
        person_match=case.person_match,
    )
    videos = [
        asset
        for window in case.ranges
        for asset in videos_in_window(
            client,
            person_ids,
            window,
            person_match=case.person_match,
        )
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


_ENDURING_ONSET_KINDS = {
    "partner-of",
    "spouse-of",
    "best-friend-of",
    "friend-of",
}
_FAMILY_ARRIVAL_KINDS = {
    "child-of",
    "son-of",
    "daughter-of",
    "nibling-of",
    "grandchild-of",
    "godchild-of",
}


def _role_words(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _is_enduring_onset(fact: PersonFact) -> bool:
    kinds = set(fact.owner_relationship_kinds)
    role = _role_words(fact.relationship)
    return bool(kinds & _ENDURING_ONSET_KINDS) or role in {
        "partner",
        "spouse",
        "best friend",
        "friend",
    }


def _month_distance(one: str, two: str) -> int | None:
    try:
        first = date.fromisoformat(f"{one}-01")
        second = date.fromisoformat(f"{two[:7]}-01")
    except ValueError:
        return None
    return abs((first.year - second.year) * 12 + first.month - second.month)


def _is_family_arrival(fact: PersonFact) -> bool:
    if not fact.birth_date or not fact.onset:
        return False
    kinds = set(fact.owner_relationship_kinds)
    role = _role_words(fact.relationship)
    family_role = bool(kinds & _FAMILY_ARRIVAL_KINDS) or role in {
        "child",
        "son",
        "daughter",
        "nibling",
        "niece",
        "nephew",
        "niece or nephew of library owner",
        "grandchild",
        "godchild",
    }
    distance = _month_distance(fact.onset, fact.birth_date)
    return family_role and distance is not None and distance <= 12


def _moment_has_person(moment: prototype.Moment, name: str) -> bool:
    return any(
        str(person.name or "").strip() == name
        for candidate in moment.group.candidates
        for person in (candidate.source.people or ())
    )


def _lifecycle_requirements(
    cards: tuple[prototype.MomentCard, ...],
    facts: dict[str, PersonFact],
) -> tuple[LifecycleRequirement, ...]:
    """Turn current graph facts plus onset evidence into mandatory first sightings."""
    requirements: list[LifecycleRequirement] = []
    for name, fact in sorted(facts.items()):
        if not fact.relationship_current or not fact.onset:
            continue
        enduring = _is_enduring_onset(fact)
        family_arrival = _is_family_arrival(fact)
        if not (enduring or family_arrival):
            continue
        eligible = tuple(
            card.moment.alias
            for card in cards
            if card.moment.group.candidates[0].taken_at.strftime("%Y-%m") == fact.onset
            and _moment_has_person(card.moment, name)
        )
        if not eligible:
            continue
        graph_basis = (
            "a current relationship still present in the confirmed people graph"
            if fact.relationship_source == "confirmed"
            else "a current relationship derived only through confirmed people-graph edges"
        )
        meaning = (
            "arrival into recorded family life near birth"
            if family_arrival
            else "recorded beginning of an enduring relationship"
        )
        requirements.append(
            LifecycleRequirement(
                person_name=name,
                relationship=fact.relationship,
                onset_month=fact.onset,
                anchor_id=eligible[0],
                eligible_ids=eligible,
                fact=f"{meaning}; {graph_basis}; sustained library onset {fact.onset}",
            )
        )
    return tuple(requirements)


def _lifecycle_block(requirements: tuple[LifecycleRequirement, ...]) -> str:
    if not requirements:
        return "None in this wall."
    return "\n".join(
        f"- {item.person_name}: {item.relationship}; {item.fact}; "
        f"runtime anchor={item.anchor_id}; onset-period evidence={list(item.eligible_ids)}"
        for item in requirements
    )


def _with_lifecycle_turning_points(
    thesis: dict[str, Any], requirements: tuple[LifecycleRequirement, ...]
) -> dict[str, Any]:
    if not requirements:
        return thesis
    turning = list(thesis.get("turning_points", []))
    for item in requirements:
        turning.append(
            {
                "summary": (
                    f"{item.person_name}'s {item.fact}; the runtime preserves the earliest "
                    "grounded onset moment"
                ),
                "evidence_moment_ids": [item.anchor_id],
            }
        )
    return {**thesis, "turning_points": turning}


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
                EditorialGroup(
                    moment.moment_id,
                    tuple(
                        candidate
                        for candidate in moment.candidates
                        if candidate.asset_id in survivor_ids
                    ),
                )
                for moment in admitted_moments
            )
            if admitted_moments is not None
            else build_moment_groups(survivors)
        ),
    )


def _fetch_motion_rendition(
    client: SyncImmichClient, asset: Any, *, cache_dir: Path
) -> Path | None:
    """Fetch Immich's own transcoded /video/playback rendition for motion sampling.

    A filmstrip only ever samples a handful of small frames from one proposed
    segment; it never needs the full-resolution original. Piping the rendition
    Immich already transcodes server-side, instead of downloading the original
    and downscaling it locally (what `_fetch_motion_original` still does), is
    what the analysis-downscale timing was measured against: 115s -> 9.2s cold
    on a real clip. Live Photos sample the video component, same as the
    original-download path.
    """
    video_id = asset.live_photo_video_id or asset.id
    subdir = video_id[:2] if len(video_id) >= 2 else "00"
    dest = cache_dir / subdir / f"{video_id}_rendition.mp4"
    if dest.exists() and dest.stat().st_size > 1024:
        return dest
    try:
        video_bytes = client.get_video_playback(video_id)
        if not video_bytes or len(video_bytes) <= 1024:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(video_bytes)
    except Exception:  # noqa: BLE001 - any failure here must fall open onto the original
        return None
    return dest


def _fetch_motion_original(client: SyncImmichClient, candidate: Any, batch: Any) -> Path | None:
    """The slow path: download the full original, then downscale it locally."""
    try:
        path, _original_path = batch.get_analysis_video(
            client,
            candidate.source,
            target_height=480,
            enable_downscaling=True,
            gpu_decode=True,
        )
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def _resolve_motion_path(
    candidate: Any,
    *,
    client: SyncImmichClient,
    batch: Any,
    cache_dir: Path,
    existing_motion_paths: dict[str, Path],
    warned: set[str],
) -> Path | None:
    """Prefer the small Immich rendition; fall back to the full original.

    Fail-open at every step: a rendition or original fetch failing must never
    drop a candidate's motion evidence outright when an earlier motion path
    (e.g. from a pre-merged Live Photo) is already known.
    """
    path = _fetch_motion_rendition(client, candidate.source, cache_dir=cache_dir)
    if path is not None:
        return path
    if candidate.asset_id not in warned:
        print(
            f"motion rendition unavailable for {candidate.asset_id}, falling back to original",
            flush=True,
        )
        warned.add(candidate.asset_id)
    path = _fetch_motion_original(client, candidate, batch)
    return path if path is not None else existing_motion_paths.get(candidate.asset_id)


def _final_refinement_atlas(
    *,
    client: SyncImmichClient,
    config: Any,
    prepared: Any,
    candidates: tuple[Any, ...],
    frame_cache_dir: Path,
) -> tuple[Any, dict[str, int]]:
    """Attach local motion only after the moment cut has bounded the reservoirs."""
    candidate_by_id = {candidate.asset_id: candidate for candidate in candidates}
    visual_sources = tuple(
        source for source in prepared.visual_sources if source.asset.id in candidate_by_id
    )
    motion_candidates = tuple(
        candidate for candidate in candidates if candidate.media_kind in {"video", "live_photo"}
    )
    existing_motion_paths = {
        source.asset.id: source.motion_path
        for source in visual_sources
        if source.motion_path is not None
    }
    motion_paths: dict[str, Path] = {}
    if motion_candidates:
        cache = VideoDownloadCache(
            cache_dir=config.cache.video_cache_path,
            max_size_gb=config.cache.video_cache_max_size_gb,
            max_age_days=config.cache.video_cache_max_age_days,
        )
        rendition_fallback_warned: set[str] = set()
        with cache.begin_batch() as batch:
            for candidate in motion_candidates:
                path = _resolve_motion_path(
                    candidate,
                    client=client,
                    batch=batch,
                    cache_dir=config.cache.video_cache_path,
                    existing_motion_paths=existing_motion_paths,
                    warned=rendition_fallback_warned,
                )
                if path is not None:
                    motion_paths[candidate.asset_id] = path
            enriched = tuple(
                replace(
                    source,
                    motion_path=motion_paths.get(source.asset.id) or source.motion_path,
                    proposed_segment=candidate_by_id[source.asset.id].proposed_segment,
                )
                for source in visual_sources
            )
            atlas = build_visual_atlas(enriched, frame_cache_dir=frame_cache_dir)
    else:
        atlas = build_visual_atlas(visual_sources, frame_cache_dir=frame_cache_dir)
    filmstrips = sum(
        atlas.tile_for(candidate.asset_id).kind == "filmstrip" for candidate in motion_candidates
    )
    return atlas, {
        "requested_motion_sources": len(motion_candidates),
        "downloaded_motion_sources": len(motion_paths),
        "filmstrip_sources": filmstrips,
    }


def _observed_motion_candidates(
    candidates: tuple[Any, ...],
    *,
    atlas: Any,
) -> tuple[Any, ...]:
    """Return only bounded motion sources whose temporal pixels were decoded."""
    observed = []
    for candidate in candidates:
        if candidate.media_kind not in {"video", "live_photo"}:
            continue
        try:
            tile = atlas.tile_for(candidate.asset_id)
        except KeyError:
            continue
        if tile.kind == "filmstrip":
            observed.append(candidate)
    return tuple(observed)


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
        "input_assets": workprint.input_assets,
        "admitted_moments": len(workprint.moments),
        "admitted_assets": len(workprint.candidates),
        "retained_asset_ratio": (
            len(workprint.candidates) / workprint.input_assets if workprint.input_assets else 0.0
        ),
        "reason_counts": dict(sorted(reasons.items())),
    }


def _resolved_card_mode(requested: str, workprint: DescriptionWorkprint | None) -> str:
    """Use favourites only when they cover the scope's complete chapter structure."""
    if requested != "auto":
        return requested
    if workprint is None:
        return "model"
    reasons = {
        reason.split(":", 1)[0]
        for admission in workprint.admissions
        for reason in admission.reasons
    }
    if "favourite-evidence" in reasons and "unstarred-chapter" not in reasons:
        return "model"
    return "fused-vision"


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
    llm_config: Any,
    out: Path,
    corpus_selects: bool,
    include_cull_rejected: bool,
    concurrency: int,
) -> Any:
    gateways: list[_ProgressGateway] = []
    expected_tile_counts: dict[str, int] = {}

    def gateway_factory(trace: Any) -> _ProgressGateway:
        gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=llm_config,
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
            concurrency=concurrency,
        )
        pass_one = run_cull(prepared, pass_zero, review_output_dir=out / "cull-review")
        exact_input = prepared.candidates if include_cull_rejected else pass_one.survivors
        # Safe Selects Stage A belongs before paid descriptions even though the
        # model-based sameness stage remains demand-driven at the fine cut.
        exact_selects = run_selects(prepared, exact_input)
        structure_workprint = build_structure_workprint(
            prepared,
            exact_selects.survivors,
            atlas=pass_zero.atlas,
            output_dir=out / "upstream-sheets" / "structure",
        )
        return SimpleNamespace(
            prepared=prepared,
            pass_zero=pass_zero,
            pass_one=pass_one,
            exact_selects=exact_selects,
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
    thinking: bool = False,
) -> TextCall:
    key = judgment_key(model=llm_config.model, prompt=prompt, thinking=thinking)
    cache_hit = JudgmentCache(cache_path).answer_for(key) is not None
    started = time.monotonic()
    raw = await query_llm(
        prompt,
        llm_config,
        temperature=0.0,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        thinking=thinking,
        cache_path=cache_path,
        require_complete=True,
    )
    return TextCall(prompt, raw, time.monotonic() - started, cache_hit, thinking)


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


def _compact_description_card_prompt(
    moment: prototype.Moment,
    *,
    metadata: tuple[str, ...],
) -> str:
    """Retry a truncated text card with the same facts and a tighter answer contract."""
    observations = [
        {
            "context": prototype._observation_context(candidate),
            "description": description.text,
        }
        for candidate, description in zip(
            moment.group.candidates,
            moment.descriptions,
            strict=True,
        )
    ]
    shape = {
        "schema_version": prototype.CARD_SCHEMA,
        "summary": "one-line literal inventory",
    }
    return f"""Retry this factual moment card after an incomplete response.

Condense every distinct visible subject, action, object, setting, and consequential readable detail
from the supplied observations into one literal line under {prototype.MAX_CARD_CHARS} characters.
Collapse only repetition. Use people metadata only as ground truth. Do not rank, select, infer
relationships, or add facts. The decoded summary must contain no double quote or backslash.

GROUND-TRUTH PEOPLE METADATA
{json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))}

OBSERVATIONS
{json.dumps(observations, ensure_ascii=False, separators=(",", ":"))}

Return exactly one complete JSON object:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {prototype.CARD_SCHEMA}."""


def _fallback_description_card(moment: prototype.Moment) -> str:
    """Keep only banked literal evidence when two card envelopes are incomplete."""
    summary = " ; ".join(description.text for description in moment.descriptions)
    summary = " ".join(summary.split()).replace('"', "”").replace("\\", "∖")
    if len(summary) > prototype.MAX_CARD_CHARS:
        summary = summary[: prototype.MAX_CARD_CHARS - 1].rstrip() + "…"
    return summary or "No usable literal description was available for this moment."


async def _build_cards(
    moments: tuple[prototype.Moment, ...],
    *,
    facts: dict[str, PersonFact],
    llm_config: Any,
    cache_path: Path,
    concurrency: int,
    timeout_seconds: int,
    card_mode: str,
) -> tuple[tuple[prototype.MomentCard, ...], tuple[TextCall | None, ...]]:
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    started = time.monotonic()

    async def build(moment: prototype.Moment) -> tuple[prototype.MomentCard, TextCall | None]:
        nonlocal completed
        if card_mode == "template-single" and len(moment.descriptions) == 1:
            card = prototype.MomentCard(moment, moment.descriptions[0].text, None)
            call = None
        else:
            metadata = _moment_people(moment, facts)
            prompt = prototype._summary_prompt(moment)
            if metadata:
                block = "\n".join(f"- {line}" for line in metadata)
                prompt = prompt.replace(
                    "\nOBSERVATIONS\n",
                    "\nGROUND-TRUTH PEOPLE METADATA\n" + block + "\n\nOBSERVATIONS\n",
                )
            try:
                async with semaphore:
                    call = await _ask_text(
                        prompt,
                        llm_config=llm_config,
                        cache_path=cache_path,
                        max_tokens=900,
                        timeout_seconds=timeout_seconds,
                    )
                summary = prototype._card_summary(call.raw)
            except (OSError, RuntimeError, TimeoutError, ValueError) as first_error:
                warning = f"initial description card failed: {type(first_error).__name__}"
                retry_prompt = _compact_description_card_prompt(moment, metadata=metadata)
                try:
                    async with semaphore:
                        retry = await _ask_text(
                            retry_prompt,
                            llm_config=llm_config,
                            cache_path=cache_path,
                            max_tokens=1200,
                            timeout_seconds=timeout_seconds,
                        )
                    summary = prototype._card_summary(retry.raw)
                    call = replace(retry, warning=warning)
                except (OSError, RuntimeError, TimeoutError, ValueError) as retry_error:
                    summary = _fallback_description_card(moment)
                    raw = json.dumps(
                        {"schema_version": prototype.CARD_SCHEMA, "summary": summary},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    call = TextCall(
                        prompt=prompt,
                        raw=raw,
                        wall_seconds=0.0,
                        cache_hit=False,
                        thinking=False,
                        warning=(
                            f"{warning}; compact retry failed: {type(retry_error).__name__}; "
                            "used deterministic description fallback"
                        ),
                    )
            card = prototype.MomentCard(moment, summary, None)

        completed += 1
        if completed == 1 or completed % 50 == 0 or completed == len(moments):
            elapsed = time.monotonic() - started
            eta = elapsed * (len(moments) - completed) / completed
            print(
                f"moment-cards: {completed}/{len(moments)}, "
                f"elapsed {_progress_duration(elapsed)}, ETA {_progress_duration(eta)}",
                flush=True,
            )
        return card, call

    pairs = await asyncio.gather(*(build(moment) for moment in moments))
    return tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs)


def _fused_card_prompt(
    moment: prototype.Moment,
    *,
    facts: dict[str, PersonFact],
) -> str:
    metadata = {
        "moment_id": moment.alias,
        "visual_count": len(moment.group.candidates),
        "favourite_count": sum(candidate.favourite for candidate in moment.group.candidates),
        "grounded_context": list(prototype._moment_context(moment)),
        "people": list(_moment_people(moment, facts)),
    }
    return f"""Build one compact factual card from every attached 400px visual.

The visuals are one production-grouped moment, attached in chronological order. Inspect every
visual. Preserve every distinct visible subject, action, object, setting, and readable consequential
detail, even when it appears in only one frame and the other frames repeat a common scene. Ground
recognized people and places in the supplied metadata when relevant. A name says who Immich
recognized, not their relationship; do not invent one. Collapse only genuine repetition. Do not
rank, score, select, interpret significance, infer relationships, or invent context. Use one line
without double quotes or backslashes.

DETERMINISTIC MOMENT METADATA
{json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))}

Return only one complete JSON object with exactly these keys:
{json.dumps(HEDGED_CARD_SHAPE, separators=(",", ":"))}
{HEDGED_CARD_SENTENCE}
The schema_version value must be exactly {prototype.CARD_SCHEMA}; do not shorten or paraphrase it."""


def _compact_fused_card_retry_prompt(
    moment: prototype.Moment,
    *,
    facts: dict[str, PersonFact],
) -> str:
    metadata = {
        "moment_id": moment.alias,
        "visual_count": len(moment.group.candidates),
        "favourite_count": sum(candidate.favourite for candidate in moment.group.candidates),
        "grounded_context": list(prototype._moment_context(moment)),
        "people": list(_moment_people(moment, facts)),
    }
    return f"""Retry this moment card after an incomplete response. Inspect every attached 400px
visual, in chronological order. Write one literal inventory of all distinct visible subjects,
actions, objects, settings, and consequential readable details. Collapse repetition. Use supplied
people metadata only as ground truth. Do not rank, select, infer relationships, or add prose.
Keep summary under 500 characters.

METADATA
{json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))}

Return exactly one complete JSON object:
{{"schema_version":"{prototype.CARD_SCHEMA}","summary":"one-line literal inventory"}}"""


def _fallback_fused_card(moment: prototype.Moment, *, facts: dict[str, PersonFact]) -> str:
    first = moment.group.candidates[0].taken_at.isoformat()
    last = moment.group.candidates[-1].taken_at.isoformat()
    metadata = (
        " Grounded people metadata remains attached separately."
        if _moment_people(moment, facts)
        else ""
    )
    return (
        f"Visual contents unavailable after two incomplete card calls; "
        f"{len(moment.group.candidates)} chronological visuals from {first} to {last}; "
        f"do not infer their contents.{metadata}"
    )


def _fused_card_provenance(request: Any) -> dict[str, Any]:
    """Record everything the wire prompt was built from, so a banked card can be replayed."""
    annotations = [str(annotation) for annotation in request.grounded_annotations]
    digest = hashlib.sha256("\x00".join((request.prompt, *annotations)).encode("utf-8"))
    return {
        "prompt_sha256": digest.hexdigest(),
        "pass_version": request.pass_version,
        "prompt_version": request.prompt_version,
        "render_version": request.render_version,
        "schema_version": request.schema_version,
        "grounded_annotations": annotations,
    }


def _fused_card_summary(raw: str) -> tuple[str, str | None]:
    """Read a card answer in either the hedged or the compact envelope."""
    payload = final_json_object(raw)
    if payload is None or set(payload) not in (set(HEDGED_CARD_SHAPE), set(COMPACT_CARD_KEYS)):
        raise ValueError("moment card answer is not an exact card envelope")
    version = payload.get("schema_version")
    warning = None
    if version == RETIRED_CARD_SCHEMA:
        warning = f"card answer echoed the retired schema version {RETIRED_CARD_SCHEMA}"
    elif version != prototype.CARD_SCHEMA:
        raise ValueError("moment card answer has the wrong schema version")
    summary = bounded_model_text(payload.get("summary"), max_chars=prototype.MAX_CARD_CHARS)
    if summary is None:
        raise ValueError("moment card summary is not safe bounded text")
    for key in sorted(set(payload) - set(COMPACT_CARD_KEYS)):
        if bounded_model_text(payload[key], max_chars=300) is None:
            raise ValueError(f"moment card {key} is not safe bounded text")
    return summary, warning


def _mechanical_fused_card_repair(raw: str) -> tuple[str, str] | None:
    """Project a literal summary into the exact card envelope without another model call."""
    payload = final_json_object(raw)
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    if not isinstance(summary, str):
        return None
    normalized = (
        summary.strip()[: prototype.MAX_CARD_CHARS].strip().replace('"', "”").replace("\\", "∖")
    )
    repaired = json.dumps(
        {"schema_version": prototype.CARD_SCHEMA, "summary": normalized},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        return repaired, _fused_card_summary(repaired)[0]
    except ValueError:
        return None


async def _build_fused_cards(
    groups: tuple[EditorialGroup, ...],
    *,
    facts: dict[str, PersonFact],
    atlas: Any,
    requester: Any,
    output_dir: Path,
    llm_config: Any,
    cache_path: Path,
    concurrency: int,
    timeout_seconds: int,
) -> tuple[tuple[prototype.MomentCard, ...], tuple[TextCall, ...]]:
    """Read every 400px visual once per moment, without prior asset descriptions."""
    semaphore = asyncio.Semaphore(concurrency)

    async def build(index: int, group: EditorialGroup) -> tuple[prototype.MomentCard, TextCall]:
        moment = prototype.Moment(alias=f"M{index:03d}", group=group, descriptions=())
        prompt = _fused_card_prompt(moment, facts=facts)
        pages = build_contact_sheets(
            tuple(atlas.tile_for(candidate.asset_id) for candidate in group.candidates),
            scope_id=f"fused-moment-{index:03d}",
            output_dir=output_dir,
            tile_px=400,
        )
        request = VisualEditorialRequest(
            pass_name="fused-moment-card",  # noqa: S106 - prototype pass identity
            pass_version=FUSED_CARD_PASS_VERSION,
            prompt=prompt,
            prompt_version=FUSED_CARD_PROMPT_VERSION,
            schema_version=prototype.CARD_SCHEMA,
            pages=pages,
            ordered_input_ids=group.candidate_ids,
            ordered_group_ids=(group.group_id,),
            grounded_annotations=tuple(
                annotation
                for candidate in group.candidates
                for annotation in candidate.grounded_annotations
            ),
            upstream_material=(),
            render_version=FUSED_CARD_RENDER_VERSION,
            limits=VisionRequestLimits(
                max_pages_per_request=max(1, len(pages)),
                max_output_tokens=900,
                timeout_seconds=timeout_seconds,
            ),
            image_detail="high",
        )
        started = time.monotonic()
        warning = None
        try:
            async with semaphore:
                answer = await asyncio.to_thread(requester.ask, request)
        except (OSError, RuntimeError, TimeoutError, ValueError) as first_error:
            warning = f"initial fused card call failed: {type(first_error).__name__}"
            retry_request = replace(
                request,
                pass_version=FUSED_CARD_RETRY_PASS_VERSION,
                prompt=_compact_fused_card_retry_prompt(moment, facts=facts),
                prompt_version=FUSED_CARD_RETRY_PROMPT_VERSION,
                limits=replace(request.limits, max_output_tokens=1200),
            )
            try:
                async with semaphore:
                    answer = await asyncio.to_thread(requester.ask, retry_request)
            except (OSError, RuntimeError, TimeoutError, ValueError) as retry_error:
                summary = _fallback_fused_card(moment, facts=facts)
                raw = json.dumps(
                    {"schema_version": prototype.CARD_SCHEMA, "summary": summary},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                call = TextCall(
                    prompt=prompt,
                    raw=raw,
                    wall_seconds=time.monotonic() - started,
                    cache_hit=False,
                    thinking=False,
                    warning=(
                        f"{warning}; compact retry failed: {type(retry_error).__name__}; "
                        "used metadata-only fallback"
                    ),
                    prompt_provenance=_fused_card_provenance(retry_request),
                )
                return prototype.MomentCard(moment, summary, None), call
        raw = answer.raw_text
        repair_cache_hit = True
        try:
            summary, schema_echo = _fused_card_summary(raw)
            warning = (
                f"{warning}; {schema_echo}" if warning and schema_echo else (schema_echo or warning)
            )
        except ValueError as parse_error:
            repair_warning = f"fused card content needed repair: {type(parse_error).__name__}"
            warning = f"{warning}; {repair_warning}" if warning else repair_warning
            mechanical = _mechanical_fused_card_repair(raw)
            if mechanical is not None:
                raw, summary = mechanical
                warning = f"{warning}; repaired schema/display punctuation mechanically"
            else:
                try:
                    async with semaphore:
                        repair = await _ask_text(
                            f"""Repair the bounded JSON card below.

Return exactly one JSON object with exactly the keys schema_version and summary. Set
schema_version to the exact literal {prototype.CARD_SCHEMA}. Keep every distinct factual subject,
action, object, setting, and consequential readable detail already present, but condense the
summary below 500 characters when it is longer. The decoded summary must be one line and contain
no double quote or backslash characters; paraphrase quoted text without those characters. Do not
add facts or prose outside the object.

INPUT
{raw}""",
                            llm_config=llm_config,
                            cache_path=cache_path,
                            max_tokens=900,
                            timeout_seconds=timeout_seconds,
                        )
                    raw = repair.raw
                    repair_cache_hit = repair.cache_hit
                    summary, _echo = _fused_card_summary(raw)
                except (OSError, RuntimeError, TimeoutError, ValueError) as repair_error:
                    summary = _fallback_fused_card(moment, facts=facts)
                    raw = json.dumps(
                        {"schema_version": prototype.CARD_SCHEMA, "summary": summary},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    repair_cache_hit = False
                    warning = (
                        f"{warning}; bounded repair failed: {type(repair_error).__name__}; "
                        "used metadata-only fallback"
                    )
        call = TextCall(
            prompt=prompt,
            raw=raw,
            wall_seconds=time.monotonic() - started,
            cache_hit=answer.provenance.cache_hit and repair_cache_hit,
            thinking=False,
            warning=warning,
            prompt_provenance=_fused_card_provenance(request),
        )
        return prototype.MomentCard(moment, summary, None), call

    pairs = await asyncio.gather(
        *(build(index, group) for index, group in enumerate(groups, start=1))
    )
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


def _editorial_wall_chars(
    cards: tuple[prototype.MomentCard, ...],
    *,
    facts: dict[str, PersonFact],
) -> int:
    """Measure the exact enriched card lines that dominate editorial context."""
    if not cards:
        return 0
    return sum(len(_enriched_line(card, facts)) for card in cards) + len(cards) - 1


def _use_flat_editorial_wall(
    cards: tuple[prototype.MomentCard, ...],
    *,
    facts: dict[str, PersonFact],
) -> bool:
    """Keep a flat read only while both count and serialized context are bounded."""
    return (
        len(cards) <= FLAT_WALL_MAX_CARDS
        and _editorial_wall_chars(cards, facts=facts) <= EDITORIAL_WALL_MAX_CHARS
    )


def _chapter_parts(
    cards: list[prototype.MomentCard],
    *,
    facts: dict[str, PersonFact],
) -> tuple[tuple[prototype.MomentCard, ...], ...]:
    """Split one chronological chapter under the same limits as a flat wall."""
    parts: list[tuple[prototype.MomentCard, ...]] = []
    current: list[prototype.MomentCard] = []
    current_chars = 0
    for card in cards:
        line_chars = len(_enriched_line(card, facts))
        proposed_chars = current_chars + (1 if current else 0) + line_chars
        if current and (
            len(current) >= CHAPTER_MAX_CARDS or proposed_chars > EDITORIAL_WALL_MAX_CHARS
        ):
            parts.append(tuple(current))
            current = []
            current_chars = 0
        current_chars += (1 if current else 0) + line_chars
        current.append(card)
    if current:
        parts.append(tuple(current))
    return tuple(parts)


def _chapters(
    case: Case,
    cards: tuple[prototype.MomentCard, ...],
    *,
    facts: dict[str, PersonFact],
) -> tuple[Chapter, ...]:
    grouped: dict[tuple[Any, ...], list[prototype.MomentCard]] = defaultdict(list)
    for card in cards:
        grouped[_chapter_key(case, card)].append(card)
    chapters: list[Chapter] = []
    for key, members in grouped.items():
        parts = _chapter_parts(members, facts=facts)
        for part_number, part in enumerate(parts, start=1):
            suffix = f" part {part_number}/{len(parts)}" if len(parts) > 1 else ""
            chapters.append(
                Chapter(
                    chapter_id=f"C{len(chapters) + 1:03d}",
                    label=_chapter_label(case, key) + suffix,
                    cards=part,
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
    thinking: bool,
    require_sustained: bool = True,
) -> tuple[dict[str, Any], TextCall]:
    requirements = _lifecycle_requirements(cards, facts)
    prompt = _enrich_wall_prompt(
        prototype._thesis_prompt(cards, case.product), cards, case=case, facts=facts
    )
    if requirements:
        prompt = prompt.replace(
            "\nMOMENT WALL\n",
            "\nREQUIRED RECORDED LIFECYCLE FACTS\n"
            + _lifecycle_block(requirements)
            + "\nThese facts are deterministic annotations, not optional interpretations. "
            "Integrate each one into the thesis and turning points. The runtime preserves its "
            "anchor independently of your prose.\n\nMOMENT WALL\n",
        )
    call = await _ask_text(
        prompt,
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=1800,
        timeout_seconds=timeout_seconds,
        thinking=thinking,
    )
    aliases = frozenset(card.moment.alias for card in cards)
    return _with_lifecycle_turning_points(
        prototype._read_thesis(
            call.raw,
            aliases,
            require_sustained=require_sustained,
        ),
        requirements,
    ), call


def _global_thesis_prompt(
    case: Case,
    chapter_readings: tuple[tuple[Chapter, dict[str, Any]], ...],
    requirements: tuple[LifecycleRequirement, ...],
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
the supplied facts and cite original moment IDs as evidence. Return the grounded threads and
turning points the evidence actually supports; do not fill a quota or stop at an arbitrary count.

REQUIRED RECORDED LIFECYCLE FACTS
{_lifecycle_block(requirements)}
These facts are deterministic annotations and must remain explicit in the global reading.

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
    thinking: bool,
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
                thinking=False,
                require_sustained=False,
            )
        return chapter, thesis, call

    rows = await asyncio.gather(*(read(chapter) for chapter in chapters))
    readings = tuple((chapter, thesis) for chapter, thesis, _call in rows)
    all_cards = tuple(card for chapter in chapters for card in chapter.cards)
    requirements = _lifecycle_requirements(all_cards, facts)
    call = await _ask_text(
        _global_thesis_prompt(case, readings, requirements),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=2200,
        timeout_seconds=timeout_seconds,
        thinking=thinking,
    )
    aliases = frozenset(card.moment.alias for chapter in chapters for card in chapter.cards)
    thesis = _with_lifecycle_turning_points(prototype._read_thesis(call.raw, aliases), requirements)
    return thesis, readings, (*tuple(row[2] for row in rows), call)


def _selection_prompt(
    cards: tuple[prototype.MomentCard, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    capacity: int,
) -> str:
    requirements = _lifecycle_requirements(cards, facts)
    required_ids = tuple(dict.fromkeys(item.anchor_id for item in requirements))
    prompt = prototype._selection_prompt(
        cards,
        memory_type=case.product,
        thesis=thesis,
        capacity=capacity,
        required_ids=required_ids,
    )
    if required_ids:
        prompt = prompt.replace(
            "The owner has already admitted these favourite-bearing moments:",
            "The runtime has already admitted these graph-grounded lifecycle anchors:",
        )
    law = (
        "Favourite counts are direct owner evidence, not automatic admission of every whole "
        "moment. Runtime may omit a favourite-bearing moment. If such a moment is retained, its "
        "later representative must be a favourite rather than an unstarred neighbour.\n\n"
        "An expressive portrait does not substitute for the only visible carrier of the event "
        "action, place, route, or atmosphere.\n\n"
    )
    prompt = prompt.replace(
        "\nUse the thesis as editorial orientation",
        "\n" + law + "Use the thesis as editorial orientation",
    )
    lifecycle_law = ""
    if requirements:
        lifecycle_law = (
            "REQUIRED RECORDED LIFECYCLE FACTS\n"
            + _lifecycle_block(requirements)
            + "\nThe listed runtime anchors are already in the cut and consume capacity. Do not "
            "return or reject them. Build the rest of the sequence around them.\n\n"
        )
    prompt = prompt.replace(
        "\nMOMENT WALL\n",
        "\n"
        + lifecycle_law
        + "Before answering, audit the tentative cut: for every choice whose value is only "
        "explanatory evidence, setup, or inventory, identify the lived moment it beats. If it "
        "beats none, drop it; if a lived alternative carries the same thread, substitute that "
        "alternative.\n\nMOMENT WALL\n",
    )
    return _enrich_wall_prompt(prompt, cards, case=case, facts=facts)


def _local_thesis(
    thesis: dict[str, Any],
    cards: tuple[prototype.MomentCard, ...],
    *,
    reading: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ground the wall in its own chapter reading when one exists; IDs stay local either way."""
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

    if reading is None:
        return {
            **thesis,
            "sustained_threads": localize(thesis.get("sustained_threads")),
            "turning_points": localize(thesis.get("turning_points")),
        }
    return {
        **reading,
        "sustained_threads": localize(reading.get("sustained_threads")),
        "turning_points": localize(reading.get("turning_points")),
        "global_context": thesis.get("thesis"),
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
    thinking: bool,
    reading: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[TextCall, ...]]:
    requirements = _lifecycle_requirements(cards, facts)
    required_ids = tuple(dict.fromkeys(item.anchor_id for item in requirements))
    if len(required_ids) > capacity:
        raise ValueError(
            f"{len(required_ids)} lifecycle anchors exceed the {capacity}-moment capacity"
        )
    if len(required_ids) == capacity:
        return _admit_lifecycle_anchors(
            {
                "keep": [],
                "audit_summary": "Lifecycle anchors consume the available moment capacity.",
                "comparisons": [],
                "overall_reason": "The hard lifecycle record fills this cut.",
            },
            required_ids=required_ids,
            cards=cards,
            requirements=requirements,
        ), ()
    prompt = _selection_prompt(cards, case=case, facts=facts, thesis=thesis, capacity=capacity)
    call = await _ask_text(
        prompt,
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=4000,
        timeout_seconds=timeout_seconds,
        thinking=thinking,
    )
    aliases = frozenset(card.moment.alias for card in cards)
    calls = [call]
    try:
        additional = _read_selection_with_comparison_repair(
            call.raw,
            aliases,
            capacity - len(required_ids),
            excluded_ids=frozenset(required_ids),
        )
    except ValueError as exc:
        if str(exc) not in {
            "moment selection audit has the wrong shape",
            "moment selection comparison has the wrong shape",
            "moment selection comparison is not grounded",
            "moment selection exceeds capacity or has the wrong shape",
        }:
            raise
        repair = await _ask_text(
            _selection_audit_repair_prompt(
                prompt,
                call.raw,
                str(exc),
                max_keep=capacity - len(required_ids),
            ),
            llm_config=llm_config,
            cache_path=cache_path,
            max_tokens=4000,
            timeout_seconds=timeout_seconds,
            thinking=False,
        )
        calls.append(repair)
        additional = _read_selection_with_comparison_repair(
            _normalize_selection_repair_envelope(repair.raw),
            aliases,
            capacity - len(required_ids),
            excluded_ids=frozenset(required_ids),
        )
    protected_ids = _reading_evidence_ids(reading)
    capped = _apply_occasion_cap(
        _admit_lifecycle_anchors(
            additional,
            required_ids=required_ids,
            cards=cards,
            requirements=requirements,
        ),
        cards,
        protected_ids=protected_ids,
    )
    reallocated, reallocation_calls = await _reallocate_freed_slots(
        capped,
        cards,
        case=case,
        facts=facts,
        thesis=thesis,
        llm_config=llm_config,
        cache_path=cache_path,
        timeout_seconds=timeout_seconds,
        protected_ids=protected_ids,
    )
    return _with_rejected_trace(reallocated, cards), (*calls, *reallocation_calls)


async def _reallocate_freed_slots(
    selection: dict[str, Any],
    cards: tuple[prototype.MomentCard, ...],
    *,
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
    protected_ids: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], tuple[TextCall, ...]]:
    """Offer the capped slots back to the chapter's starved occasions in one bounded round."""
    cap = selection.get("occasion_cap") or {}
    freed = cap.get("freed_slots", 0)
    if not freed or not cap.get("starved_occasions"):
        return selection, ()
    aliases = frozenset(card.moment.alias for card in cards)
    # Only previously rejected moments of other occasions remain choosable: the
    # round may not re-admit what the cap just dropped, or re-list a keep.
    excluded = frozenset(
        {row["moment_id"] for row in selection["keep"]} | set(cap["removed_moment_ids"])
    )
    if not aliases - excluded:
        return selection, ()
    call = await _ask_text(
        _selection_prompt(cards, case=case, facts=facts, thesis=thesis, capacity=freed),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=4000,
        timeout_seconds=timeout_seconds,
        thinking=False,
    )
    error: str | None = None
    try:
        round_keep = _read_selection_with_comparison_repair(
            call.raw, aliases, freed, excluded_ids=excluded
        )["keep"]
    except ValueError as exc:
        round_keep, error = [], str(exc)
    reallocation: dict[str, Any] = {
        "slots": freed,
        "keep": round_keep,
        "removed_moment_ids": [],
        **({"error": error} if error else {}),
    }
    if not round_keep:
        return {**selection, "occasion_cap": {**cap, "reallocation": reallocation}}, (call,)
    rows = {row["moment_id"]: row for row in (*selection["keep"], *round_keep)}
    merged = _apply_occasion_cap(
        {
            **selection,
            "keep": [rows[card.moment.alias] for card in cards if card.moment.alias in rows],
        },
        cards,
        protected_ids=protected_ids,
    )
    reallocation["removed_moment_ids"] = merged["occasion_cap"]["removed_moment_ids"]
    return {**merged, "occasion_cap": {**cap, "reallocation": reallocation}}, (call,)


def _normalize_selection_repair_envelope(raw: str) -> str:
    """Restore fixed syntax without changing a repaired cut's editorial content."""
    payload = final_json_object(raw)
    expected_without_version = {"keep", "audit_summary", "comparisons", "overall_reason"}
    if not isinstance(payload, dict) or set(payload) != expected_without_version:
        return raw
    audit = payload.get("audit_summary")
    if isinstance(audit, dict) and set(audit) == {"main_tradeoff"}:
        payload = {**payload, "audit_summary": audit["main_tradeoff"]}
    return json.dumps(
        {"schema_version": prototype.SELECTION_SCHEMA, **payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _selection_audit_repair_prompt(
    original_prompt: str,
    raw: str,
    error: str,
    *,
    max_keep: int,
) -> str:
    payload = final_json_object(raw)
    previous_keep = payload.get("keep") if isinstance(payload, dict) else None
    previous_count = len(previous_keep) if isinstance(previous_keep, list) else None
    capacity_correction = (
        f"Your previous keep has {previous_count} rows. Return at most {max_keep} keep rows; "
        f"remove at least {max(0, previous_count - max_keep)} through explicit comparison."
        if previous_count is not None
        else f"Return at most {max_keep} keep rows."
    )
    return f"""{original_prompt}

Your previous JSON failed validation: {error}.
{capacity_correction}

PREVIOUS JSON
{raw}

Return one corrected complete JSON object in the exact requested schema. Preserve the grounded keep
list unless satisfying the schema requires changing it. Every kept_moment_id in comparisons must be
in keep. Every rejected_moment_id must be a different moment from the wall that is not in keep. If
keep is empty, comparisons must be empty. Do not add commentary outside the JSON."""


def _admit_lifecycle_anchors(
    selection: dict[str, Any],
    *,
    required_ids: tuple[str, ...],
    cards: tuple[prototype.MomentCard, ...],
    requirements: tuple[LifecycleRequirement, ...],
) -> dict[str, Any]:
    if not required_ids:
        return selection
    reason_by_id: dict[str, list[str]] = defaultdict(list)
    for item in requirements:
        reason_by_id[item.anchor_id].append(f"Preserves {item.person_name}'s {item.fact}.")
    selected_by_id = {row["moment_id"]: row for row in selection["keep"]}
    selected_by_id.update(
        {
            moment_id: {
                "moment_id": moment_id,
                "reason": " ".join(reason_by_id[moment_id]),
            }
            for moment_id in required_ids
        }
    )
    return {
        **selection,
        "keep": [
            selected_by_id[card.moment.alias]
            for card in cards
            if card.moment.alias in selected_by_id
        ],
        "lifecycle_anchor_ids": list(required_ids),
    }


def _occasion_id(card: prototype.MomentCard) -> str:
    return card.moment.group.candidates[0].taken_at.date().isoformat()


def _reading_evidence_ids(reading: dict[str, Any] | None) -> frozenset[str]:
    """Name the moments a chapter reading argued from, so the cap cannot delete its own evidence."""
    if not isinstance(reading, dict):
        return frozenset()
    return frozenset(
        moment_id
        for lane in ("sustained_threads", "turning_points")
        for row in reading.get(lane) or ()
        if isinstance(row, dict)
        for moment_id in row.get("evidence_moment_ids") or ()
        if isinstance(moment_id, str)
    )


def _apply_occasion_cap(
    selection: dict[str, Any],
    cards: tuple[prototype.MomentCard, ...],
    *,
    max_per_occasion: int = OCCASION_MOMENT_CAP,
    protected_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Cap unstarred keeps per occasion and surface the freed slots without refilling them."""
    card_by_id = {card.moment.alias: card for card in cards}
    keep_ids = [
        row["moment_id"] for row in selection["keep"] if row["moment_id"] in card_by_id
    ]
    exempt = (
        set(selection.get("lifecycle_anchor_ids", ()))
        | set(protected_ids)
        | {
            alias
            for alias, card in card_by_id.items()
            if any(candidate.favourite for candidate in card.moment.group.candidates)
        }
    )
    # The cut's own comparisons are the only strength evidence the model produced;
    # keep order (already repaired to chronology) breaks ties, so an occasion keeps
    # its opening beat and sheds the later repeat.
    defended = Counter(
        row["kept_moment_id"]
        for row in selection.get("comparisons", ())
        if isinstance(row, dict) and isinstance(row.get("kept_moment_id"), str)
    )
    by_occasion: dict[str, list[str]] = defaultdict(list)
    for moment_id in keep_ids:
        if moment_id not in exempt:
            by_occasion[_occasion_id(card_by_id[moment_id])].append(moment_id)
    dropped = {
        moment_id
        for moment_ids in by_occasion.values()
        for moment_id in sorted(moment_ids, key=lambda one: -defended[one])[max_per_occasion:]
    }
    kept_per_occasion = Counter(
        _occasion_id(card_by_id[moment_id]) for moment_id in keep_ids if moment_id not in dropped
    )
    dropped_per_occasion = Counter(_occasion_id(card_by_id[moment_id]) for moment_id in dropped)
    rejected_per_occasion = Counter(
        _occasion_id(card) for alias, card in card_by_id.items() if alias not in set(keep_ids)
    )
    return {
        **selection,
        "keep": [row for row in selection["keep"] if row["moment_id"] not in dropped],
        "occasion_cap": {
            "max_per_occasion": max_per_occasion,
            "freed_slots": len(dropped),
            "removed_moment_ids": [
                moment_id for moment_id in keep_ids if moment_id in dropped
            ],
            "capped_occasions": [
                {
                    "occasion_id": occasion_id,
                    "kept_moments": kept_per_occasion[occasion_id],
                    "removed_moments": dropped_per_occasion[occasion_id],
                }
                for occasion_id in sorted(dropped_per_occasion)
            ],
            "starved_occasions": [
                {
                    "occasion_id": occasion_id,
                    "kept_moments": kept_per_occasion[occasion_id],
                    "rejected_moments": rejected_per_occasion[occasion_id],
                }
                for occasion_id in sorted(rejected_per_occasion)
                if occasion_id not in dropped_per_occasion
            ],
        },
    }


def _with_rejected_trace(
    selection: dict[str, Any],
    cards: tuple[prototype.MomentCard, ...],
) -> dict[str, Any]:
    """Record what the cut dropped, joined to the comparison that argued against it."""
    reasons = {
        row["rejected_moment_id"]: row["reason"]
        for row in selection.get("comparisons", ())
        if isinstance(row, dict) and isinstance(row.get("rejected_moment_id"), str)
    }
    kept = {row["moment_id"] for row in selection["keep"]}
    return {
        **selection,
        "rejected": [
            {"moment_id": card.moment.alias, "reason": reasons.get(card.moment.alias)}
            for card in cards
            if card.moment.alias not in kept
        ],
    }


def _read_selection_with_comparison_repair(
    raw: str,
    valid_ids: frozenset[str],
    capacity: int,
    *,
    excluded_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Reject bad audit rows without discarding an otherwise grounded cut."""
    payload = final_json_object(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("comparisons"), list):
        return prototype._read_selection(raw, valid_ids, capacity, excluded_ids=excluded_ids)
    raw_keep = payload.get("keep")
    if not isinstance(raw_keep, list):
        return prototype._read_selection(raw, valid_ids, capacity, excluded_ids=excluded_ids)
    runtime_anchor_rows = [
        row
        for row in raw_keep
        if isinstance(row, dict)
        and set(row) == {"moment_id", "reason"}
        and row.get("moment_id") in excluded_ids
        and bounded_model_text(row.get("reason"), max_chars=prototype.MAX_REASON_CHARS) is not None
    ]
    if runtime_anchor_rows:
        raw_keep = [row for row in raw_keep if row not in runtime_anchor_rows]
        payload = {**payload, "keep": raw_keep}
    keep_order = {moment_id: index for index, moment_id in enumerate(sorted(valid_ids))}
    raw_keep_ids = [row.get("moment_id") if isinstance(row, dict) else None for row in raw_keep]
    can_sort_keep = (
        all(isinstance(moment_id, str) and moment_id in keep_order for moment_id in raw_keep_ids)
        and len(set(raw_keep_ids)) == len(raw_keep_ids)
        and not (set(raw_keep_ids) & excluded_ids)
    )
    keep_reordered = False
    if can_sort_keep:
        ordered_keep = sorted(raw_keep, key=lambda row: keep_order[row["moment_id"]])
        keep_reordered = ordered_keep != raw_keep
        if keep_reordered:
            payload = {**payload, "keep": ordered_keep}
            raw_keep = ordered_keep
    keep_ids = {
        row.get("moment_id")
        for row in raw_keep
        if isinstance(row, dict) and isinstance(row.get("moment_id"), str)
    }
    comparisons = []
    for row in payload["comparisons"]:
        if not isinstance(row, dict) or set(row) != {
            "kept_moment_id",
            "rejected_moment_id",
            "reason",
        }:
            continue
        kept_id = row.get("kept_moment_id")
        rejected_id = row.get("rejected_moment_id")
        reason = bounded_model_text(row.get("reason"), max_chars=prototype.MAX_REASON_CHARS)
        if (
            kept_id not in keep_ids
            or rejected_id not in valid_ids
            or rejected_id in keep_ids
            or rejected_id in excluded_ids
            or rejected_id == kept_id
            or reason is None
        ):
            continue
        comparisons.append(
            {
                "kept_moment_id": kept_id,
                "rejected_moment_id": rejected_id,
                "reason": reason,
            }
        )
    discarded = len(payload["comparisons"]) - len(comparisons)
    if discarded == 0 and not keep_reordered and not runtime_anchor_rows:
        return prototype._read_selection(raw, valid_ids, capacity, excluded_ids=excluded_ids)
    repaired = dict(payload)
    repaired["comparisons"] = comparisons
    parsed = prototype._read_selection(
        json.dumps(repaired, ensure_ascii=False, separators=(",", ":")),
        valid_ids,
        capacity,
        excluded_ids=excluded_ids,
    )
    repairs = {
        **({"discarded_comparisons": discarded} if discarded else {}),
        **(
            {"discarded_runtime_anchor_rows": len(runtime_anchor_rows)}
            if runtime_anchor_rows
            else {}
        ),
        **({"chronological_keep_repair": True} if keep_reordered else {}),
    }
    return {**parsed, **repairs}


def _allocation_prompt(
    case: Case,
    thesis: dict[str, Any],
    readings: tuple[tuple[Chapter, dict[str, Any]], ...],
    capacity: int,
    minimum_slots: dict[str, int],
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
Before returning JSON, add every `slots` value yourself. If their sum exceeds {capacity}, reduce
the weakest allocation until the verified total is at most {capacity}.

RUNTIME MINIMUMS
{json.dumps(minimum_slots, separators=(",", ":"))}
These minima are already consumed by graph-grounded lifecycle anchors. Every listed chapter must
receive at least its stated number of slots. Distribute only the remaining scarcity editorially.

EDITORIAL BRIEF
{case.brief}

GLOBAL THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

CHAPTERS
{json.dumps(chapters, ensure_ascii=False, separators=(",", ":"))}

Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}"""


def _read_allocation(
    raw: str,
    chapters: tuple[Chapter, ...],
    capacity: int,
    minimum_slots: dict[str, int],
) -> dict[str, Any]:
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
    requested_slots = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"chapter_id", "slots", "reason"}:
            raise ValueError("chapter allocation row has the wrong shape")
        chapter_id, slots = row.get("chapter_id"), row.get("slots")
        reason = bounded_model_text(row.get("reason"), max_chars=400)
        if chapter_id not in by_id or not isinstance(slots, int) or isinstance(slots, bool):
            raise ValueError("chapter allocation row is not grounded")
        if slots < 0 or reason is None:
            raise ValueError("chapter allocation exceeds its chapter")
        requested_slots.append(slots)
        available_slots = min(slots, len(by_id[chapter_id].cards))
        if available_slots < minimum_slots.get(chapter_id, 0):
            raise ValueError("chapter allocation drops a required lifecycle anchor")
        parsed.append({"chapter_id": chapter_id, "slots": available_slots, "reason": reason})
    expected = [chapter.chapter_id for chapter in chapters]
    if [row["chapter_id"] for row in parsed] != expected:
        raise ValueError("chapter allocation is unordered")
    model_total = sum(requested_slots)
    source_total = sum(row["slots"] for row in parsed)
    if source_total > capacity:
        current = [minimum_slots.get(chapter.chapter_id, 0) for chapter in chapters]
        desired = [capacity * row["slots"] / source_total for row in parsed]
        remaining = capacity - sum(current)
        while remaining:
            eligible = [index for index, row in enumerate(parsed) if current[index] < row["slots"]]
            if not eligible:
                raise ValueError("chapter allocation cannot be scaled to capacity")
            winner = max(
                eligible,
                key=lambda index: (desired[index] - current[index], -index),
            )
            current[winner] += 1
            remaining -= 1
        parsed = [{**row, "slots": slots} for row, slots in zip(parsed, current, strict=True)]
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=500)
    if overall is None:
        raise ValueError("chapter allocation overall reason is unsafe")
    return {
        "allocations": parsed,
        "overall_reason": overall,
        "slot_normalization": {
            "applied": model_total != sum(row["slots"] for row in parsed),
            "model_total": model_total,
            "runtime_total": sum(row["slots"] for row in parsed),
            "capacity": capacity,
        },
    }


GLOBAL_GRANT_PRIORITY = ("reading_evidence", "starved_favourite", "weak_ending")


def _unused_freed_slots(cut_selection: dict[str, Any]) -> int:
    cap = cut_selection.get("occasion_cap") or {}
    freed = cap.get("freed_slots", 0)
    if not freed:
        return 0
    return max(0, freed - len(((cap.get("reallocation") or {}).get("keep")) or ()))


def _unserved_reading_evidence(
    chapter: Chapter,
    cut_selection: dict[str, Any],
    reading: dict[str, Any] | None,
) -> tuple[str, ...]:
    kept = {row["moment_id"] for row in cut_selection["keep"]}
    local = {card.moment.alias for card in chapter.cards}
    cited = _reading_evidence_ids(reading) & local
    return tuple(card.moment.alias for card in chapter.cards if card.moment.alias in cited - kept)


def _starved_favourites(chapter: Chapter, cut_selection: dict[str, Any]) -> tuple[str, ...]:
    kept = {row["moment_id"] for row in cut_selection["keep"]}
    cap = cut_selection.get("occasion_cap")
    # A chapter the allocator gave no slots never ran a cap, and every occasion
    # in it is starved by definition.
    starved = (
        {row["occasion_id"] for row in cap.get("starved_occasions", ())}
        if cap is not None
        else {_occasion_id(card) for card in chapter.cards}
    )
    return tuple(
        card.moment.alias
        for card in chapter.cards
        if card.moment.alias not in kept
        and _occasion_id(card) in starved
        and any(candidate.favourite for candidate in card.moment.group.candidates)
    )


def _unserved_closing_occasion(
    chapter: Chapter,
    cut_selection: dict[str, Any],
    final_occasion: str,
) -> tuple[str, ...]:
    kept = {row["moment_id"] for row in cut_selection["keep"]}
    closing = tuple(
        card.moment.alias for card in chapter.cards if _occasion_id(card) == final_occasion
    )
    return () if any(alias in kept for alias in closing) else closing


def _global_grant_claims(
    chapters: tuple[Chapter, ...],
    cuts: tuple[tuple[dict[str, Any], tuple[TextCall, ...]], ...],
    reading_by_chapter: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank the chapters another slot would serve; each chapter claims on one ground only."""
    final_occasion = max(_occasion_id(card) for chapter in chapters for card in chapter.cards)
    by_class: dict[str, list[dict[str, Any]]] = {name: [] for name in GLOBAL_GRANT_PRIORITY}
    for index, (chapter, (cut_selection, _calls)) in enumerate(zip(chapters, cuts, strict=True)):
        wanted = {
            "reading_evidence": _unserved_reading_evidence(
                chapter, cut_selection, reading_by_chapter.get(chapter.chapter_id)
            ),
            "starved_favourite": _starved_favourites(chapter, cut_selection),
            "weak_ending": (
                _unserved_closing_occasion(chapter, cut_selection, final_occasion)
                if index == len(chapters) - 1
                else ()
            ),
        }
        for name in GLOBAL_GRANT_PRIORITY:
            if wanted[name]:
                by_class[name].append(
                    {
                        "chapter_id": chapter.chapter_id,
                        "reason": name,
                        "unserved_moment_ids": list(wanted[name]),
                    }
                )
                break
    return [
        # Ties hold chapter order: sorted is stable and the lists are built in it.
        *sorted(by_class["reading_evidence"], key=lambda row: -len(row["unserved_moment_ids"])),
        *by_class["starved_favourite"],
        *by_class["weak_ending"],
    ]


def _grant_freed_slots(claims: list[dict[str, Any]], pooled: int) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    remaining = pooled
    for claim in claims:
        if not remaining:
            break
        slots = min(len(claim["unserved_moment_ids"]), remaining)
        grants.append({**claim, "slots": slots})
        remaining -= slots
    return grants


async def _reask_granted_chapter(
    chapter: Chapter,
    cut_selection: dict[str, Any],
    slots: int,
    *,
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    reading: dict[str, Any] | None,
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, dict[str, Any], TextCall]:
    cap = cut_selection.get("occasion_cap") or {}
    excluded = frozenset(
        {row["moment_id"] for row in cut_selection["keep"]}
        | set(cap.get("removed_moment_ids", ()))
        | set((cap.get("reallocation") or {}).get("removed_moment_ids", ()))
    )
    aliases = frozenset(card.moment.alias for card in chapter.cards)
    call = await _ask_text(
        _selection_prompt(chapter.cards, case=case, facts=facts, thesis=thesis, capacity=slots),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=4000,
        timeout_seconds=timeout_seconds,
        thinking=False,
    )
    error: str | None = None
    try:
        round_keep = _read_selection_with_comparison_repair(
            call.raw, aliases, slots, excluded_ids=excluded
        )["keep"]
    except ValueError as exc:
        round_keep, error = [], str(exc)
    record: dict[str, Any] = {
        "chapter_id": chapter.chapter_id,
        "slots": slots,
        "keep": round_keep,
        "removed_moment_ids": [],
        **({"error": error} if error else {}),
    }
    if not round_keep:
        return None, record, call
    rows = {row["moment_id"]: row for row in (*cut_selection["keep"], *round_keep)}
    merged = _apply_occasion_cap(
        {
            **cut_selection,
            "keep": [
                rows[card.moment.alias] for card in chapter.cards if card.moment.alias in rows
            ],
        },
        chapter.cards,
        protected_ids=_reading_evidence_ids(reading),
    )
    record["removed_moment_ids"] = merged["occasion_cap"]["removed_moment_ids"]
    # The chapter's own cap record stands: this round's removals belong to the
    # global record, exactly as the in-chapter round keeps its own cap block.
    return _with_rejected_trace({**merged, "occasion_cap": cap}, chapter.cards), record, call


async def _redistribute_freed_slots(
    chapters: tuple[Chapter, ...],
    cuts: tuple[tuple[dict[str, Any], tuple[TextCall, ...]], ...],
    *,
    reading_by_chapter: dict[str, dict[str, Any]],
    case: Case,
    facts: dict[str, PersonFact],
    thesis: dict[str, Any],
    llm_config: Any,
    cache_path: Path,
    semaphore: asyncio.Semaphore,
    timeout_seconds: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], tuple[TextCall, ...]]:
    """Spend one chapter's capped slots on the starved chapters its own round cannot reach."""
    pooled = sum(_unused_freed_slots(cut_selection) for cut_selection, _calls in cuts)
    claims = _global_grant_claims(chapters, cuts, reading_by_chapter) if pooled else []
    grants = _grant_freed_slots(claims, pooled)
    if not grants:
        return {}, {"pooled": pooled}, ()
    chapter_by_id = {chapter.chapter_id: chapter for chapter in chapters}
    cut_by_id = {
        chapter.chapter_id: cut_selection
        for chapter, (cut_selection, _calls) in zip(chapters, cuts, strict=True)
    }

    async def reask(
        grant: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], TextCall]:
        chapter = chapter_by_id[grant["chapter_id"]]
        reading = reading_by_chapter.get(chapter.chapter_id)
        async with semaphore:
            return await _reask_granted_chapter(
                chapter,
                cut_by_id[chapter.chapter_id],
                grant["slots"],
                case=case,
                facts=facts,
                thesis=_local_thesis(thesis, chapter.cards, reading=reading),
                reading=reading,
                llm_config=llm_config,
                cache_path=cache_path,
                timeout_seconds=timeout_seconds,
            )

    outcomes = await asyncio.gather(*(reask(grant) for grant in grants))
    updated: dict[str, dict[str, Any]] = {}
    rounds: list[dict[str, Any]] = []
    forfeits: list[dict[str, Any]] = []
    for grant, (merged, round_record, _call) in zip(grants, outcomes, strict=True):
        if merged is None:
            forfeits.append(
                {
                    "chapter_id": grant["chapter_id"],
                    "reason": grant["reason"],
                    "slots": grant["slots"],
                    **({"error": round_record["error"]} if "error" in round_record else {}),
                }
            )
            continue
        updated[grant["chapter_id"]] = merged
        rounds.append(round_record)
    record = {"pooled": pooled, "grants": grants, "rounds": rounds, "forfeits": forfeits}
    return updated, record, tuple(call for _merged, _record, call in outcomes)


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
    thinking: bool,
) -> tuple[dict[str, Any], dict[str, Any], tuple[TextCall, ...]]:
    minimum_slots = {
        chapter.chapter_id: len(
            {item.anchor_id for item in _lifecycle_requirements(chapter.cards, facts)}
        )
        for chapter in chapters
    }
    minimum_slots = {key: value for key, value in minimum_slots.items() if value}
    if sum(minimum_slots.values()) > capacity:
        raise ValueError("lifecycle anchors exceed the global moment capacity")
    allocation_call = await _ask_text(
        _allocation_prompt(case, thesis, readings, capacity, minimum_slots),
        llm_config=llm_config,
        cache_path=cache_path,
        max_tokens=3000,
        timeout_seconds=timeout_seconds,
        thinking=thinking,
    )
    allocation = _read_allocation(allocation_call.raw, chapters, capacity, minimum_slots)
    slots = {row["chapter_id"]: row["slots"] for row in allocation["allocations"]}
    reading_by_chapter = {chapter.chapter_id: reading for chapter, reading in readings}
    semaphore = asyncio.Semaphore(concurrency)

    async def cut(chapter: Chapter) -> tuple[dict[str, Any], tuple[TextCall, ...]]:
        if slots[chapter.chapter_id] == 0:
            return {"keep": [], "overall_reason": "This chapter received no scarce slots."}, ()
        async with semaphore:
            return await _select_cards(
                chapter.cards,
                case=case,
                facts=facts,
                thesis=_local_thesis(
                    thesis,
                    chapter.cards,
                    reading=reading_by_chapter[chapter.chapter_id],
                ),
                reading=reading_by_chapter[chapter.chapter_id],
                capacity=slots[chapter.chapter_id],
                llm_config=llm_config,
                cache_path=cache_path,
                timeout_seconds=timeout_seconds,
                thinking=False,
            )

    cuts = tuple(await asyncio.gather(*(cut(chapter) for chapter in chapters)))
    updated, global_reallocation, global_calls = await _redistribute_freed_slots(
        chapters,
        cuts,
        reading_by_chapter=reading_by_chapter,
        case=case,
        facts=facts,
        thesis=thesis,
        llm_config=llm_config,
        cache_path=cache_path,
        semaphore=semaphore,
        timeout_seconds=timeout_seconds,
    )
    cuts = tuple(
        (updated.get(chapter.chapter_id, cut_selection), cut_calls)
        for chapter, (cut_selection, cut_calls) in zip(chapters, cuts, strict=True)
    )
    by_id = {row["moment_id"]: row for selection, _calls in cuts for row in selection["keep"]}
    rejected_by_id = {
        row["moment_id"]: row for selection, _calls in cuts for row in selection.get("rejected", ())
    }
    ordered_cards = [card for chapter in chapters for card in chapter.cards]
    selection = {
        "keep": [by_id[card.moment.alias] for card in ordered_cards if card.moment.alias in by_id],
        # A chapter that received no slots never ran a cut, so its moments carry no
        # comparison; they are still rejections and still owe the record a row.
        "rejected": [
            rejected_by_id.get(card.moment.alias, {"moment_id": card.moment.alias, "reason": None})
            for card in ordered_cards
            if card.moment.alias not in by_id
        ],
        "occasion_cap": [
            {"chapter_id": chapter.chapter_id, **cut_selection["occasion_cap"]}
            for chapter, (cut_selection, _calls) in zip(chapters, cuts, strict=True)
            if cut_selection.get("occasion_cap", {}).get("freed_slots")
        ],
        "global_reallocation": global_reallocation,
        "overall_reason": allocation["overall_reason"],
    }
    calls = (
        allocation_call,
        *(call for _selection, cut_calls in cuts for call in cut_calls),
        *global_calls,
    )
    return selection, allocation, calls


def _call_record(call: TextCall | None) -> dict[str, Any] | None:
    return None if call is None else asdict(call)


async def _text_phase(
    case: Case,
    prepared: Any,
    descriptions: tuple[prototype.Description, ...],
    *,
    atlas: Any,
    facts: dict[str, PersonFact],
    config: Any,
    out: Path,
    vision_model: str,
    text_model: str,
    concurrency: int,
    editorial_concurrency: int,
    timeout_seconds: int,
    editorial_thinking: bool,
    card_mode: str,
    text_cache_path: Path | None,
) -> dict[str, Any]:
    cache_path = text_cache_path or out.parent / "text-judgments.db"
    editorial_llm_config = config.llm.model_copy(update={"model": text_model})
    card_llm_config = config.llm.model_copy(update={"model": vision_model})
    cards_started = time.monotonic()
    cards: tuple[prototype.MomentCard, ...]
    card_calls: tuple[TextCall | None, ...]
    if card_mode == "fused-vision":
        card_gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=card_llm_config,
                cache_path=cache_path,
                trace=prepared.trace,
            ),
            expected_tiles={
                "fused-moment-card": sum(len(group.candidates) for group in prepared.moment_groups)
            },
        )
        try:
            cards, card_calls = await _build_fused_cards(
                prepared.moment_groups,
                facts=facts,
                atlas=atlas,
                requester=card_gateway,
                output_dir=out / "fused-card-sheets",
                llm_config=card_llm_config,
                cache_path=cache_path,
                concurrency=concurrency,
                timeout_seconds=timeout_seconds,
            )
        finally:
            card_gateway.close()
    else:
        moments = prototype._moments(prepared, descriptions)
        cards, card_calls = await _build_cards(
            moments,
            facts=facts,
            llm_config=card_llm_config,
            cache_path=cache_path,
            concurrency=concurrency,
            timeout_seconds=timeout_seconds,
            card_mode=card_mode,
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
    thesis_calls: tuple[TextCall, ...]
    wall_chars = _editorial_wall_chars(cards, facts=facts)
    if _use_flat_editorial_wall(cards, facts=facts):
        thesis, thesis_call = await _read_thesis(
            cards,
            case=case,
            facts=facts,
            llm_config=editorial_llm_config,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
            thinking=editorial_thinking,
        )
        chapter_readings: tuple[tuple[Chapter, dict[str, Any]], ...] = ()
        thesis_calls = (thesis_call,)
        chapters: tuple[Chapter, ...] = ()
        shape = "flat"
    else:
        chapters = _chapters(case, cards, facts=facts)
        thesis, chapter_readings, thesis_calls = await _hierarchical_thesis(
            chapters,
            case=case,
            facts=facts,
            llm_config=editorial_llm_config,
            cache_path=cache_path,
            concurrency=editorial_concurrency,
            timeout_seconds=timeout_seconds,
            thinking=editorial_thinking,
        )
        shape = "hierarchical"
    thesis_seconds = time.monotonic() - thesis_started
    selection_started = time.monotonic()
    allocation = None
    if shape == "flat":
        selection, selection_calls = await _select_cards(
            cards,
            case=case,
            facts=facts,
            thesis=thesis,
            capacity=moment_capacity,
            llm_config=editorial_llm_config,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
            thinking=editorial_thinking,
        )
    else:
        selection, allocation, selection_calls = await _hierarchical_selection(
            chapters,
            chapter_readings,
            case=case,
            facts=facts,
            thesis=thesis,
            capacity=moment_capacity,
            llm_config=editorial_llm_config,
            cache_path=cache_path,
            concurrency=editorial_concurrency,
            timeout_seconds=timeout_seconds,
            thinking=editorial_thinking,
        )
    selection_seconds = time.monotonic() - selection_started
    kept = {row["moment_id"] for row in selection["keep"]}
    lifecycle_requirements = _lifecycle_requirements(cards, facts)
    lifecycle_anchor_ids = {item.anchor_id for item in lifecycle_requirements}
    favourite_moments = {
        card.moment.alias
        for card in cards
        if any(candidate.favourite for candidate in card.moment.group.candidates)
    }
    model_card_calls = tuple(call for call in card_calls if call is not None)
    all_calls = (*model_card_calls, *thesis_calls, *selection_calls)
    return {
        "configuration": {
            "text_model": text_model,
            "card_model": vision_model,
            "temperature": 0.0,
            "thinking": editorial_thinking,
            "thinking_scope": (
                "global thesis and global allocation; flat final cut"
                if editorial_thinking
                else "disabled"
            ),
            "card_mode": card_mode,
            "bulk_concurrency": concurrency,
            "editorial_concurrency": editorial_concurrency,
            "shape": shape,
            "flat_wall_max_cards": FLAT_WALL_MAX_CARDS,
            "chapter_max_cards": CHAPTER_MAX_CARDS,
            "editorial_wall_chars": wall_chars,
            "editorial_wall_max_chars": EDITORIAL_WALL_MAX_CHARS,
            "capacity": capacity,
        },
        "counts": {
            "moments": len(cards),
            "chapters": len(chapters),
            "selected_moments": len(kept),
            "favourite_moments": len(favourite_moments),
            "selected_favourite_moments": len(kept & favourite_moments),
            "lifecycle_requirements": len(lifecycle_requirements),
            "lifecycle_anchor_moments": len(lifecycle_anchor_ids),
            "selected_lifecycle_anchor_moments": len(kept & lifecycle_anchor_ids),
            "template_cards": len(card_calls) - len(model_card_calls),
            "model_card_calls": len(model_card_calls),
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
            {
                "chapter_id": chapter.chapter_id,
                "label": chapter.label,
                "moment_ids": [card.moment.alias for card in chapter.cards],
                **reading,
            }
            for chapter, reading in chapter_readings
        ],
        "allocation": allocation,
        "lifecycle_requirements": [asdict(item) for item in lifecycle_requirements],
        "selection": selection,
        "selected_production_group_ids": [
            card.moment.group.group_id for card in cards if card.moment.alias in kept
        ],
        "moment_alias_by_group": {card.moment.group.group_id: card.moment.alias for card in cards},
        "thesis_calls": [_call_record(call) for call in thesis_calls],
        "selection_calls": [_call_record(call) for call in selection_calls],
    }


def _candidate_people_names(candidate: Any) -> set[str]:
    return {
        str(person.name).strip()
        for person in (candidate.source.people or ())
        if str(person.name or "").strip()
    }


def _required_fine_cut_ids(
    candidates: tuple[Any, ...],
    *,
    reservoirs: tuple[Any, ...],
    text_result: dict[str, Any],
) -> tuple[str, ...]:
    """Choose one grounded asset for every retained hard moment obligation."""
    alive = {candidate.asset_id: candidate for candidate in candidates}
    alias_by_group = text_result["moment_alias_by_group"]
    lifecycle_names: dict[str, set[str]] = defaultdict(set)
    for requirement in text_result["lifecycle_requirements"]:
        lifecycle_names[str(requirement["anchor_id"])].add(str(requirement["person_name"]))

    required: list[str] = []
    for reservoir in reservoirs:
        members = tuple(
            alive[candidate.asset_id]
            for candidate in reservoir.candidates
            if candidate.asset_id in alive
        )
        if not members:
            continue
        moment_alias = alias_by_group[reservoir.moment_id]
        names = lifecycle_names.get(moment_alias, set())
        if not names:
            continue
        anchor = min(
            members,
            key=lambda candidate: (
                not bool(_candidate_people_names(candidate).intersection(names)),
                not candidate.favourite,
                candidate != reservoir.representative,
                candidate.taken_at,
                candidate.asset_id,
            ),
        )
        required.append(anchor.asset_id)
        favourites = tuple(candidate for candidate in members if candidate.favourite)
        if favourites and not anchor.favourite:
            required.append(
                min(
                    favourites,
                    key=lambda candidate: (
                        candidate != reservoir.representative,
                        candidate.taken_at,
                        candidate.asset_id,
                    ),
                ).asset_id
            )
    return tuple(dict.fromkeys(required))


def _restore_required_fine_cut_candidates(
    survivors: tuple[Any, ...],
    *,
    reservoir_candidates: tuple[Any, ...],
    required_asset_ids: tuple[str, ...],
) -> tuple[Any, ...]:
    """Keep graph-grounded asset obligations alive across similarity collapse."""
    by_id = {candidate.asset_id: candidate for candidate in reservoir_candidates}
    missing = tuple(asset_id for asset_id in required_asset_ids if asset_id not in by_id)
    if missing:
        raise ValueError(f"required fine-cut assets escaped their reservoirs: {missing}")
    restored = {
        candidate.asset_id: candidate
        for candidate in (*survivors, *(by_id[asset_id] for asset_id in required_asset_ids))
    }
    return tuple(sorted(restored.values(), key=lambda item: (item.taken_at, item.asset_id)))


# The v28 closer was a luminance-53 frame the wall described as ordinary texture: the
# text contract could not see that it was dark. Decoding is the expensive part, so one
# process-wide answer per asset serves every wall the run renders.
_PREVIEW_LUMINANCE_CACHE: dict[str, int | None] = {}


def _preview_luminance(asset_id: str, preview_jpeg: Callable[[str], bytes | None]) -> int | None:
    if asset_id in _PREVIEW_LUMINANCE_CACHE:
        return _PREVIEW_LUMINANCE_CACHE[asset_id]
    value: int | None = None
    try:
        jpeg = preview_jpeg(asset_id)
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as image:
                value = round(ImageStat.Stat(image.convert("L")).mean[0])
    except (OSError, TypeError, ValueError):
        value = None
    _PREVIEW_LUMINANCE_CACHE[asset_id] = value
    return value


def _with_preview_luminance(
    wall: Sequence[FineCutCandidate],
    *,
    preview_jpeg: Callable[[str], bytes | None],
) -> tuple[FineCutCandidate, ...]:
    """Attach mean preview luminance to every fine-cut row as a per-candidate fact."""
    return tuple(
        replace(candidate, luminance=_preview_luminance(candidate.asset_id, preview_jpeg))
        for candidate in wall
    )


def _fine_cut_candidates(
    candidates: tuple[Any, ...],
    *,
    reservoirs: tuple[Any, ...],
    descriptions: dict[str, str],
    text_result: dict[str, Any],
    facts: dict[str, PersonFact],
    atlas: Any | None = None,
    motion_contributions: dict[str, str] | None = None,
    motion_reasons: dict[str, str] | None = None,
) -> tuple[FineCutCandidate, ...]:
    group_by_asset = {
        candidate.asset_id: reservoir.moment_id
        for reservoir in reservoirs
        for candidate in reservoir.candidates
    }
    alias_by_group = text_result["moment_alias_by_group"]
    ordered = sorted(candidates, key=lambda item: (item.taken_at, item.asset_id))
    names = sorted(
        {
            name
            for candidate in ordered
            for person in (candidate.source.people or ())
            if (name := str(person.name or "").strip())
        },
        key=str.casefold,
    )
    token_by_name = {name: f"P{index:02d}" for index, name in enumerate(names, start=1)}
    episode_by_asset = {
        candidate.asset_id: f"E{index:03d}"
        for index, episode in enumerate(build_episode_groups(ordered), start=1)
        for candidate in episode.candidates
    }

    def people_context(candidate: Any) -> tuple[str, ...]:
        rows = []
        for person in candidate.source.people or ():
            name = str(person.name or "").strip()
            if not name or name not in token_by_name:
                continue
            fact = facts.get(name)
            rows.append(
                f"{token_by_name[name]}:tier={fact.tier or 'unknown'};"
                f"relationship={fact.relationship};source={fact.relationship_source}"
                if fact is not None
                else f"{token_by_name[name]}:tier=unknown;relationship=unconfirmed"
            )
        return tuple(dict.fromkeys(rows))

    contributions = motion_contributions or {}
    reasons = motion_reasons or {}

    def motion_is_observed(candidate: Any) -> bool:
        if atlas is None:
            return False
        try:
            tile = atlas.tile_for(candidate.asset_id)
        except KeyError:
            return False
        return tile.kind == "filmstrip"

    def editorial_motion(candidate: Any) -> tuple[str, str | None, str | None, bool]:
        observed = motion_is_observed(candidate)
        contribution = contributions.get(candidate.asset_id)
        reason = reasons.get(candidate.asset_id)
        if candidate.media_kind == "video":
            kind = "video" if observed and contribution == "meaningful" else "photo"
            return kind, contribution, reason, observed
        if candidate.media_kind != "live_photo":
            return candidate.media_kind, contribution, reason, observed
        if not observed:
            return "photo", None, None, False
        kind = "live-motion" if contribution == "meaningful" else "photo"
        return kind, contribution, reason, True

    def fine_cut_candidate(index: int, candidate: Any) -> FineCutCandidate:
        media_kind, motion_contribution, motion_reason, motion_observed = editorial_motion(
            candidate
        )
        render_mode = "motion" if media_kind in {"video", "live-motion"} else "still"
        render_frame_seconds: float | None = None
        if render_mode == "still" and candidate.media_kind == "video":
            segment = candidate.proposed_segment
            if segment is not None and segment[1] > segment[0]:
                render_frame_seconds = segment[0] + (segment[1] - segment[0]) / 2
            else:
                render_frame_seconds = max(0.0, candidate.shippable_duration / 2)
        return FineCutCandidate(
            alias=f"A{index:03d}",
            asset_id=candidate.asset_id,
            moment_id=alias_by_group[group_by_asset[candidate.asset_id]],
            taken_at=candidate.taken_at,
            media_kind=media_kind,
            favourite=candidate.favourite,
            description=descriptions.get(candidate.asset_id, "[visual description unavailable]"),
            context=tuple(candidate.grounded_annotations),
            episode_id=episode_by_asset.get(candidate.asset_id),
            people_context=people_context(candidate),
            motion_contribution=motion_contribution,
            motion_reason=motion_reason,
            source_media_kind=candidate.media_kind,
            motion_observed=motion_observed,
            render_mode=render_mode,
            render_frame_seconds=render_frame_seconds,
        )

    return tuple(
        fine_cut_candidate(index, candidate) for index, candidate in enumerate(ordered, start=1)
    )


def _needs_optional_asset_cut(
    candidates: tuple[FineCutCandidate, ...],
    *,
    required_aliases: tuple[str, ...],
    capacity: int,
) -> bool:
    """Whether quality still has a choice after runtime obligations are applied."""
    return len(required_aliases) < capacity and len(candidates) > len(required_aliases)


def _hierarchical_final_cut_plan(
    text_result: dict[str, Any],
    wall: tuple[FineCutCandidate, ...],
    *,
    required_aliases: tuple[str, ...],
    capacity: int,
) -> tuple[dict[str, Any], ...]:
    """Scale the global moment allocation into bounded chronological asset cuts."""
    readings = text_result.get("chapter_readings")
    allocation = text_result.get("allocation")
    allocation_rows = allocation.get("allocations") if isinstance(allocation, dict) else None
    if not isinstance(readings, list) or not isinstance(allocation_rows, list):
        raise ValueError("hierarchical final cut needs chapter readings and allocation")
    source_slots: dict[str, int] = {}
    for row in allocation_rows:
        if not isinstance(row, dict):
            continue
        chapter_id, slots = row.get("chapter_id"), row.get("slots")
        if isinstance(chapter_id, str) and isinstance(slots, int) and not isinstance(slots, bool):
            source_slots[chapter_id] = slots
    chapter_by_moment: dict[str, tuple[str, str]] = {}
    reading_by_chapter: dict[str, dict[str, Any]] = {}
    chapter_order: list[tuple[str, str]] = []
    for row in readings:
        if not isinstance(row, dict):
            raise ValueError("final-cut chapter reading has the wrong shape")
        chapter_id, label, moment_ids = (
            row.get("chapter_id"),
            row.get("label"),
            row.get("moment_ids"),
        )
        if (
            not isinstance(chapter_id, str)
            or not isinstance(label, str)
            or not isinstance(moment_ids, list)
            or chapter_id not in source_slots
        ):
            raise ValueError("final-cut chapter reading is incomplete")
        chapter_order.append((chapter_id, label))
        reading_by_chapter[chapter_id] = row
        for moment_id in moment_ids:
            if not isinstance(moment_id, str) or moment_id in chapter_by_moment:
                raise ValueError("final-cut moment-to-chapter mapping is ambiguous")
            chapter_by_moment[moment_id] = (chapter_id, label)

    selection = text_result.get("selection")
    raw_admissions = selection.get("keep") if isinstance(selection, dict) else None
    admissions = (
        [
            row
            for row in raw_admissions
            if isinstance(row, dict)
            and isinstance(row.get("moment_id"), str)
            and isinstance(row.get("reason"), str)
        ]
        if isinstance(raw_admissions, list)
        else []
    )

    candidates_by_chapter: dict[str, list[FineCutCandidate]] = defaultdict(list)
    for candidate in wall:
        chapter = chapter_by_moment.get(candidate.moment_id)
        if chapter is None:
            raise ValueError("final-cut candidate escaped the chapter allocation")
        candidates_by_chapter[chapter[0]].append(candidate)
    required = set(required_aliases)
    if not required <= {candidate.alias for candidate in wall}:
        raise ValueError("required final-cut assets escaped the hierarchical wall")

    plans: list[dict[str, Any]] = []
    for chapter_id, label in chapter_order:
        candidates = tuple(candidates_by_chapter.get(chapter_id, ()))
        if not candidates:
            continue
        chapter_required = tuple(
            candidate.alias for candidate in candidates if candidate.alias in required
        )
        plans.append(
            {
                "chapter_id": chapter_id,
                "label": label,
                "source_slots": max(1, source_slots[chapter_id]),
                "candidates": candidates,
                "required_aliases": chapter_required,
                "capacity": len(chapter_required),
                "story_evidence": {
                    "chapter_reading": reading_by_chapter[chapter_id],
                    "admitted_moments": [
                        row
                        for row in admissions
                        if chapter_by_moment.get(row["moment_id"], (None, None))[0] == chapter_id
                    ],
                },
            }
        )

    required_count = sum(len(plan["required_aliases"]) for plan in plans)
    if required_count > capacity:
        raise ValueError("hierarchical required assets exceed final duration capacity")
    target = min(capacity, sum(len(plan["candidates"]) for plan in plans))
    weights = [plan["source_slots"] for plan in plans]
    weight_total = sum(weights)
    desired = [target * weight / weight_total for weight in weights]
    remaining = target - required_count
    while remaining:
        eligible = [
            index for index, plan in enumerate(plans) if plan["capacity"] < len(plan["candidates"])
        ]
        if not eligible:
            break
        winner = max(
            eligible,
            key=lambda index: (
                desired[index] - plans[index]["capacity"],
                -index,
            ),
        )
        plans[winner]["capacity"] += 1
        remaining -= 1
    return tuple(plans)


async def _hierarchical_final_asset_cut(
    plans: tuple[dict[str, Any], ...],
    *,
    case: Case,
    thesis: dict[str, Any],
    llm_config: Any,
    cache_path: Path,
    concurrency: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any], tuple[TextCall, ...]]:
    semaphore = asyncio.Semaphore(concurrency)
    progress_started = time.monotonic()
    completed = 0
    cache_hits = 0
    actual_calls = 0

    def record_progress(calls: tuple[TextCall, ...]) -> None:
        nonlocal actual_calls, cache_hits, completed
        completed += 1
        cache_hits += sum(call.cache_hit for call in calls)
        actual_calls += sum(not call.cache_hit for call in calls)
        elapsed = time.monotonic() - progress_started
        eta = elapsed / completed * (len(plans) - completed)
        print(
            f"final-asset-cut: {completed}/{len(plans)} chapters complete, "
            f"{cache_hits} cache hits, {actual_calls} actual calls, "
            f"elapsed {_progress_duration(elapsed)}, ETA {_progress_duration(eta)}",
            flush=True,
        )

    async def cut(plan: dict[str, Any]) -> tuple[dict[str, Any], tuple[TextCall, ...]]:
        candidates = plan["candidates"]
        local_candidates = tuple(
            replace(candidate, alias=f"A{index:03d}")
            for index, candidate in enumerate(candidates, start=1)
        )
        local_by_global = {
            global_candidate.alias: local_candidate.alias
            for global_candidate, local_candidate in zip(candidates, local_candidates, strict=True)
        }
        global_by_local = {local: global_ for global_, local in local_by_global.items()}
        required = tuple(local_by_global[alias] for alias in plan["required_aliases"])
        chapter_capacity = plan["capacity"]
        if not _needs_optional_asset_cut(
            local_candidates,
            required_aliases=required,
            capacity=chapter_capacity,
        ):
            selected = set(required)
            result = (
                {
                    "keep": [
                        {
                            "asset_id": candidate.alias,
                            "reason": "Admitted by the runtime before the optional asset cut.",
                        }
                        for candidate in local_candidates
                        if candidate.alias in selected
                    ],
                    "required_asset_ids": list(required),
                    "discarded_required_echoes": 0,
                    "discarded_duplicate_keeps": 0,
                    "comparisons": [],
                    "discarded_comparisons": 0,
                    "overall_reason": "Runtime obligations consume this chapter's allocation.",
                },
                (),
            )
            record_progress(result[1])
            return result
        prompt = final_asset_cut_prompt(
            local_candidates,
            memory_label=f"{case.label} — {plan['label']}",
            memory_type=case.product,
            editorial_brief=case.brief,
            thesis=thesis,
            capacity=chapter_capacity,
            required_aliases=required,
            local_story_evidence=plan.get("story_evidence"),
        )
        async with semaphore:
            call = await _ask_text(
                prompt,
                llm_config=llm_config,
                cache_path=cache_path,
                max_tokens=4000,
                timeout_seconds=timeout_seconds,
                thinking=False,
            )
            calls = [call]
            try:
                result = read_final_asset_cut(
                    call.raw,
                    local_candidates,
                    capacity=chapter_capacity,
                    required_aliases=required,
                    project_favourites=True,
                )
            except ValueError as exc:
                if str(exc) != "final asset keep row is not grounded":
                    raise
                repair = await _ask_text(
                    _final_asset_cut_repair_prompt(prompt, call.raw, str(exc)),
                    llm_config=llm_config,
                    cache_path=cache_path,
                    max_tokens=4000,
                    timeout_seconds=timeout_seconds,
                    thinking=False,
                )
                calls.append(repair)
                result = read_final_asset_cut(
                    repair.raw,
                    local_candidates,
                    capacity=chapter_capacity,
                    required_aliases=required,
                    project_favourites=True,
                )
        chapter_calls = tuple(calls)
        record_progress(chapter_calls)
        return _restore_global_final_cut_aliases(result, global_by_local), chapter_calls

    rows = await asyncio.gather(*(cut(plan) for plan in plans))
    reasons = {
        row["asset_id"]: row["reason"] for cut_result, _calls in rows for row in cut_result["keep"]
    }
    ordered_candidates = tuple(candidate for plan in plans for candidate in plan["candidates"])
    required = tuple(alias for plan in plans for alias in plan["required_aliases"])
    result = {
        "keep": [
            {"asset_id": candidate.alias, "reason": reasons[candidate.alias]}
            for candidate in ordered_candidates
            if candidate.alias in reasons
        ],
        "required_asset_ids": list(required),
        "discarded_required_echoes": sum(
            cut_result["discarded_required_echoes"] for cut_result, _calls in rows
        ),
        "discarded_duplicate_keeps": sum(
            cut_result["discarded_duplicate_keeps"] for cut_result, _calls in rows
        ),
        "projected_favourite_assets": sum(
            cut_result.get("projected_favourite_assets", 0) for cut_result, _calls in rows
        ),
        "comparisons": [
            comparison for cut_result, _calls in rows for comparison in cut_result["comparisons"]
        ],
        "discarded_comparisons": sum(
            cut_result["discarded_comparisons"] for cut_result, _calls in rows
        ),
        "overall_reason": (
            "Chronological chapter cuts apply the same global thesis under the scaled duration "
            "allocation; unused chapter slots remain unused."
        ),
        "chapters": [
            {
                "chapter_id": plan["chapter_id"],
                "label": plan["label"],
                "source_slots": plan["source_slots"],
                "capacity": plan["capacity"],
                "candidates": len(plan["candidates"]),
                "selected": len(cut_result["keep"]),
                "calls": len(chapter_calls),
                "projected_favourite_assets": cut_result.get("projected_favourite_assets", 0),
                "overall_reason": cut_result["overall_reason"],
            }
            for plan, (cut_result, chapter_calls) in zip(plans, rows, strict=True)
        ],
    }
    return result, tuple(call for _cut_result, chapter_calls in rows for call in chapter_calls)


async def _global_final_sequence_review(
    wall: tuple[FineCutCandidate, ...],
    cut: dict[str, Any],
    *,
    case: Case,
    thesis: dict[str, Any],
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], TextCall | None]:
    """Reduce the assembled chapter winners with one whole-sequence view."""
    proposed = tuple(row["asset_id"] for row in cut["keep"])
    required = tuple(cut.get("required_asset_ids", ()))
    if len(proposed) <= 1 or set(proposed) <= set(required):
        review = {
            "status": "not-needed",
            "keep": list(proposed),
            "cut": [],
            "overall_reason": "No optional whole-sequence decision remains.",
        }
        return (
            {
                **cut,
                "pre_global_review_keep": cut["keep"],
                "global_review": review,
            },
            review,
            None,
        )
    prompt = final_asset_sequence_review_prompt(
        wall,
        proposed_aliases=proposed,
        required_aliases=required,
        editorial_brief=case.brief,
        thesis=thesis,
    )
    try:
        call = await _ask_text(
            prompt,
            llm_config=llm_config,
            cache_path=cache_path,
            max_tokens=8000,
            timeout_seconds=timeout_seconds,
            thinking=False,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        call = None
        review = {
            "status": "unapproved",
            "keep": list(proposed),
            "cut": [],
            "overall_reason": "The global sequence review failed open.",
            "warning": f"global sequence review failed: {type(exc).__name__}",
        }
    else:
        assert call is not None
        review = apply_final_asset_sequence_review(
            call.raw,
            wall,
            proposed_aliases=proposed,
            required_aliases=required,
        )
    kept = set(review["keep"])
    reviewed = {
        **cut,
        "pre_global_review_keep": cut["keep"],
        "keep": [row for row in cut["keep"] if row["asset_id"] in kept],
        "global_review": review,
    }
    return reviewed, review, call


def _run_visual_final_asset_audit(
    wall: tuple[FineCutCandidate, ...],
    *,
    current_aliases: tuple[str, ...],
    case: Case,
    thesis: dict[str, Any],
    chapter_readings: Sequence[dict[str, Any]] = (),
    requester: Any,
    output_dir: Path,
    preview_jpeg: Callable[[str], bytes | None],
    iteration: int,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    """Inspect bounded current-wall groups with pixels, failing open per group."""
    by_alias = {candidate.alias: candidate for candidate in wall}
    review_focus = runtime_final_asset_audit_findings(
        wall,
        current_aliases=current_aliases,
    )
    groups = visual_final_asset_audit_groups(
        wall,
        current_aliases=current_aliases,
        review_focus=review_focus,
    )
    audits: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for group in groups:
        aliases = tuple(group["asset_ids"])
        group_moments = {by_alias[alias].moment_id for alias in aliases}
        matching_readings = tuple(
            reading
            for reading in chapter_readings
            if isinstance(reading, dict)
            and isinstance(reading.get("moment_ids"), list)
            and group_moments.intersection(reading["moment_ids"])
        )
        local_story_evidence = tuple(
            {
                key: reading[key]
                for key in (
                    "chapter_id",
                    "label",
                    "thesis",
                    "turning_points",
                    "sustained_threads",
                    "ordinary_texture",
                )
                if key in reading
            }
            for reading in matching_readings
        )
        complete_local_story = len(matching_readings) == 1 and set(aliases) == {
            alias
            for alias in current_aliases
            if by_alias[alias].moment_id in matching_readings[0]["moment_ids"]
        }
        tiles = tuple(
            SimpleNamespace(
                entity_id=alias,
                jpeg_bytes=preview_jpeg(by_alias[alias].asset_id),
                unavailable_reason="preview unavailable",
            )
            for alias in aliases
        )
        try:
            pages = build_contact_sheets(
                tiles,
                scope_id=(
                    f"{case.key}-visual-final-wall-audit-i{iteration:02d}-"
                    f"{str(group['group_id']).lower()}"
                ),
                output_dir=output_dir,
                per_sheet=min(len(tiles), 120),
                tile_px=250,
            )
            mapping = tuple(
                (reference.number, reference.entity_id)
                for page in pages
                for reference in page.tile_refs
            )
            prompt = visual_final_asset_audit_prompt(
                wall,
                current_aliases=current_aliases,
                group=group,
                tile_mapping=mapping,
                editorial_brief=case.brief,
                thesis=thesis,
                local_story_evidence=local_story_evidence,
                complete_local_story=complete_local_story,
            )
            answer = requester.ask(
                VisualEditorialRequest(
                    pass_name=VISUAL_FINAL_AUDIT_PASS_NAME,
                    pass_version=VISUAL_FINAL_AUDIT_PASS_VERSION,
                    prompt=prompt,
                    prompt_version=VISUAL_FINAL_AUDIT_PROMPT_VERSION,
                    schema_version=FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
                    pages=pages,
                    ordered_input_ids=tuple(by_alias[alias].asset_id for alias in aliases),
                    ordered_group_ids=(str(group["group_id"]),),
                    grounded_annotations=(),
                    upstream_material=(
                        "assembled-final-wall",
                        f"focus-kind={group['focus_kind']}",
                    ),
                    render_version=VISUAL_FINAL_AUDIT_RENDER_VERSION,
                    limits=VisionRequestLimits(
                        max_pages_per_request=len(pages),
                        max_output_tokens=2500,
                        timeout_seconds=timeout_seconds,
                    ),
                    thinking=False,
                    image_detail="low",
                )
            )
            audit = read_visual_final_asset_audit(
                answer.raw_text,
                wall,
                current_aliases=current_aliases,
            )
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            warnings.append(f"visual audit group {group['group_id']} failed: {type(exc).__name__}")
            continue
        audits.append(audit)
        summaries.append(
            {
                "group_id": group["group_id"],
                "focus_kind": group["focus_kind"],
                "asset_count": len(aliases),
                "verdict": audit["verdict"],
                "findings": len(audit["findings"]),
            }
        )
    if not audits:
        return None
    return {
        **merge_final_asset_audits(tuple(audits)),
        "groups": summaries,
        "warnings": warnings,
    }


def _fine_cut_media_priority(candidate: FineCutCandidate) -> int:
    if candidate.media_kind == "video":
        return 2
    if candidate.media_kind == "live-motion" and candidate.motion_contribution == "meaningful":
        return 1
    return 0


def _filter_lower_priority_near_duplicate_proposals(
    wall: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    proposals: Sequence[dict[str, Any]],
    preview_jpeg: Callable[[str], bytes | None],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Reject only grounded add-only representation duplicates before the global gate."""
    by_alias = {candidate.alias: candidate for candidate in wall}
    current = tuple(by_alias[alias] for alias in current_aliases)
    hashes: dict[str, str | None] = {}

    def thumbnail_hash(candidate: FineCutCandidate) -> str | None:
        if candidate.alias in hashes:
            return hashes[candidate.alias]
        try:
            jpeg = preview_jpeg(candidate.asset_id)
            value = compute_thumbnail_hash(jpeg) if jpeg is not None else None
        except (OSError, TypeError, ValueError):
            value = None
        hashes[candidate.alias] = value
        return value

    eligible: list[dict[str, Any]] = []
    decisions: list[dict[str, str]] = []
    for proposal in proposals:
        added_aliases = proposal.get("add_asset_ids")
        removed_aliases = proposal.get("remove_asset_ids")
        if (
            not isinstance(added_aliases, list)
            or len(added_aliases) != 1
            or not isinstance(removed_aliases, list)
            or removed_aliases
            or added_aliases[0] not in by_alias
        ):
            eligible.append(proposal)
            continue
        added = by_alias[added_aliases[0]]
        higher_priority = tuple(
            selected
            for selected in current
            if selected.moment_id == added.moment_id
            and _fine_cut_media_priority(selected) > _fine_cut_media_priority(added)
        )
        rejected_against: FineCutCandidate | None = None
        for selected in higher_priority:
            left_hash = thumbnail_hash(added)
            right_hash = thumbnail_hash(selected)
            if left_hash is None or right_hash is None:
                continue
            try:
                distance = hamming_distance(left_hash, right_hash)
            except (TypeError, ValueError):
                continue
            if distance <= SELECTS_MAX_CORROBORATION:
                rejected_against = selected
                break
        if rejected_against is None:
            eligible.append(proposal)
            continue
        representation = "video" if rejected_against.media_kind == "video" else "meaningful motion"
        decisions.append(
            {
                "change_id": str(proposal["change_id"]),
                "verdict": "reject",
                "reason": (
                    "A lower-priority same-moment addition is a perceptual near-duplicate of "
                    f"selected {representation}."
                ),
            }
        )
    return eligible, decisions


def _filter_review_cut_proposals(
    proposals: Sequence[dict[str, Any]],
    *,
    review_cut_aliases: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """Refuse re-adds of assets the whole-sequence review already cut in this run.

    The visual pool keys a change on change_id and the text arm on finding_id; both
    are the identity the downstream decision list is read by.
    """
    review_cut = set(review_cut_aliases)
    if not review_cut:
        return list(proposals), [], []
    eligible: list[dict[str, Any]] = []
    decisions: list[dict[str, str]] = []
    skipped: list[str] = []
    for proposal in proposals:
        added = proposal.get("add_asset_ids")
        resurrected = (
            [alias for alias in added if alias in review_cut] if isinstance(added, list) else []
        )
        if not resurrected:
            eligible.append(proposal)
            continue
        skipped.extend(alias for alias in resurrected if alias not in skipped)
        decisions.append(
            {
                "change_id": str(proposal.get("change_id", proposal.get("finding_id"))),
                "verdict": "reject",
                "reason": (
                    "The global review already cut "
                    f"{', '.join(resurrected)} from this film; a reopened moment may only "
                    "propose its other assets."
                ),
            }
        )
    return eligible, decisions, skipped


def _reconsideration_without_review_cuts(
    proposal: dict[str, Any],
    *,
    wall: Sequence[FineCutCandidate],
    current_aliases: Sequence[str],
    review_cut_aliases: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Re-derive a text delta from only the changes the whole-sequence review has not cut."""
    survivors, decisions, skipped = _filter_review_cut_proposals(
        proposal["changes"],
        review_cut_aliases=review_cut_aliases,
    )
    if not decisions:
        return proposal, None
    added = {alias for change in survivors for alias in change["add_asset_ids"]}
    removed = {alias for change in survivors for alias in change["remove_asset_ids"]}
    keep_set = set(current_aliases) - removed | added
    ordered = [candidate.alias for candidate in wall]
    guarded = {
        **proposal,
        "keep": [alias for alias in ordered if alias in keep_set],
        "added": [alias for alias in ordered if alias in added],
        "removed": [alias for alias in ordered if alias in removed],
        "changes": survivors,
    }
    return guarded, {"skipped_review_cut_assets": skipped, "decisions": decisions}


def _ask_visual_final_pool_request(
    wall: tuple[FineCutCandidate, ...],
    group: dict[str, Any],
    *,
    current_aliases: tuple[str, ...],
    required_aliases: tuple[str, ...],
    capacity: int,
    case: Case,
    thesis: dict[str, Any],
    requester: Any,
    output_dir: Path,
    preview_jpeg: Callable[[str], bytes | None],
    iteration: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Render one bounded tile set, ask it for pool deltas, and read the grounded proposal."""
    by_alias = {candidate.alias: candidate for candidate in wall}
    aliases = tuple(group["asset_ids"])
    tiles = tuple(
        SimpleNamespace(
            entity_id=alias,
            jpeg_bytes=preview_jpeg(by_alias[alias].asset_id),
            unavailable_reason="preview unavailable",
        )
        for alias in aliases
    )
    pages = build_contact_sheets(
        tiles,
        scope_id=(
            f"{case.key}-visual-final-pool-i{iteration:02d}-{str(group['group_id']).lower()}"
        ),
        output_dir=output_dir / "chapter-pools",
        per_sheet=min(len(tiles), AUDIT_MAX_TILES_PER_REQUEST),
        tile_px=180,
    )
    mapping = tuple(
        (reference.number, reference.entity_id) for page in pages for reference in page.tile_refs
    )
    prompt = visual_final_pool_reconsideration_prompt(
        wall,
        current_aliases=current_aliases,
        group=group,
        tile_mapping=mapping,
        editorial_brief=case.brief,
        thesis=thesis,
        capacity=capacity,
        required_aliases=required_aliases,
    )
    answer = requester.ask(
        VisualEditorialRequest(
            pass_name=VISUAL_FINAL_POOL_PASS_NAME,
            pass_version=VISUAL_FINAL_POOL_PASS_VERSION,
            prompt=prompt,
            prompt_version=VISUAL_FINAL_POOL_PROMPT_VERSION,
            schema_version=FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA,
            pages=pages,
            ordered_input_ids=tuple(by_alias[alias].asset_id for alias in aliases),
            ordered_group_ids=(str(group["group_id"]),),
            grounded_annotations=(),
            upstream_material=(
                "reopened-final-candidate-pool",
                f"chapter={group['chapter_id']}",
            ),
            render_version=VISUAL_FINAL_POOL_RENDER_VERSION,
            limits=VisionRequestLimits(
                max_pages_per_request=len(pages),
                max_output_tokens=3000,
                timeout_seconds=timeout_seconds,
            ),
            thinking=False,
            image_detail="low",
        )
    )
    return read_visual_final_pool_reconsideration(
        answer.raw_text,
        wall,
        current_aliases=current_aliases,
        group=group,
        required_aliases=required_aliases,
        capacity=capacity,
    )


def _run_visual_final_pool_reconsideration(
    wall: tuple[FineCutCandidate, ...],
    *,
    current_aliases: tuple[str, ...],
    required_aliases: tuple[str, ...],
    capacity: int,
    case: Case,
    thesis: dict[str, Any],
    chapter_readings: Sequence[dict[str, Any]],
    requester: Any,
    output_dir: Path,
    preview_jpeg: Callable[[str], bytes | None],
    iteration: int,
    timeout_seconds: int,
    cap_removed_aliases: tuple[str, ...] = (),
    reviewed_focus_keys: set[tuple[str, tuple[str, ...]]] | None = None,
    failed_focus_keys: set[tuple[str, tuple[str, ...]]] | None = None,
    review_cut_aliases: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Search each reopened chapter visually, then gate its deltas against the whole cut."""
    by_alias = {candidate.alias: candidate for candidate in wall}
    reviewed = reviewed_focus_keys if reviewed_focus_keys is not None else set()
    failed = failed_focus_keys if failed_focus_keys is not None else set()

    def focus_key(focus: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
        return (
            str(focus.get("focus_kind", "mechanical_focus")),
            tuple(str(moment_id) for moment_id in focus.get("moment_ids", ())),
        )

    review_focus = tuple(
        focus
        for focus in runtime_final_pool_findings(
            wall,
            current_aliases=current_aliases,
            cap_removed_aliases=cap_removed_aliases if iteration == 1 else (),
            chapter_readings=chapter_readings,
        )
        if focus_key(focus) not in reviewed and focus_key(focus) not in failed
    )
    if not review_focus:
        return None
    groups = (
        visual_final_pool_groups(
            wall,
            current_aliases=current_aliases,
            chapter_readings=chapter_readings,
            review_focus=review_focus,
        )
        if review_focus
        else ()
    )
    summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    failed_focus: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for group in groups:
        aliases = tuple(group["asset_ids"])
        requests = visual_final_pool_request_groups(wall, group)
        issued = {alias for request in requests for alias in request["asset_ids"]}
        merged: list[dict[str, Any]] = []
        identities: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        failure = ""
        answered = 0
        for request in requests:
            try:
                proposal = _ask_visual_final_pool_request(
                    wall,
                    request,
                    current_aliases=current_aliases,
                    required_aliases=required_aliases,
                    capacity=capacity,
                    case=case,
                    thesis=thesis,
                    requester=requester,
                    output_dir=output_dir,
                    preview_jpeg=preview_jpeg,
                    iteration=iteration,
                    timeout_seconds=timeout_seconds,
                )
            except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                failure = type(exc).__name__
                warnings.append(f"visual pool group {request['group_id']} failed: {failure}")
                continue
            answered += 1
            for change in proposal["changes"]:
                identity = (
                    tuple(change["add_asset_ids"]),
                    tuple(change["remove_asset_ids"]),
                )
                if identity in identities:
                    continue
                identities.add(identity)
                merged.append(change)
        focus = (
            str(group.get("focus_kind", "mechanical_focus")),
            tuple(str(moment_id) for moment_id in group.get("target_moment_ids", ())),
        )
        if failure:
            # A group that timed out once will time out again on the same tiles; only
            # successful reviews used to be recorded, so the loop re-burned the timeout.
            failed.add(focus)
            failed_focus.append(
                {
                    "group_id": group["group_id"],
                    "focus_kind": focus[0],
                    "moment_ids": list(focus[1]),
                    "error": failure,
                }
            )
        else:
            reviewed.add(focus)
        if not answered:
            continue
        summaries.append(
            {
                "group_id": group["group_id"],
                "chapter_id": group["chapter_id"],
                "asset_count": len(aliases),
                "current_asset_count": len(group["current_asset_ids"]),
                "focus_kind": group.get("focus_kind"),
                "verdict": "revise" if merged else "stable",
                "proposed_changes": len(merged),
                "request_count": len(requests),
                "truncated_tiles": len([alias for alias in aliases if alias not in issued]),
            }
        )
        for change in merged:
            proposals.append(
                {
                    "change_id": f"C{len(proposals) + 1:03d}",
                    "chapter_id": group["chapter_id"],
                    "chapter_label": group["label"],
                    **change,
                }
            )

    stable = {
        "verdict": "stable",
        "keep": list(current_aliases),
        "added": [],
        "removed": [],
        "changes": [],
        "proposals": [],
        "decisions": [],
        "accepted_change_ids": [],
        "groups": summaries,
        "warnings": warnings,
        "failed_focus": failed_focus,
        "skipped_review_cut_assets": [],
    }
    if not proposals:
        return stable

    survivors, review_cut_decisions, skipped_review_cut = _filter_review_cut_proposals(
        proposals,
        review_cut_aliases=review_cut_aliases,
    )
    stable = {**stable, "skipped_review_cut_assets": skipped_review_cut}
    eligible_proposals, near_duplicate_decisions = _filter_lower_priority_near_duplicate_proposals(
        wall,
        current_aliases=current_aliases,
        proposals=survivors,
        preview_jpeg=preview_jpeg,
    )
    mechanical_decisions = [*review_cut_decisions, *near_duplicate_decisions]
    if not eligible_proposals:
        return {
            **stable,
            "proposals": proposals,
            "decisions": mechanical_decisions,
            "proposed_changes": len(proposals),
        }

    affected_chapter_ids = {str(proposal["chapter_id"]) for proposal in eligible_proposals}
    affected_current = {
        alias
        for group in groups
        if str(group["chapter_id"]) in affected_chapter_ids
        for alias in group.get("validation_current_asset_ids", group["current_asset_ids"])
    }
    gate_current_aliases = tuple(alias for alias in current_aliases if alias in affected_current)
    gate_required_aliases = tuple(alias for alias in required_aliases if alias in affected_current)
    gate_capacity = capacity - (len(current_aliases) - len(gate_current_aliases))
    visible_aliases = tuple(
        dict.fromkeys(
            (
                *gate_current_aliases,
                *(alias for proposal in eligible_proposals for alias in proposal["add_asset_ids"]),
            )
        )
    )
    tiles = tuple(
        SimpleNamespace(
            entity_id=alias,
            jpeg_bytes=preview_jpeg(by_alias[alias].asset_id),
            unavailable_reason="preview unavailable",
        )
        for alias in visible_aliases
    )
    proposal_tile_count = len(visible_aliases) - len(gate_current_aliases)

    def page_sizes_for(count: int) -> tuple[int, ...]:
        return tuple(min(80, count - offset) for offset in range(0, count, 80))

    page_sizes = (
        *page_sizes_for(len(gate_current_aliases)),
        *page_sizes_for(proposal_tile_count),
    )
    try:
        pages = build_contact_sheets(
            tiles,
            scope_id=f"{case.key}-visual-final-pool-global-i{iteration:02d}",
            output_dir=output_dir / "global-validation",
            per_sheet=min(len(tiles), 80),
            page_sizes=page_sizes,
            tile_px=250,
        )
        mapping = tuple(
            (reference.number, reference.entity_id)
            for page in pages
            for reference in page.tile_refs
        )
        prompt = visual_final_pool_global_validation_prompt(
            wall,
            current_aliases=gate_current_aliases,
            proposals=eligible_proposals,
            tile_mapping=mapping,
            editorial_brief=case.brief,
            thesis=thesis,
            complete_current_cut=False,
        )
        answer = requester.ask(
            VisualEditorialRequest(
                pass_name=VISUAL_FINAL_POOL_GLOBAL_PASS_NAME,
                pass_version=VISUAL_FINAL_POOL_GLOBAL_PASS_VERSION,
                prompt=prompt,
                prompt_version=VISUAL_FINAL_POOL_GLOBAL_PROMPT_VERSION,
                schema_version=FINAL_VISUAL_POOL_GLOBAL_VALIDATION_SCHEMA,
                pages=pages,
                ordered_input_ids=tuple(by_alias[alias].asset_id for alias in visible_aliases),
                ordered_group_ids=tuple(proposal["change_id"] for proposal in eligible_proposals),
                grounded_annotations=(),
                upstream_material=(
                    "assembled-final-wall-plus-proposed-additions",
                    f"proposal-count={len(eligible_proposals)}",
                ),
                render_version=VISUAL_FINAL_POOL_GLOBAL_RENDER_VERSION,
                limits=VisionRequestLimits(
                    max_pages_per_request=len(pages),
                    max_output_tokens=2500,
                    timeout_seconds=timeout_seconds,
                ),
                thinking=False,
                image_detail="low",
            )
        )
        validation = read_visual_final_pool_global_validation(
            answer.raw_text,
            wall,
            current_aliases=gate_current_aliases,
            proposals=eligible_proposals,
            required_aliases=gate_required_aliases,
            capacity=gate_capacity,
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        warnings.append(f"visual pool global validation failed: {type(exc).__name__}")
        return {
            **stable,
            "proposals": proposals,
            "decisions": mechanical_decisions,
            "warnings": warnings,
            "proposed_changes": len(proposals),
        }

    global_keep = set(current_aliases) - set(validation["removed"]) | set(validation["added"])
    validation = {
        **validation,
        "keep": [candidate.alias for candidate in wall if candidate.alias in global_keep],
    }
    decisions_by_id = {
        row["change_id"]: row for row in (*mechanical_decisions, *validation["decisions"])
    }
    validation = {
        **validation,
        "decisions": [
            decisions_by_id[proposal["change_id"]]
            for proposal in proposals
            if proposal["change_id"] in decisions_by_id
        ],
    }
    accepted = set(validation["accepted_change_ids"])
    accepted_changes = [proposal for proposal in proposals if proposal["change_id"] in accepted]
    return {
        **validation,
        "verdict": "revise" if accepted_changes else "stable",
        "changes": accepted_changes,
        "proposals": proposals,
        "groups": summaries,
        "warnings": warnings,
        "failed_focus": failed_focus,
        "proposed_changes": len(proposals),
        "skipped_review_cut_assets": skipped_review_cut,
    }


async def _iterative_final_asset_review(
    wall: tuple[FineCutCandidate, ...],
    cut: dict[str, Any],
    *,
    case: Case,
    thesis: dict[str, Any],
    capacity: int,
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
    max_iterations: int = 3,
    visual_reconsideration: (Callable[[tuple[str, ...], int], dict[str, Any] | None] | None) = None,
    visual_audit: Callable[[tuple[str, ...], int], dict[str, Any] | None] | None = None,
    review_cut_aliases: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run a bounded evidence-gated deliberation over the selected-moment pools."""
    if not 1 <= max_iterations <= 3:
        raise ValueError("final asset deliberation must use one to three iterations")
    current = tuple(row["asset_id"] for row in cut["keep"])
    if not current:
        deliberation = {
            "max_iterations": max_iterations,
            "stop_reason": "not-needed",
            "iterations": [],
        }
        return {**cut, "deliberation": deliberation}, deliberation
    required = tuple(cut.get("required_asset_ids", ()))
    reason_by_alias = {row["asset_id"]: row["reason"] for row in cut["keep"]}
    seen = {frozenset(current)}
    iterations: list[dict[str, Any]] = []
    stop_reason = "cap"
    recoverable = (OSError, RuntimeError, TimeoutError, TypeError, ValueError)

    for index in range(1, max_iterations + 1):
        input_aliases = current
        calls: dict[str, Any] = {}
        if visual_reconsideration is not None:
            try:
                visual_pool = visual_reconsideration(current, index)
                if visual_pool is not None:
                    calls["visual_pool"] = {
                        "verdict": visual_pool.get("verdict"),
                        "groups": visual_pool.get("groups", ()),
                        "proposed_changes": visual_pool.get("proposed_changes", 0),
                        "accepted_change_ids": visual_pool.get("accepted_change_ids", ()),
                        "proposals": visual_pool.get("proposals", ()),
                        "decisions": visual_pool.get("decisions", ()),
                        "overall_reason": visual_pool.get("overall_reason"),
                        "warnings": visual_pool.get("warnings", ()),
                        "failed_focus": visual_pool.get("failed_focus", ()),
                        "global_envelope_repaired": visual_pool.get("repaired_envelope", False),
                    }
                    if visual_pool.get("verdict") == "revise":
                        proposed_rows = visual_pool.get("keep")
                        if (
                            not isinstance(proposed_rows, list)
                            or not proposed_rows
                            or any(not isinstance(alias, str) for alias in proposed_rows)
                            or len(set(proposed_rows)) != len(proposed_rows)
                            or not set(required) <= set(proposed_rows)
                            or not set(proposed_rows) <= {candidate.alias for candidate in wall}
                            or len(proposed_rows) > capacity
                        ):
                            raise ValueError("visual pool returned an invalid final set")
                        proposed = tuple(proposed_rows)
                        if proposed != current:
                            if frozenset(proposed) in seen:
                                iterations.append(
                                    {
                                        "index": index,
                                        "input_aliases": list(input_aliases),
                                        "audit": None,
                                        "proposal_aliases": list(proposed),
                                        "added": visual_pool.get("added", ()),
                                        "removed": visual_pool.get("removed", ()),
                                        "validation": {
                                            "verdict": "accept",
                                            "source": "visual-pool-global-validation",
                                        },
                                        "outcome": "unchanged",
                                        "output_aliases": list(current),
                                        "calls": calls,
                                    }
                                )
                                stop_reason = "cycle"
                                break
                            for change in visual_pool.get("changes", ()):
                                if not isinstance(change, dict):
                                    continue
                                reason = change.get("reason")
                                for alias in change.get("add_asset_ids", ()):
                                    if isinstance(alias, str) and isinstance(reason, str):
                                        reason_by_alias[alias] = reason
                            current = proposed
                            seen.add(frozenset(current))
                            iterations.append(
                                {
                                    "index": index,
                                    "input_aliases": list(input_aliases),
                                    "audit": None,
                                    "proposal_aliases": list(proposed),
                                    "added": visual_pool.get("added", ()),
                                    "removed": visual_pool.get("removed", ()),
                                    "validation": {
                                        "verdict": "accept",
                                        "source": "visual-pool-global-validation",
                                        "accepted_change_ids": visual_pool.get(
                                            "accepted_change_ids", ()
                                        ),
                                    },
                                    "outcome": "accepted",
                                    "output_aliases": list(current),
                                    "calls": calls,
                                }
                            )
                            continue
            except recoverable as exc:
                calls["visual_pool_warning"] = (
                    f"visual pool reconsideration failed: {type(exc).__name__}"
                )
        visual_source: dict[str, Any] | None = None
        if visual_audit is not None:
            try:
                visual_source = visual_audit(current, index)
                if visual_source is not None:
                    calls["visual_audit"] = {
                        "verdict": visual_source.get("verdict"),
                        "findings": len(visual_source.get("findings", ())),
                        "groups": visual_source.get("groups", ()),
                    }
            except recoverable as exc:
                calls["visual_audit_warning"] = f"visual audit failed: {type(exc).__name__}"
        try:
            runtime_focus = runtime_final_asset_audit_findings(
                wall,
                current_aliases=current,
            )
            audit_prompt = final_asset_audit_prompt(
                wall,
                current_aliases=current,
                editorial_brief=case.brief,
                thesis=thesis,
                review_focus=runtime_focus,
            )
            audit_call = await _ask_text(
                audit_prompt,
                llm_config=llm_config,
                cache_path=cache_path,
                max_tokens=2500,
                timeout_seconds=timeout_seconds,
                thinking=False,
            )
            calls["audit"] = _call_record(audit_call)
            text_audit = read_final_asset_audit(
                audit_call.raw,
                wall,
                current_aliases=current,
            )
            audit = (
                merge_final_asset_audits((text_audit, visual_source))
                if visual_source is not None
                else text_audit
            )
            if runtime_focus:
                audit = {**audit, "runtime_focus": runtime_focus}
        except recoverable as exc:
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": None,
                    "proposal_aliases": list(current),
                    "added": [],
                    "removed": [],
                    "validation": None,
                    "outcome": "unchanged",
                    "warning": f"audit failed: {type(exc).__name__}",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "invalid"
            break
        if audit["verdict"] == "stable":
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": audit,
                    "proposal_aliases": list(current),
                    "added": [],
                    "removed": [],
                    "validation": None,
                    "outcome": "unchanged",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "stable"
            break
        try:
            proposal_prompt = final_asset_reconsideration_prompt(
                wall,
                current_aliases=current,
                required_aliases=required,
                capacity=capacity,
                audit=audit,
                editorial_brief=case.brief,
                thesis=thesis,
            )
            proposal_call = await _ask_text(
                proposal_prompt,
                llm_config=llm_config,
                cache_path=cache_path,
                max_tokens=3000,
                timeout_seconds=timeout_seconds,
                thinking=False,
            )
            calls["reconsideration"] = _call_record(proposal_call)
            proposal = read_final_asset_reconsideration(
                proposal_call.raw,
                wall,
                current_aliases=current,
                required_aliases=required,
                capacity=capacity,
                audit=audit,
            )
            proposal, review_cut_guard = _reconsideration_without_review_cuts(
                proposal,
                wall=wall,
                current_aliases=current,
                review_cut_aliases=review_cut_aliases,
            )
            if review_cut_guard is not None:
                calls["reconsideration_review_cut"] = review_cut_guard
        except recoverable as exc:
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": audit,
                    "proposal_aliases": list(current),
                    "added": [],
                    "removed": [],
                    "validation": None,
                    "outcome": "unchanged",
                    "warning": f"reconsideration failed: {type(exc).__name__}",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "invalid"
            break
        proposed = tuple(proposal["keep"])
        if proposed == current:
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": audit,
                    "proposal_aliases": list(proposed),
                    "added": [],
                    "removed": [],
                    "validation": None,
                    "outcome": "unchanged",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "converged"
            break
        if frozenset(proposed) in seen:
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": audit,
                    "proposal_aliases": list(proposed),
                    "added": proposal["added"],
                    "removed": proposal["removed"],
                    "validation": None,
                    "outcome": "unchanged",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "cycle"
            break
        try:
            validation_prompt = final_asset_delta_validation_prompt(
                wall,
                before_aliases=current,
                proposal=proposal,
                audit=audit,
            )
            validation_call = await _ask_text(
                validation_prompt,
                llm_config=llm_config,
                cache_path=cache_path,
                max_tokens=1800,
                timeout_seconds=timeout_seconds,
                thinking=False,
            )
            calls["validation"] = _call_record(validation_call)
            validation = read_final_asset_delta_validation(
                validation_call.raw,
                audit=audit,
                proposal=proposal,
            )
        except recoverable as exc:
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": audit,
                    "proposal_aliases": list(proposed),
                    "added": proposal["added"],
                    "removed": proposal["removed"],
                    "validation": None,
                    "outcome": "unchanged",
                    "warning": f"validation failed: {type(exc).__name__}",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "invalid"
            break
        if validation["verdict"] != "accept":
            iterations.append(
                {
                    "index": index,
                    "input_aliases": list(input_aliases),
                    "audit": audit,
                    "proposal_aliases": list(proposed),
                    "added": proposal["added"],
                    "removed": proposal["removed"],
                    "validation": validation,
                    "outcome": "rejected",
                    "output_aliases": list(current),
                    "calls": calls,
                }
            )
            stop_reason = "rejected"
            break
        for change in proposal["changes"]:
            for alias in change["add_asset_ids"]:
                reason_by_alias[alias] = change["reason"]
        current = proposed
        seen.add(frozenset(current))
        iterations.append(
            {
                "index": index,
                "input_aliases": list(input_aliases),
                "audit": audit,
                "proposal_aliases": list(proposed),
                "added": proposal["added"],
                "removed": proposal["removed"],
                "validation": validation,
                "outcome": "accepted",
                "output_aliases": list(current),
                "calls": calls,
            }
        )

    current_set = set(current)
    reviewed = {
        **cut,
        "pre_deliberation_keep": cut["keep"],
        "keep": [
            {
                "asset_id": candidate.alias,
                "reason": reason_by_alias.get(
                    candidate.alias, "Admitted by grounded corpus reconsideration."
                ),
            }
            for candidate in wall
            if candidate.alias in current_set
        ],
    }
    deliberation = {
        "max_iterations": max_iterations,
        "stop_reason": stop_reason,
        "iterations": iterations,
    }
    return {**reviewed, "deliberation": deliberation}, deliberation


def _needs_post_deliberation_text_global_review(deliberation: dict[str, Any]) -> bool:
    """Only text-originated accepted deltas need another text-only sequence reduction."""
    for row in deliberation.get("iterations", ()):
        if not isinstance(row, dict) or row.get("outcome") != "accepted":
            continue
        validation = row.get("validation")
        if (
            not isinstance(validation, dict)
            or validation.get("source") != "visual-pool-global-validation"
        ):
            return True
    return False


def _deliberation_added_assets(deliberation: dict[str, Any]) -> bool:
    """Return whether an accepted refinement admitted any new alias."""
    for row in deliberation.get("iterations", ()):
        if not isinstance(row, dict) or row.get("outcome") != "accepted":
            continue
        input_aliases = row.get("input_aliases")
        output_aliases = row.get("output_aliases")
        if not isinstance(input_aliases, list) or not isinstance(output_aliases, list):
            continue
        if set(output_aliases) - set(input_aliases):
            return True
    return False


SSCD_SIDE = 320
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def _sscd_copy_embedder(model_path: Path) -> CopyEmbedder:
    """SSCD disc_mixup embeddings for one preview, loaded on first use.

    Same recipe measured in probe_pairhead_specialist_instruments.py: square
    resize to 320 (the batching preprocessing the SSCD README recommends) and
    ImageNet normalisation. Loading lazily keeps a missing checkpoint a
    nomination that never happens rather than a run that never starts.
    """
    loaded: list[Any] = []

    def _embed(jpeg_bytes: bytes) -> Sequence[float]:
        import numpy as np
        import torch
        from PIL import Image

        if not loaded:
            loaded.append(torch.jit.load(str(model_path)).eval())
        with Image.open(io.BytesIO(jpeg_bytes)) as handle:
            image = handle.convert("RGB").resize((SSCD_SIDE, SSCD_SIDE), Image.Resampling.BILINEAR)
        array = np.asarray(image, dtype=np.float32) / 255.0
        chw = (array - np.array(IMAGENET_MEAN, np.float32)) / np.array(IMAGENET_STD, np.float32)
        with torch.no_grad():
            vector = loaded[0](torch.from_numpy(chw.transpose(2, 0, 1)[None]))
        return [float(value) for value in vector[0].tolist()]

    return _embed


def _apply_final_duplicate_review(
    cut: dict[str, Any],
    *,
    wall: tuple[FineCutCandidate, ...],
    review: FinalDuplicateReview,
    pre_keep_key: str = "pre_duplicate_review_keep",
    review_key: str = "duplicate_review",
) -> tuple[dict[str, Any], set[str]]:
    """Translate the production duplicate verdict back to private wall aliases."""
    alias_by_asset = {candidate.asset_id: candidate.alias for candidate in wall}
    survivor_assets = {candidate.asset_id for candidate in review.survivors}
    selected_aliases = {alias_by_asset[asset_id] for asset_id in survivor_assets}
    return (
        {
            **cut,
            pre_keep_key: cut["keep"],
            "keep": [row for row in cut["keep"] if row["asset_id"] in selected_aliases],
            review_key: {
                "survivor_asset_ids": [candidate.asset_id for candidate in review.survivors],
                "nominations": [asdict(row) for row in review.nominations],
                "decisions": [asdict(row) for row in review.decisions],
                "absorbed": [asdict(row) for row in review.absorbed],
                "warnings": list(review.warnings),
            },
        },
        selected_aliases,
    )


def _final_asset_cut_repair_prompt(original_prompt: str, raw: str, error: str) -> str:
    return f"""{original_prompt}

Your previous JSON failed validation: {error}.

PREVIOUS JSON
{raw}

Return one corrected complete JSON object in the exact requested schema. Preserve every grounded
choice you can. Every asset_id, kept_asset_id and rejected_asset_id must be copied exactly from the
ASSET WALL in this prompt. Do not use an asset from another chapter and do not add commentary outside
the JSON. If any kept asset belongs to a moment containing a FAVOURITE row, keep at least one
FAVOURITE asset from that moment within capacity or drop that moment entirely."""


def _restore_global_final_cut_aliases(
    cut: dict[str, Any], global_by_local: dict[str, str]
) -> dict[str, Any]:
    """Translate chapter-local prompt handles back to the chronological global wall."""
    return {
        **cut,
        "keep": [{**row, "asset_id": global_by_local[row["asset_id"]]} for row in cut["keep"]],
        "required_asset_ids": [global_by_local[alias] for alias in cut["required_asset_ids"]],
        "comparisons": [
            {
                **row,
                "kept_asset_id": global_by_local[row["kept_asset_id"]],
                "rejected_asset_id": global_by_local[row["rejected_asset_id"]],
            }
            for row in cut["comparisons"]
        ],
    }


def _run_final_refinement(
    *,
    client: SyncImmichClient,
    upstream: Any,
    workprint: DescriptionWorkprint,
    descriptions: tuple[prototype.Description, ...],
    text_result: dict[str, Any],
    facts: dict[str, PersonFact],
    case: Case,
    config: Any,
    thumbnail_cache: ThumbnailCache,
    args: argparse.Namespace,
    out: Path,
) -> dict[str, Any]:
    """Open retained reservoirs, deduplicate them, then make the real asset cut."""
    started = time.monotonic()
    selected_groups = set(text_result["selected_production_group_ids"])
    reservoirs = tuple(
        moment for moment in workprint.reservoir_moments if moment.moment_id in selected_groups
    )
    reservoir_candidates = tuple(
        candidate for moment in reservoirs for candidate in moment.candidates
    )

    motion_source_started = time.monotonic()
    final_atlas, motion_source_stats = _final_refinement_atlas(
        client=client,
        config=config,
        prepared=upstream.prepared,
        candidates=reservoir_candidates,
        frame_cache_dir=config.cache.cache_path / "editorial-frames",
    )
    motion_source_seconds = time.monotonic() - motion_source_started

    vision_llm = config.llm.model_copy(update={"model": args.vision_model})
    early_motion_candidates = _observed_motion_candidates(
        reservoir_candidates,
        atlas=final_atlas,
    )
    motion_description_started = time.monotonic()
    early_motion_descriptions: tuple[Any, ...] = ()
    if early_motion_candidates:
        motion_subset = _description_subset(upstream.prepared, early_motion_candidates)
        motion_description_gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=vision_llm,
                cache_path=verdicts_beside(config.cache.cache_path),
                trace=upstream.prepared.trace,
            ),
            expected_tiles={"asset-description": len(early_motion_candidates)},
        )
        try:
            motion_description_result = describe_editorial_assets(
                motion_subset,
                requester=motion_description_gateway,
                output_dir=out / "motion-description-sheets",
                frame_cache_dir=config.cache.cache_path / "editorial-frames",
                atlas=final_atlas,
                concurrency=args.concurrency,
            )
            early_motion_descriptions = motion_description_result.descriptions
        finally:
            motion_description_gateway.close()
    motion_description_seconds = time.monotonic() - motion_description_started
    early_motion_by_id = {
        description.asset_id: description for description in early_motion_descriptions
    }
    motion_contributions: dict[str, str] = {
        description.asset_id: description.motion_contribution
        for description in early_motion_descriptions
        if description.motion_contribution != "not_observed"
    }
    motion_reasons = {
        description.asset_id: description.motion_reason
        for description in early_motion_descriptions
        if description.motion_reason is not None
    }

    selects_started = time.monotonic()
    upstream_model = args.upstream_model or config.llm.model
    selects_gateway = _ProgressGateway(
        VisualEditorialGateway(
            llm_config=config.llm.model_copy(update={"model": upstream_model}),
            cache_path=verdicts_beside(config.cache.cache_path),
            trace=upstream.prepared.trace,
        )
    )
    try:
        refined = run_selects(
            upstream.prepared,
            reservoir_candidates,
            requester=selects_gateway,
            sheet_output_dir=out / "final-selects-sheets",
            frame_cache_dir=config.cache.cache_path / "editorial-frames",
            concurrency=args.concurrency,
            atlas=final_atlas,
            motion_contributions=motion_contributions,
        )
    finally:
        selects_gateway.close()
    selects_seconds = time.monotonic() - selects_started

    required_assets = _required_fine_cut_ids(
        reservoir_candidates,
        reservoirs=reservoirs,
        text_result=text_result,
    )
    fine_cut_survivors = _restore_required_fine_cut_candidates(
        refined.survivors,
        reservoir_candidates=reservoir_candidates,
        required_asset_ids=required_assets,
    )
    restored_required = len(fine_cut_survivors) - len(refined.survivors)

    remaining_description_candidates = tuple(
        candidate
        for candidate in fine_cut_survivors
        if candidate.asset_id not in early_motion_by_id
    )
    description_started = time.monotonic()
    late_descriptions: tuple[Any, ...] = ()
    if remaining_description_candidates:
        refined_subset = _description_subset(
            upstream.prepared,
            remaining_description_candidates,
        )
        description_gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=vision_llm,
                cache_path=verdicts_beside(config.cache.cache_path),
                trace=upstream.prepared.trace,
            ),
            expected_tiles={"asset-description": len(remaining_description_candidates)},
        )
        try:
            refined_description_result = describe_editorial_assets(
                refined_subset,
                requester=description_gateway,
                output_dir=out / "final-description-sheets",
                frame_cache_dir=config.cache.cache_path / "editorial-frames",
                atlas=final_atlas,
                concurrency=args.concurrency,
            )
            late_descriptions = refined_description_result.descriptions
        finally:
            description_gateway.close()
    description_seconds = time.monotonic() - description_started
    refined_by_id = {
        description.asset_id: description
        for description in (*early_motion_descriptions, *late_descriptions)
    }
    refined_descriptions = tuple(
        refined_by_id[candidate.asset_id]
        for candidate in fine_cut_survivors
        if candidate.asset_id in refined_by_id
    )
    description_by_id = {description.asset_id: description.text for description in descriptions}
    description_by_id.update(
        {description.asset_id: description.text for description in refined_descriptions}
    )

    wall = _fine_cut_candidates(
        fine_cut_survivors,
        reservoirs=reservoirs,
        descriptions=description_by_id,
        text_result=text_result,
        facts=facts,
        atlas=final_atlas,
        motion_contributions=motion_contributions,
        motion_reasons=motion_reasons,
    )
    wall = _with_preview_luminance(
        wall,
        preview_jpeg=lambda asset_id: cached_preview_bytes(thumbnail_cache, asset_id),
    )
    alias_by_asset = {candidate.asset_id: candidate.alias for candidate in wall}
    required_aliases = tuple(alias_by_asset[asset_id] for asset_id in required_assets)
    capacity = int(text_result["configuration"]["capacity"]["moment_capacity"])

    cut_started = time.monotonic()
    cut: dict[str, Any]
    cut_calls: tuple[TextCall, ...] = ()
    selected_aliases: set[str]
    if not _needs_optional_asset_cut(
        wall,
        required_aliases=required_aliases,
        capacity=capacity,
    ):
        selected_aliases = set(required_aliases)
        cut = {
            "keep": [
                {
                    "asset_id": candidate.alias,
                    "reason": "Admitted by the runtime before the optional asset cut.",
                }
                for candidate in wall
                if candidate.alias in selected_aliases
            ],
            "required_asset_ids": list(required_aliases),
            "discarded_required_echoes": 0,
            "discarded_duplicate_keeps": 0,
            "comparisons": [],
            "discarded_comparisons": 0,
            "overall_reason": "Runtime obligations consume every available candidate or slot.",
        }
    elif text_result["configuration"]["shape"] == "hierarchical":
        plans = _hierarchical_final_cut_plan(
            text_result,
            wall,
            required_aliases=required_aliases,
            capacity=capacity,
        )
        print(
            f"{case.key}: final asset cut split across {len(plans)} chronological chapters "
            f"for {sum(plan['capacity'] for plan in plans)}/{capacity} allocated slots",
            flush=True,
        )
        text_llm = config.llm.model_copy(update={"model": args.text_model})
        cut, cut_calls = asyncio.run(
            _hierarchical_final_asset_cut(
                plans,
                case=case,
                thesis=text_result["thesis"],
                llm_config=text_llm,
                cache_path=args.text_cache or out.parent / "text-judgments.db",
                concurrency=args.editorial_concurrency,
                timeout_seconds=args.timeout_seconds,
            )
        )
        selected_aliases = {row["asset_id"] for row in cut["keep"]}
    else:
        prompt = final_asset_cut_prompt(
            wall,
            memory_label=case.label,
            memory_type=case.product,
            editorial_brief=case.brief,
            thesis=text_result["thesis"],
            capacity=capacity,
            required_aliases=required_aliases,
        )
        text_llm = config.llm.model_copy(update={"model": args.text_model})
        cut_call = asyncio.run(
            _ask_text(
                prompt,
                llm_config=text_llm,
                cache_path=args.text_cache or out.parent / "text-judgments.db",
                max_tokens=4000,
                timeout_seconds=args.timeout_seconds,
                thinking=args.editorial_thinking,
            )
        )
        cut_calls = (cut_call,)
        cut = read_final_asset_cut(
            cut_call.raw,
            wall,
            capacity=capacity,
            required_aliases=required_aliases,
            project_favourites=True,
        )
        selected_aliases = {row["asset_id"] for row in cut["keep"]}
    cut = apply_final_moment_cap(wall, cut, max_per_moment=2)
    cut = apply_closer_luminance_swap(wall, cut)
    selected_aliases = {row["asset_id"] for row in cut["keep"]}
    # Every downstream wall reads the memory's closing row from the wall itself.
    wall = mark_closing_candidate(wall, kept_aliases=tuple(sorted(selected_aliases)))
    cut_seconds = time.monotonic() - cut_started
    copy_embedder = _sscd_copy_embedder(args.sscd_model) if args.sscd_model else None

    def review_selected_duplicates(
        aliases: set[str], *, sheet_output_dir: Path
    ) -> FinalDuplicateReview:
        winner_asset_ids = {candidate.asset_id for candidate in wall if candidate.alias in aliases}
        winner_candidates = tuple(
            candidate for candidate in fine_cut_survivors if candidate.asset_id in winner_asset_ids
        )
        media_priorities = {
            candidate.asset_id: (
                2
                if candidate.media_kind == "video"
                else 1
                if candidate.media_kind == "live-motion"
                and candidate.motion_contribution == "meaningful"
                else 0
            )
            for candidate in wall
            if candidate.alias in aliases
        }
        duplicate_gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=config.llm.model_copy(update={"model": upstream_model}),
                cache_path=verdicts_beside(config.cache.cache_path),
                trace=upstream.prepared.trace,
            )
        )
        try:
            return review_final_duplicates(
                winner_candidates,
                descriptions=description_by_id,
                atlas=final_atlas,
                requester=duplicate_gateway,
                sheet_output_dir=sheet_output_dir,
                required_asset_ids=required_assets,
                concurrency=args.concurrency,
                media_priorities=media_priorities,
                copy_embedder=copy_embedder,
            )
        finally:
            duplicate_gateway.close()

    pre_duplicate_review_assets = len(selected_aliases)
    duplicate_review_started = time.monotonic()
    duplicate_review: FinalDuplicateReview | None = None
    if selected_aliases:
        duplicate_review = review_selected_duplicates(
            selected_aliases,
            sheet_output_dir=out / "final-duplicate-sheets",
        )
        cut, selected_aliases = _apply_final_duplicate_review(
            cut,
            wall=wall,
            review=duplicate_review,
        )
        favourite_by_asset = {
            candidate.asset_id: candidate.favourite for candidate in duplicate_review.survivors
        }
        wall = tuple(
            replace(
                candidate,
                favourite=favourite_by_asset.get(candidate.asset_id, candidate.favourite),
            )
            for candidate in wall
        )
    duplicate_review_seconds = time.monotonic() - duplicate_review_started

    pre_global_review_assets = len(selected_aliases)
    initial_global_review_started = time.monotonic()
    initial_global_review_call: TextCall | None = None
    initial_global_review: dict[str, Any] | None = None
    if selected_aliases:
        text_llm = config.llm.model_copy(update={"model": args.text_model})
        cut, initial_global_review, initial_global_review_call = asyncio.run(
            _global_final_sequence_review(
                wall,
                cut,
                case=case,
                thesis=text_result["thesis"],
                llm_config=text_llm,
                cache_path=args.text_cache or out.parent / "text-judgments.db",
                timeout_seconds=args.timeout_seconds,
            )
        )
        selected_aliases = {row["asset_id"] for row in cut["keep"]}
    initial_global_review_seconds = time.monotonic() - initial_global_review_started

    pre_deliberation_assets = len(selected_aliases)
    deliberation_started = time.monotonic()
    max_refinement_iterations = getattr(args, "final_refinement_iterations", 3)
    deliberation: dict[str, Any] = {
        "max_iterations": max_refinement_iterations,
        "stop_reason": "not-needed",
        "iterations": [],
    }
    if selected_aliases:
        text_llm = config.llm.model_copy(update={"model": args.text_model})
        reviewed_pool_focus_keys: set[tuple[str, tuple[str, ...]]] = set()
        failed_pool_focus_keys: set[tuple[str, tuple[str, ...]]] = set()
        review_cut_aliases = tuple(
            str(row["asset_id"])
            for row in (initial_global_review or {}).get("cut", ())
            if isinstance(row, dict) and isinstance(row.get("asset_id"), str)
        )
        # The whole-sequence review may have cut the row that closed the memory.
        wall = mark_closing_candidate(wall, kept_aliases=tuple(sorted(selected_aliases)))
        visual_pool_gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=vision_llm,
                cache_path=verdicts_beside(config.cache.cache_path),
                trace=upstream.prepared.trace,
            )
        )
        try:
            cut, deliberation = asyncio.run(
                _iterative_final_asset_review(
                    wall,
                    cut,
                    case=case,
                    thesis=text_result["thesis"],
                    capacity=capacity,
                    llm_config=text_llm,
                    cache_path=args.text_cache or out.parent / "text-judgments.db",
                    timeout_seconds=args.timeout_seconds,
                    max_iterations=max_refinement_iterations,
                    visual_reconsideration=lambda aliases, iteration: (
                        _run_visual_final_pool_reconsideration(
                            wall,
                            current_aliases=aliases,
                            required_aliases=required_aliases,
                            capacity=capacity,
                            case=case,
                            thesis=text_result["thesis"],
                            chapter_readings=tuple(text_result.get("chapter_readings", ())),
                            requester=visual_pool_gateway,
                            output_dir=out / "visual-final-pool-sheets",
                            preview_jpeg=lambda asset_id: cached_preview_bytes(
                                thumbnail_cache, asset_id
                            ),
                            iteration=iteration,
                            timeout_seconds=args.timeout_seconds,
                            cap_removed_aliases=tuple(
                                cut.get("moment_cap", {}).get("removed_asset_ids", ())
                            ),
                            reviewed_focus_keys=reviewed_pool_focus_keys,
                            failed_focus_keys=failed_pool_focus_keys,
                            review_cut_aliases=review_cut_aliases,
                        )
                    ),
                    review_cut_aliases=review_cut_aliases,
                )
            )
        finally:
            visual_pool_gateway.close()
        selected_aliases = {row["asset_id"] for row in cut["keep"]}
    deliberation_seconds = time.monotonic() - deliberation_started
    deliberation_changed = any(
        row.get("outcome") == "accepted" for row in deliberation["iterations"]
    )
    needs_text_global_review = _needs_post_deliberation_text_global_review(deliberation)

    final_global_review_started = time.monotonic()
    final_global_review_call = initial_global_review_call
    if selected_aliases and deliberation_changed and needs_text_global_review:
        pre_initial_global_review_keep = cut.get("pre_global_review_keep")
        cut, _final_global_review, final_global_review_call = asyncio.run(
            _global_final_sequence_review(
                wall,
                cut,
                case=case,
                thesis=text_result["thesis"],
                llm_config=text_llm,
                cache_path=args.text_cache or out.parent / "text-judgments.db",
                timeout_seconds=args.timeout_seconds,
            )
        )
        cut = {
            **cut,
            "pre_initial_global_review_keep": pre_initial_global_review_keep,
            "initial_global_review": initial_global_review,
        }
        selected_aliases = {row["asset_id"] for row in cut["keep"]}
    elif selected_aliases and deliberation_changed:
        cut = {
            **cut,
            "pre_initial_global_review_keep": cut.get("pre_global_review_keep"),
            "initial_global_review": initial_global_review,
            "global_review": {
                "status": "approved-by-visual-pool",
                "keep": [row["asset_id"] for row in cut["keep"]],
                "cut": [],
                "overall_reason": (
                    "Every accepted delta already passed whole-wall visual validation."
                ),
            },
        }
    final_global_review_seconds = time.monotonic() - final_global_review_started
    global_review_seconds = initial_global_review_seconds + final_global_review_seconds

    post_deliberation_duplicate_review: FinalDuplicateReview | None = None
    post_deliberation_pre_duplicate_review_assets = 0
    post_deliberation_duplicate_review_started = time.monotonic()
    if selected_aliases and _deliberation_added_assets(deliberation):
        post_deliberation_pre_duplicate_review_assets = len(selected_aliases)
        post_deliberation_duplicate_review = review_selected_duplicates(
            selected_aliases,
            sheet_output_dir=out / "post-deliberation-duplicate-sheets",
        )
        cut, selected_aliases = _apply_final_duplicate_review(
            cut,
            wall=wall,
            review=post_deliberation_duplicate_review,
            pre_keep_key="pre_post_deliberation_duplicate_review_keep",
            review_key="post_deliberation_duplicate_review",
        )
        favourite_by_asset = {
            candidate.asset_id: candidate.favourite
            for candidate in post_deliberation_duplicate_review.survivors
        }
        wall = tuple(
            replace(
                candidate,
                favourite=favourite_by_asset.get(candidate.asset_id, candidate.favourite),
            )
            for candidate in wall
        )
    post_deliberation_duplicate_review_seconds = (
        time.monotonic() - post_deliberation_duplicate_review_started
    )
    duplicate_review_seconds += post_deliberation_duplicate_review_seconds
    duplicate_reviews = tuple(
        review
        for review in (duplicate_review, post_deliberation_duplicate_review)
        if review is not None
    )

    selected_moments = {
        candidate.moment_id for candidate in wall if candidate.alias in selected_aliases
    }
    result = {
        "configuration": {
            "capacity": capacity,
            "expected_seconds_per_visual": 4.0,
            "thinking": args.editorial_thinking,
            "shape": text_result["configuration"]["shape"],
        },
        "counts": {
            "selected_moment_reservoirs": len(reservoirs),
            "reservoir_assets": len(reservoir_candidates),
            "after_demand_selects": len(refined.survivors),
            "selects_absorbed": len(refined.absorbed),
            "restored_required_after_selects": restored_required,
            "fine_cut_candidates": len(fine_cut_survivors),
            "pre_global_review_assets": pre_global_review_assets,
            "pre_deliberation_assets": pre_deliberation_assets,
            "post_deliberation_assets": len(
                deliberation["iterations"][-1]["output_aliases"]
                if deliberation["iterations"]
                else selected_aliases
            ),
            "deliberation_iterations": len(deliberation["iterations"]),
            "visual_audit_groups": sum(
                len(row.get("calls", {}).get("visual_audit", {}).get("groups", ()))
                for row in deliberation["iterations"]
            ),
            "visual_audit_findings": sum(
                int(row.get("calls", {}).get("visual_audit", {}).get("findings", 0))
                for row in deliberation["iterations"]
            ),
            "visual_pool_groups": sum(
                len(row.get("calls", {}).get("visual_pool", {}).get("groups", ()))
                for row in deliberation["iterations"]
            ),
            "visual_pool_proposals": sum(
                int(row.get("calls", {}).get("visual_pool", {}).get("proposed_changes", 0))
                for row in deliberation["iterations"]
            ),
            "visual_pool_accepted_changes": sum(
                len(row.get("calls", {}).get("visual_pool", {}).get("accepted_change_ids", ()))
                for row in deliberation["iterations"]
            ),
            "pre_duplicate_review_assets": pre_duplicate_review_assets,
            "post_deliberation_pre_duplicate_review_assets": (
                post_deliberation_pre_duplicate_review_assets
            ),
            "duplicate_nominations": sum(len(review.nominations) for review in duplicate_reviews),
            "duplicates_confirmed": sum(
                decision.same for review in duplicate_reviews for decision in review.decisions
            ),
            "duplicates_absorbed": sum(len(review.absorbed) for review in duplicate_reviews),
            "post_deliberation_duplicates_absorbed": (
                len(post_deliberation_duplicate_review.absorbed)
                if post_deliberation_duplicate_review is not None
                else 0
            ),
            "selected_assets": len(selected_aliases),
            "represented_moments": len(selected_moments),
            "required_assets": len(required_aliases),
            "description_cache_hits": sum(
                item.provenance.cache_hit for item in refined_descriptions
            ),
            "motion_assets_observed_before_selects": len(early_motion_candidates),
            "motion_assets_described_before_selects": len(early_motion_descriptions),
            "motion_description_cache_hits": sum(
                item.provenance.cache_hit for item in early_motion_descriptions
            ),
            **motion_source_stats,
        },
        "timings": {
            "demand_selects_seconds": selects_seconds,
            "motion_source_seconds": motion_source_seconds,
            "motion_descriptions_seconds": motion_description_seconds,
            "post_select_descriptions_seconds": description_seconds,
            "reservoir_descriptions_seconds": (motion_description_seconds + description_seconds),
            "asset_cut_seconds": cut_seconds,
            "global_review_seconds": global_review_seconds,
            "initial_global_review_seconds": initial_global_review_seconds,
            "deliberation_seconds": deliberation_seconds,
            "final_global_review_seconds": final_global_review_seconds,
            "duplicate_review_seconds": duplicate_review_seconds,
            "post_deliberation_duplicate_review_seconds": (
                post_deliberation_duplicate_review_seconds
            ),
            "total_seconds": time.monotonic() - started,
        },
        "selection": cut,
        "selection_call": _call_record(cut_calls[0]) if len(cut_calls) == 1 else None,
        "selection_calls": [_call_record(call) for call in cut_calls],
        "global_review_call": _call_record(final_global_review_call),
        "global_review_calls": [
            record
            for call in (initial_global_review_call, final_global_review_call)
            if (record := _call_record(call)) is not None
        ][: 1 if final_global_review_call is initial_global_review_call else 2],
        "assets": [
            {
                "alias": candidate.alias,
                "asset_id": candidate.asset_id,
                "moment_id": candidate.moment_id,
                "taken_at": candidate.taken_at.isoformat(),
                "media_kind": candidate.media_kind,
                "favourite": candidate.favourite,
                "description": candidate.description,
                "context": list(candidate.context),
                "episode_id": candidate.episode_id,
                "people_context": list(candidate.people_context),
                "motion_contribution": candidate.motion_contribution,
                "motion_reason": candidate.motion_reason,
                "source_media_kind": candidate.source_media_kind,
                "motion_observed": candidate.motion_observed,
                "render_mode": candidate.render_mode,
                "render_frame_seconds": candidate.render_frame_seconds,
                "selected": candidate.alias in selected_aliases,
            }
            for candidate in wall
        ],
    }
    _atomic_json(
        out / "final-cut.json",
        {
            "privacy": "private final asset selection; do not commit",
            **result,
        },
    )
    print(
        f"{case.key}: final cut opened {len(reservoir_candidates)} reservoir assets -> "
        f"{len(refined.survivors)} after demand Selects -> {len(selected_aliases)}/{capacity} "
        f"assets across {len(selected_moments)} moments",
        flush=True,
    )
    return result


def _run_case(
    client: SyncImmichClient,
    config: Any,
    case: Case,
    people: dict[str, Any],
    facts: dict[str, PersonFact],
    args: argparse.Namespace,
    llm_counters: llm_metrics.LLMCounters,
) -> None:
    out = args.out / case.key
    if (out / "result.json").exists():
        print(f"{case.key}: complete result already exists; skipping", flush=True)
        return
    out.mkdir(parents=True, exist_ok=True)
    total_started = time.monotonic()
    llm_started = llm_counters.snapshot()
    llm_mark = llm_started
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
        accept_any_provenance=case.accept_any_provenance,
    )
    dependencies = EditorialDependencies(
        source_fetcher=lambda _scope: assets,
        preview_jpeg=lambda asset: cached_preview_bytes(thumbnail_cache, asset.id),
    )
    upstream_started = time.monotonic()
    upstream_model = args.upstream_model or config.llm.model
    upstream_llm = config.llm.model_copy(update={"model": upstream_model})
    upstream = _run_upstream(
        EditorialSelectionRequest(scope=scope),
        dependencies,
        config=config,
        llm_config=upstream_llm,
        out=out,
        corpus_selects=args.corpus_selects,
        include_cull_rejected=args.include_cull_rejected,
        concurrency=args.concurrency,
    )
    upstream_seconds = time.monotonic() - upstream_started
    upstream_llm_usage = llm_counters.since(llm_mark).as_metrics()
    llm_mark = llm_counters.snapshot()
    exact_selects = getattr(upstream, "exact_selects", None)
    description_workprint = None
    if upstream.pass_two is not None:
        survivors = upstream.pass_two.survivors
        subset = _description_subset(upstream.prepared, survivors)
    else:
        description_workprint = build_description_workprint(
            upstream.structure_workprint,
            chapter_key=lambda moment: _description_chapter_key(case, moment),
            relationship_names=_relationship_context_names(case, facts),
            reduce_above_moments=0,
        )
        survivors = description_workprint.candidates
        subset = _description_subset(
            upstream.prepared,
            survivors,
            admitted_moments=description_workprint.moments,
        )
    pipeline_order = (
        "as-built-corpus-selects"
        if args.corpus_selects
        else (
            "refined-pre-cut-cull-quality-ablation-exact-collapse"
            if args.include_cull_rejected
            else "refined-pre-cut-exact-collapse"
        )
    )
    _atomic_json(
        out / "upstream.json",
        {
            "case": asdict(case),
            "pipeline_order": pipeline_order,
            "counts": {
                "fetched": len(assets),
                "source_eligible": len(upstream.prepared.candidates),
                "cull_survivors": len(upstream.pass_one.survivors),
                "exact_instant_survivors": (
                    len(exact_selects.survivors) if exact_selects is not None else None
                ),
                "exact_instant_absorbed": (
                    len(exact_selects.absorbed) if exact_selects is not None else None
                ),
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
        f"{len(exact_selects.survivors) if exact_selects is not None else 'integrated'} "
        "after exact-instant collapse -> "
        f"{len(subset.moment_groups)} moments -> {len(survivors)} description assets "
        f"[{pipeline_order}]",
        flush=True,
    )
    if description_workprint is not None:
        allocation = _description_allocation_record(description_workprint) or {}
        reason_counts = allocation.get("reason_counts", {})
        print(
            f"{case.key}: admission reasons: "
            f"{reason_counts.get('favourite-evidence', 0)} favourite moments, "
            f"{reason_counts.get('unstarred-chapter', 0)} moments from unstarred chapters, "
            f"{reason_counts.get('relationship-context', 0)} relationship repairs, "
            f"{reason_counts.get('first-copresence', 0)} first appearances, "
            f"{reason_counts.get('resumption', 0)} resumptions",
            flush=True,
        )

    card_mode = _resolved_card_mode(args.card_mode, description_workprint)
    if args.card_mode == "auto":
        print(f"{case.key}: automatic card mode -> {card_mode}", flush=True)

    description_started = time.monotonic()
    if card_mode == "fused-vision":
        descriptions: tuple[prototype.Description, ...] = ()
        description_warnings: tuple[str, ...] = ()
        description_hits = 0
        description_actual_calls = 0
    else:
        vision_llm = config.llm.model_copy(update={"model": args.vision_model})
        gateway = _ProgressGateway(
            VisualEditorialGateway(
                llm_config=vision_llm,
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
        description_warnings = described.warnings
        description_hits = sum(item.provenance.cache_hit for item in described.descriptions)
        description_actual_calls = sum(
            request.actual_calls
            for request in upstream.prepared.trace.requests
            if request.provenance.pass_name == "asset-description"  # noqa: S105
        )
    description_seconds = time.monotonic() - description_started
    description_llm_usage = llm_counters.since(llm_mark).as_metrics()
    llm_mark = llm_counters.snapshot()
    _atomic_json(
        out / "description-bank.json",
        {
            "privacy": "private lifetime asset descriptions; do not commit",
            "mode": (
                "deferred-until-selected-reservoirs"
                if card_mode == "fused-vision"
                else "up-front-lifetime-descriptions"
            ),
            "descriptions": [asdict(description) for description in descriptions],
            "warnings": list(description_warnings),
            "cache": {
                "hits": description_hits,
                "actual_calls": description_actual_calls,
            },
            "timings": {"description_seconds": description_seconds},
        },
    )
    print(
        f"{case.key}: "
        + (
            f"deferred {len(subset.candidates)} reservoir descriptions to the final cut"
            if card_mode == "fused-vision"
            else f"described {len(descriptions)}/{len(subset.candidates)} in {description_seconds:.1f}s"
        ),
        flush=True,
    )
    text_result = asyncio.run(
        _text_phase(
            case,
            subset,
            descriptions,
            atlas=upstream.pass_zero.atlas,
            facts=facts,
            config=config,
            out=out,
            vision_model=args.vision_model,
            text_model=args.text_model,
            concurrency=args.concurrency,
            editorial_concurrency=args.editorial_concurrency,
            timeout_seconds=args.timeout_seconds,
            editorial_thinking=args.editorial_thinking,
            card_mode=card_mode,
            text_cache_path=args.text_cache,
        )
    )
    text_llm_usage = llm_counters.since(llm_mark).as_metrics()
    llm_mark = llm_counters.snapshot()
    final_cut = None
    if description_workprint is not None:
        final_cut = _run_final_refinement(
            client=client,
            upstream=upstream,
            workprint=description_workprint,
            descriptions=descriptions,
            text_result=text_result,
            facts=facts,
            case=case,
            config=config,
            thumbnail_cache=thumbnail_cache,
            args=args,
            out=out,
        )
    final_refinement_llm_usage = llm_counters.since(llm_mark).as_metrics()
    total_llm_usage = llm_counters.since(llm_started).as_metrics()
    estimated_cost = _estimated_provider_cost(config.llm.model, total_llm_usage)
    cost_currency = _PROVIDER_PRICE_CURRENCY.get(config.llm.model)
    benchmark = {
        "provider": getattr(args, "provider", "configured"),
        "model": config.llm.model,
        "cold": getattr(args, "cold_cache_root", None) is not None,
        "estimated_cost": (
            {"amount": estimated_cost, "currency": cost_currency}
            if estimated_cost is not None and cost_currency is not None
            else None
        ),
        "estimated_cost_usd": estimated_cost if cost_currency == "USD" else None,
        "price_per_million": (
            {
                "input": _PROVIDER_PRICES_PER_MILLION[config.llm.model][0],
                "cached_input": _PROVIDER_PRICES_PER_MILLION[config.llm.model][1],
                "output": _PROVIDER_PRICES_PER_MILLION[config.llm.model][2],
                "currency": cost_currency,
            }
            if estimated_cost is not None
            else None
        ),
    }
    result = {
        "schema_version": "smart-edit-matrix-case-v1",
        "privacy": "private matrix artifact; do not commit IDs, names, descriptions, or answers",
        "case": asdict(case),
        "benchmark": benchmark,
        "upstream_model": upstream_model,
        "vision_model": args.vision_model,
        "source": {
            "pipeline_order": pipeline_order,
            "fetched": len(assets),
            "source_eligible": len(upstream.prepared.candidates),
            "cull_survivors": len(upstream.pass_one.survivors),
            "exact_instant_survivors": (
                len(exact_selects.survivors) if exact_selects is not None else None
            ),
            "exact_instant_absorbed": (
                len(exact_selects.absorbed) if exact_selects is not None else None
            ),
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
        "llm_usage": {
            "upstream_visual": upstream_llm_usage,
            "asset_descriptions": description_llm_usage,
            "cards_and_editorial": text_llm_usage,
            "final_refinement": final_refinement_llm_usage,
            "total": total_llm_usage,
        },
        "edit": {
            key: value for key, value in text_result.items() if key not in {"cache", "timings"}
        },
        "final_cut": final_cut,
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
    parser.add_argument(
        "--provider",
        choices=("configured", "openai", "zai", "melious"),
        default="configured",
        help="run every model stage through one configured or hosted provider",
    )
    parser.add_argument(
        "--provider-model",
        help="single upstream, vision-card, and editorial model for a hosted provider run",
    )
    parser.add_argument(
        "--upstream-model",
        help="model for production Cull/Structure; defaults to the configured model",
    )
    parser.add_argument(
        "--editorial-thinking",
        action="store_true",
        help="enable configured thinking only for thesis, allocation, and final selection",
    )
    parser.add_argument(
        "--card-mode",
        choices=("auto", "model", "template-single", "fused-vision"),
        default="auto",
        help=(
            "automatically use favourite evidence only when it covers every chapter, force cards "
            "from asset descriptions, template exact single-asset moments, or read every 400px "
            "visual once per production moment before describing selected reservoirs"
        ),
    )
    parser.add_argument(
        "--text-cache",
        type=Path,
        help="private shared text judgment cache used to compare output directories",
    )
    parser.add_argument(
        "--cold-cache-root",
        type=Path,
        help=(
            "fresh private root for thumbnails, visual verdicts, frames, and text judgments; "
            "the path must not already exist"
        ),
    )
    parser.add_argument(
        "--include-cull-rejected",
        action="store_true",
        help=(
            "quality ablation: feed all source-eligible assets to Structure while still running "
            "Period Insight and Cull for a measured comparison"
        ),
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--editorial-concurrency",
        type=int,
        default=2,
        help="parallel thesis/selection reasoning calls; bulk descriptions still use --concurrency",
    )
    parser.add_argument(
        "--final-refinement-iterations",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help="maximum evidence-gated corpus refinement iterations; stops early on stability",
    )
    parser.add_argument(
        "--sscd-model",
        type=Path,
        default=None,
        help="sscd_disc_mixup.torchscript.pt; enables copy-embedding duplicate nomination",
    )
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
    if args.sscd_model is not None:
        args.sscd_model = args.sscd_model.expanduser().resolve()
        if not args.sscd_model.is_file():
            parser.error(f"--sscd-model does not exist: {args.sscd_model}")
    if args.text_cache is not None:
        args.text_cache = args.text_cache.expanduser().resolve()
        if not args.text_cache.is_relative_to(matrix):
            parser.error("--text-cache must be inside ~/.immich-memories-matrix")
    if args.provider == "configured" and args.provider_model:
        parser.error("--provider-model requires a hosted --provider")
    if args.provider != "configured" and not args.provider_model:
        parser.error("hosted --provider requires --provider-model")
    if args.provider != "configured" and args.out.exists():
        parser.error("hosted provider output must be a new path")
    if args.cold_cache_root is not None:
        args.cold_cache_root = args.cold_cache_root.expanduser().resolve()
        if not args.cold_cache_root.is_relative_to(matrix):
            parser.error("--cold-cache-root must be inside ~/.immich-memories-matrix")
        if args.cold_cache_root.exists():
            parser.error("--cold-cache-root must not already exist")
        if args.text_cache is not None:
            parser.error("--text-cache cannot be combined with --cold-cache-root")
        args.text_cache = args.cold_cache_root / "text-judgments.db"
    if args.corpus_selects and args.include_cull_rejected:
        parser.error("--include-cull-rejected cannot be combined with --corpus-selects")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    if not 1 <= args.editorial_concurrency <= 8:
        parser.error("--editorial-concurrency must be between 1 and 8")
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
    if args.provider != "configured":
        hosted_llm = _hosted_llm_config(args.provider, args.provider_model)
        config = config.model_copy(update={"llm": hosted_llm})
        args.upstream_model = args.provider_model
        args.vision_model = args.provider_model
        args.text_model = args.provider_model
    if args.cold_cache_root is not None:
        config = _cold_config(config, args.cold_cache_root)
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
            with llm_metrics.collecting() as counters:
                _run_case(client, config, case, people, facts, args, counters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
