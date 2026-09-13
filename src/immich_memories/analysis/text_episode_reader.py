"""Read canonical episodes from annotation text, filling only cold identities."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, TypeVar

from immich_memories.analysis.annotation_lines import AnnotationLineBatch
from immich_memories.analysis.cull_answer import CullDecision
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    RequestTrace,
)
from immich_memories.analysis.editorial_evidence_provenance import EpisodeEvidenceLines
from immich_memories.analysis.selection_source_groups import EditorialGroupProjection
from immich_memories.analysis.strict_json import bounded_model_text
from immich_memories.analysis.text_episode_answers import (
    _WHAT_HAPPENED_MAX_CHARS,
    EpisodeResponseDiagnostic,
    TextEpisodeReadDiagnostics,
    _EpisodePageReading,
    _EpisodeRequestScope,
    _read_response_result,
)
from immich_memories.operations.cut_progress import StageUpdate, announce_stage
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeCullDecision,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)

TEXT_EPISODE_PROMPT_VERSION = "episode-prompt-v1"
TEXT_EPISODE_MAX_OUTPUT_TOKENS = 4_000
_DEFAULT_MAX_PROMPT_CHARS = 24_000
_DEFAULT_MIN_OUTPUT_TOKENS = 512
_DEFAULT_OUTPUT_BASE_TOKENS = 200
_DEFAULT_OUTPUT_TOKENS_PER_ROW = 128
_DEFAULT_OUTPUT_TOKENS_PER_ASSET = 12

_PROMPT = """Read these episodes from one family's photo library. Every asset line contains
all banked annotations for that asset. Use only those lines; do not invent names, places,
relationships, or events.

For every episode return what happened in at most 25 words, one to three representatives
covering distinct situations, and only genuine Cull rejects. Prefer a starred action frame,
video, or qualifying Live Photo when it earns the place. Cull buckets are notes (screens,
documents, receipts), failed (the picture did not come out), and foreign (saved imagery not
from this life). Similar or merely ordinary pictures are not Cull rejects.

Return JSON only:
{{"schema_version":"episode-reading-text-v1","episodes":[{{"episode":1,
"what_happened":"plain factual sentence","representatives":[{{"asset":1,
"reason":"short reason"}}],"cull":[{{"asset":2,"bucket":"notes"}}]}}]}}

{episodes}"""


class AnnotationLineReader(Protocol):
    """Build the one complete prompt line for every requested asset."""

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch: ...


@dataclass(frozen=True)
class TextEpisodeRequestLimits:
    """Bound one serialized episode request below the model's safe context."""

    max_prompt_chars: int = _DEFAULT_MAX_PROMPT_CHARS
    max_assets_per_page: int = 90
    max_output_tokens: int = TEXT_EPISODE_MAX_OUTPUT_TOKENS
    min_output_tokens: int = _DEFAULT_MIN_OUTPUT_TOKENS
    output_base_tokens: int = _DEFAULT_OUTPUT_BASE_TOKENS
    output_tokens_per_row: int = _DEFAULT_OUTPUT_TOKENS_PER_ROW
    output_tokens_per_asset: int = _DEFAULT_OUTPUT_TOKENS_PER_ASSET
    unread_retry_rounds: int = 2

    def __post_init__(self) -> None:
        if (
            self.max_prompt_chars <= 0
            or self.max_assets_per_page <= 0
            or self.max_output_tokens <= 0
            or self.min_output_tokens <= 0
            or self.output_base_tokens <= 0
            or self.output_tokens_per_row <= 0
            or self.output_tokens_per_asset <= 0
            or self.unread_retry_rounds < 0
        ):
            raise ValueError("episode request limits must be positive")
        if self.min_output_tokens > self.max_output_tokens:
            raise ValueError("episode minimum output budget cannot exceed its ceiling")


@dataclass(frozen=True, slots=True)
class EpisodeCacheRequestPlan:
    """The exact cache partition and initial request shape before any model call."""

    requested_identities: tuple[EpisodeReadingIdentity, ...]
    cache_hit_identities: tuple[EpisodeReadingIdentity, ...]
    missing_identities: tuple[EpisodeReadingIdentity, ...]
    initial_page_count: int
    initial_pack_count: int
    oversized_page_count: int

    def __post_init__(self) -> None:
        requested = frozenset(self.requested_identities)
        cache_hits = frozenset(self.cache_hit_identities)
        missing = frozenset(self.missing_identities)
        if len(requested) != len(self.requested_identities):
            raise ValueError("episode request plan identities must be unique")
        if (
            len(cache_hits) != len(self.cache_hit_identities)
            or len(missing) != len(self.missing_identities)
            or cache_hits & missing
            or cache_hits | missing != requested
        ):
            raise ValueError("episode request plan must exactly partition cache hits and misses")
        if (
            min(
                self.initial_page_count,
                self.initial_pack_count,
                self.oversized_page_count,
            )
            < 0
        ):
            raise ValueError("episode request plan counts cannot be negative")
        if self.initial_pack_count + self.oversized_page_count > self.initial_page_count:
            raise ValueError("episode request plan packs cannot exceed its pages")


EpisodeRequestPlanGuard = Callable[[EpisodeCacheRequestPlan], None]
EpisodeEvidenceRecorder = Callable[[Sequence[EpisodeEvidenceLines]], None]


@dataclass(frozen=True)
class EpisodeEditorialEvidence:
    """One demanded canonical episode and the semantic reading outcome."""

    projection: EditorialGroupProjection
    identity: EpisodeReadingIdentity | None
    reading: BankedEpisodeReading | None
    cache_hit: bool
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        if (self.reading is None) == (self.unavailable_reason is None):
            raise ValueError("episode evidence needs exactly one reading outcome")
        if self.reading is not None and (
            self.identity is None
            or self.reading.identity != self.identity
            or self.reading.full_asset_ids != self.projection.group.candidate_ids
        ):
            raise ValueError("episode evidence must preserve canonical identity and membership")
        if self.cache_hit and self.reading is None:
            raise ValueError("an unavailable episode cannot be a cache hit")


@dataclass(frozen=True)
class TextEpisodeReadResult:
    """Demanded episode evidence plus visible fail-open diagnostics."""

    episodes: tuple[EpisodeEditorialEvidence, ...]
    annotation_batch: AnnotationLineBatch
    warnings: tuple[str, ...]
    actual_calls: int
    request_trace: RequestTrace | None = None
    diagnostics: TextEpisodeReadDiagnostics = TextEpisodeReadDiagnostics()

    def __post_init__(self) -> None:
        if self.actual_calls < 0:
            raise ValueError("episode reading call count cannot be negative")
        if self.request_trace is not None and self.request_trace.actual_calls != self.actual_calls:
            raise ValueError("episode request trace must carry the exact call count")

    @property
    def cull_decisions(self) -> tuple[CullDecision, ...]:
        """Return typed decisions from every successfully read episode."""
        return tuple(
            CullDecision(decision.asset_id, decision.bucket)
            for episode in self.episodes
            if episode.reading is not None
            for decision in episode.reading.cull_decisions
        )

    def representative_for(
        self,
        candidates: tuple[EditorialCandidate, ...],
    ) -> tuple[str, str]:
        """Choose the first surviving banked representative, then a safe rule fallback."""
        if not candidates:
            raise ValueError("cannot represent an empty moment")
        candidate_ids = {candidate.asset_id for candidate in candidates}
        for episode in self.episodes:
            if episode.reading is None:
                continue
            for representative in episode.reading.representatives:
                if representative.asset_id in candidate_ids:
                    return representative.asset_id, representative.reason
        favourite = next((candidate for candidate in candidates if candidate.favourite), None)
        if favourite is not None:
            return favourite.asset_id, "rule fallback: protected favourite"
        return candidates[0].asset_id, "rule fallback: first surviving frame"


class CachedTextEpisodeReader:
    """Resolve demanded full episodes from the bank before asking a text model."""

    def __init__(
        self,
        *,
        store: EpisodeReadingStore,
        producer: EpisodeReadingProducer,
        annotations: AnnotationLineReader,
        requester: Callable[[str], str],
        limits: TextEpisodeRequestLimits | None = None,
        request_plan_guard: EpisodeRequestPlanGuard | None = None,
        strict_persistence_readback: bool = False,
        record_evidence: EpisodeEvidenceRecorder | None = None,
    ) -> None:
        self._store = store
        self._producer = producer
        self._annotations = annotations
        self._requester = requester
        self._limits = limits or TextEpisodeRequestLimits()
        self._request_plan_guard = request_plan_guard
        self._strict_persistence_readback = strict_persistence_readback
        self._record_evidence = record_evidence

    @property
    def producer(self) -> EpisodeReadingProducer:
        """Expose the exact contract used to key and interpret every reading."""
        return self._producer

    def read(
        self,
        projections: Sequence[EditorialGroupProjection],
    ) -> TextEpisodeReadResult:
        """Return demanded readings in canonical episode order."""
        annotation_batch = self._annotations.lines_for(
            tuple(
                dict.fromkeys(
                    asset_id
                    for projection in projections
                    for asset_id in projection.group.candidate_ids
                )
            )
        )
        _validate_annotation_contract(annotation_batch, self._producer)
        lines = annotation_batch.as_mapping()
        identities_by_group, unavailable_by_group = self._identities(projections, lines)
        if self._record_evidence is not None:
            self._record_evidence(_evidence_lines(projections, identities_by_group, lines))
        identities = tuple(identities_by_group.values())
        banked = self._store.readings_for(identities)
        cache_hits = frozenset(banked)
        missing = tuple(
            (identity, projection.group.candidate_ids)
            for projection in projections
            if (identity := identities_by_group.get(projection.group.group_id)) is not None
            if identity.group_id not in banked
        )
        request_scopes = tuple(
            page
            for identity, full_asset_ids in missing
            for page in _page_scopes(
                identity,
                full_asset_ids,
                max_assets_per_page=self._limits.max_assets_per_page,
                lines=lines,
                max_prompt_chars=self._limits.max_prompt_chars,
            )
        )
        packs, oversized = _pack_scopes(request_scopes, lines, limits=self._limits)
        response_diagnostics: list[EpisodeResponseDiagnostic] = []
        request_plan = EpisodeCacheRequestPlan(
            requested_identities=identities,
            cache_hit_identities=tuple(
                identity for identity in identities if identity.group_id in banked
            ),
            missing_identities=tuple(identity for identity, _membership in missing),
            initial_page_count=len(request_scopes),
            initial_pack_count=len(packs),
            oversized_page_count=len(oversized),
        )
        if self._request_plan_guard is not None:
            self._request_plan_guard(request_plan)
        actual_calls = 0
        if missing:
            actual_calls = self._fill(
                missing=missing,
                request_scopes=request_scopes,
                packs=packs,
                oversized=oversized,
                lines=lines,
                banked=banked,
                unavailable_by_group=unavailable_by_group,
                diagnostics=response_diagnostics,
            )
        episodes = _evidence(
            projections, identities_by_group, banked, cache_hits, unavailable_by_group
        )
        unavailable = sum(episode.reading is None for episode in episodes)
        warnings = (
            *annotation_batch.warnings,
            *(
                (f"!! {unavailable} demanded episode(s) unread; full membership retained",)
                if unavailable
                else ()
            ),
        )
        provenance = _episode_provenance(episodes, annotation_batch, self._producer)
        return TextEpisodeReadResult(
            episodes=episodes,
            annotation_batch=annotation_batch,
            warnings=warnings,
            actual_calls=actual_calls,
            request_trace=RequestTrace(
                provenance=provenance,
                attached_sheet_hashes=(),
                planned_calls=len(packs),
                actual_calls=actual_calls,
                cache_hit=provenance.cache_hit,
                tile_count=0,
                model=self._producer.model_id,
            ),
            diagnostics=TextEpisodeReadDiagnostics(tuple(response_diagnostics)),
        )

    def _identities(
        self,
        projections: Sequence[EditorialGroupProjection],
        lines: Mapping[str, str],
    ) -> tuple[dict[str, EpisodeReadingIdentity], dict[str, str]]:
        """Key every episode whose complete annotation evidence is present; name the rest."""
        identities_by_group: dict[str, EpisodeReadingIdentity] = {}
        unavailable_by_group: dict[str, str] = {}
        for projection in projections:
            missing_annotations = tuple(
                asset_id
                for asset_id in projection.group.candidate_ids
                if not str(lines.get(asset_id, "")).strip()
            )
            if missing_annotations:
                unavailable_by_group[projection.group.group_id] = (
                    "complete annotation evidence unavailable for "
                    f"{len(missing_annotations)} full episode member(s)"
                )
                continue
            identities_by_group[projection.group.group_id] = (
                EpisodeReadingIdentity.from_annotations(
                    group_id=projection.group.group_id,
                    producer_key=self._producer.key(),
                    annotation_lines={
                        asset_id: lines[asset_id] for asset_id in projection.group.candidate_ids
                    },
                )
            )
        return identities_by_group, unavailable_by_group

    def _fill(
        self,
        *,
        missing,
        request_scopes,
        packs,
        oversized,
        lines,
        banked,
        unavailable_by_group,
        diagnostics,
    ) -> int:
        page_readings: list[_EpisodePageReading] = []
        failed: set[tuple[str, int]] = set()
        for scope in oversized:
            failed.add(_scope_key(scope))
            unavailable_by_group[scope.identity.group_id] = (
                "complete episode annotation evidence exceeds the request limit"
            )
        calls = 0
        for pack in packs:
            calls += 1
            announce_stage(
                StageUpdate("event evidence", done=calls, total=len(packs), verb="Reading")
            )
            page_readings.extend(self._ask(pack, lines, diagnostics, unavailable_by_group, failed))
            self._bank_complete(missing, request_scopes, page_readings, banked)
        for _round in range(self._limits.unread_retry_rounds):
            unread = _unread_scopes(request_scopes, page_readings, failed)
            if not unread:
                break
            for scope in unread:
                calls += 1
                page_readings.extend(
                    self._ask((scope,), lines, diagnostics, unavailable_by_group, failed)
                )
                self._bank_complete(missing, request_scopes, page_readings, banked)
        return calls

    def _ask(self, pack, lines, diagnostics, unavailable_by_group, failed):
        try:
            response = _read_missing(
                self._requester,
                pack,
                lines,
                max_tokens=_completion_budget(pack, self._limits),
            )
        except Exception as exc:  # WHY: one failed pack cannot remove other episodes
            detail = str(exc).strip() or type(exc).__name__
            reason = f"text episode provider failed ({type(exc).__name__}): {detail}"
            for scope in pack:
                failed.add(_scope_key(scope))
                unavailable_by_group[scope.identity.group_id] = reason
            return ()
        if response.diagnostic is not None:
            diagnostics.append(response.diagnostic)
        return response.readings

    def _bank_complete(self, missing, request_scopes, page_readings, banked) -> None:
        completed = tuple(
            reading
            for reading in _merge_complete_readings(missing, request_scopes, tuple(page_readings))
            if reading.identity.group_id not in banked
        )
        if not completed:
            return
        self._store.remember(completed)
        if self._strict_persistence_readback:
            self._verify_readback(completed)
        banked.update({reading.identity.group_id: reading for reading in completed})

    def _verify_readback(self, completed: tuple[BankedEpisodeReading, ...]) -> None:
        recalled = self._store.readings_for(tuple(reading.identity for reading in completed))
        matched = sum(recalled.get(reading.identity.group_id) == reading for reading in completed)
        if len(recalled) != len(completed) or matched != len(completed):
            raise RuntimeError(
                "episode persistence readback failed "
                f"(attempted={len(completed)}, read_back={len(recalled)}, "
                f"matched={matched})"
            )


def _evidence_lines(
    projections: Sequence[EditorialGroupProjection],
    identities_by_group: Mapping[str, EpisodeReadingIdentity],
    lines: Mapping[str, str],
) -> tuple[EpisodeEvidenceLines, ...]:
    """The exact evidence each keyed episode was read from, in canonical order."""
    return tuple(
        EpisodeEvidenceLines(
            group_id=projection.group.group_id,
            evidence_key=identity.evidence_key,
            lines=tuple((asset_id, lines[asset_id]) for asset_id in projection.group.candidate_ids),
        )
        for projection in projections
        if (identity := identities_by_group.get(projection.group.group_id)) is not None
    )


def _unread_scopes(request_scopes, page_readings, failed):
    received = {_scope_key(reading.scope) for reading in page_readings}
    return tuple(
        scope
        for scope in request_scopes
        if _scope_key(scope) not in received and _scope_key(scope) not in failed
    )


def _evidence(
    projections, identities_by_group, banked, cache_hits, unavailable_by_group
) -> tuple[EpisodeEditorialEvidence, ...]:
    return tuple(
        EpisodeEditorialEvidence(
            projection=projection,
            identity=identities_by_group.get(projection.group.group_id),
            reading=banked.get(projection.group.group_id),
            cache_hit=projection.group.group_id in cache_hits,
            unavailable_reason=(
                None
                if projection.group.group_id in banked
                else unavailable_by_group.get(
                    projection.group.group_id,
                    "text episode response was missing or invalid; full membership retained",
                )
            ),
        )
        for projection in projections
    )


def _episode_provenance(
    episodes: tuple[EpisodeEditorialEvidence, ...],
    annotation_batch: AnnotationLineBatch,
    producer: EpisodeReadingProducer,
) -> DecisionProvenance:
    identity_material = [
        (
            episode.projection.group.group_id,
            episode.identity.producer_key if episode.identity is not None else "unavailable",
            episode.identity.evidence_key if episode.identity is not None else "unavailable",
        )
        for episode in episodes
    ]
    request_key = sha256(
        json.dumps(identity_material, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return DecisionProvenance(
        pass_name="episode-reading-text",  # noqa: S106 - public editorial pass identity.
        pass_version=producer.prompt_version,
        schema_version=producer.schema_version,
        model_identity=producer.model_id,
        input_ids=annotation_batch.requested_asset_ids,
        sheet_hashes=(),
        request_key=request_key,
        cache_hit=bool(episodes) and all(episode.cache_hit for episode in episodes),
    )


def _read_missing(
    requester: Callable[[str], str],
    scopes: tuple[_EpisodeRequestScope, ...],
    lines: Mapping[str, str],
    *,
    max_tokens: int,
):
    prompt = _prompt_for(scopes, lines)
    budgeted_request = getattr(requester, "request_with_budget", None)
    raw = (
        budgeted_request(prompt, max_tokens=max_tokens)
        if callable(budgeted_request)
        else requester(prompt)
    )
    return _read_response_result(raw, scopes)


def _page_scopes(
    identity: EpisodeReadingIdentity,
    full_asset_ids: tuple[str, ...],
    *,
    max_assets_per_page: int,
    lines: Mapping[str, str],
    max_prompt_chars: int,
) -> tuple[_EpisodeRequestScope, ...]:
    whole = _EpisodeRequestScope(
        identity=identity,
        full_asset_ids=full_asset_ids,
        page_asset_ids=full_asset_ids,
        page_number=1,
        page_count=1,
    )
    if (
        len(full_asset_ids) <= max_assets_per_page
        and len(_prompt_for((whole,), lines)) <= max_prompt_chars
    ):
        return (whole,)

    pages: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    conservative_page_count = max(len(full_asset_ids), 2)
    for asset_id in full_asset_ids:
        proposed = (*current, asset_id)
        scope = _EpisodeRequestScope(
            identity=identity,
            full_asset_ids=full_asset_ids,
            page_asset_ids=proposed,
            page_number=conservative_page_count,
            page_count=conservative_page_count,
        )
        if current and (
            len(proposed) > max_assets_per_page
            or len(_prompt_for((scope,), lines)) > max_prompt_chars
        ):
            pages.append(current)
            current = (asset_id,)
        else:
            current = proposed
    if current:
        pages.append(current)

    return tuple(
        _EpisodeRequestScope(
            identity=identity,
            full_asset_ids=full_asset_ids,
            page_asset_ids=page_asset_ids,
            page_number=page_number,
            page_count=len(pages),
        )
        for page_number, page_asset_ids in enumerate(pages, start=1)
    )


def _scope_key(scope: _EpisodeRequestScope) -> tuple[str, int]:
    return scope.identity.group_id, scope.page_number


def _pack_scopes(
    scopes: tuple[_EpisodeRequestScope, ...],
    lines: Mapping[str, str],
    *,
    limits: TextEpisodeRequestLimits,
) -> tuple[tuple[tuple[_EpisodeRequestScope, ...], ...], tuple[_EpisodeRequestScope, ...]]:
    packs: list[tuple[_EpisodeRequestScope, ...]] = []
    oversized: list[_EpisodeRequestScope] = []
    current: tuple[_EpisodeRequestScope, ...] = ()
    for scope in scopes:
        if (
            len(_prompt_for((scope,), lines)) > limits.max_prompt_chars
            or _completion_budget((scope,), limits) > limits.max_output_tokens
        ):
            oversized.append(scope)
            continue
        if scope.page_count > 1:
            if current:
                packs.append(current)
                current = ()
            packs.append((scope,))
            continue
        proposed = (*current, scope)
        if current and (
            len(_prompt_for(proposed, lines)) > limits.max_prompt_chars
            or _completion_budget(proposed, limits) > limits.max_output_tokens
        ):
            packs.append(current)
            current = (scope,)
        else:
            current = proposed
    if current:
        packs.append(current)
    return tuple(packs), tuple(oversized)


def _completion_budget(
    scopes: tuple[_EpisodeRequestScope, ...],
    limits: TextEpisodeRequestLimits,
) -> int:
    """Size generation from demanded response rows and possible Cull aliases."""
    estimated = (
        limits.output_base_tokens
        + limits.output_tokens_per_row * len(scopes)
        + limits.output_tokens_per_asset * sum(len(scope.page_asset_ids) for scope in scopes)
    )
    return max(limits.min_output_tokens, estimated)


_Decided = TypeVar("_Decided", EpisodeRepresentative, EpisodeCullDecision)


def _first_per_asset(items: Iterable[_Decided]) -> tuple[_Decided, ...]:
    """The first row naming each asset, in order: a later page never overrides an earlier one."""
    seen: set[str] = set()
    kept: list[_Decided] = []
    for item in items:
        if item.asset_id not in seen:
            seen.add(item.asset_id)
            kept.append(item)
    return tuple(kept)


def _complete_reading(identity, full_asset_ids, ordered) -> BankedEpisodeReading | None:
    """One episode's pages folded into a bankable reading, or None when they contradict."""
    what_happened = bounded_model_text(
        " ".join(reading.what_happened for reading in ordered),
        max_chars=_WHAT_HAPPENED_MAX_CHARS,
    )
    representatives = _first_per_asset(
        representative for reading in ordered for representative in reading.representatives
    )[:4]
    cull_decisions = _first_per_asset(
        decision for reading in ordered for decision in reading.cull_decisions
    )
    if (
        what_happened is None
        or not representatives
        or {item.asset_id for item in representatives}.intersection(
            decision.asset_id for decision in cull_decisions
        )
    ):
        return None
    return BankedEpisodeReading(
        identity=identity,
        full_asset_ids=full_asset_ids,
        what_happened=what_happened,
        representatives=representatives,
        cull_decisions=cull_decisions,
    )


def _merge_complete_readings(
    missing: tuple[tuple[EpisodeReadingIdentity, tuple[str, ...]], ...],
    request_scopes: tuple[_EpisodeRequestScope, ...],
    page_readings: tuple[_EpisodePageReading, ...],
) -> tuple[BankedEpisodeReading, ...]:
    scopes_by_group: dict[str, list[_EpisodeRequestScope]] = {}
    for scope in request_scopes:
        scopes_by_group.setdefault(scope.identity.group_id, []).append(scope)
    readings_by_group: dict[str, dict[int, _EpisodePageReading]] = {}
    for page_reading in page_readings:
        readings_by_group.setdefault(page_reading.scope.identity.group_id, {})[
            page_reading.scope.page_number
        ] = page_reading

    completed: list[BankedEpisodeReading] = []
    for identity, full_asset_ids in missing:
        expected = scopes_by_group.get(identity.group_id, [])
        received = readings_by_group.get(identity.group_id, {})
        if not expected or set(received) != {scope.page_number for scope in expected}:
            continue
        reading = _complete_reading(
            identity, full_asset_ids, tuple(received[scope.page_number] for scope in expected)
        )
        if reading is not None:
            completed.append(reading)
    return tuple(completed)


def _validate_annotation_contract(
    batch: AnnotationLineBatch,
    producer: EpisodeReadingProducer,
) -> None:
    if batch.contract.renderer_version != producer.annotation_renderer_version or frozenset(
        batch.contract.producer_versions
    ) != frozenset(producer.annotation_versions):
        raise ValueError("episode producer does not match the annotation evidence contract")


def _prompt_for(
    scopes: tuple[_EpisodeRequestScope, ...],
    lines: Mapping[str, str],
) -> str:
    blocks = []
    for episode_alias, scope in enumerate(scopes, start=1):
        asset_lines = "\n".join(
            f"  asset {asset_alias} | {lines[asset_id]}"
            for asset_alias, asset_id in enumerate(scope.page_asset_ids, start=1)
        )
        page = f"  page {scope.page_number} of {scope.page_count}\n" if scope.page_count > 1 else ""
        blocks.append(f"episode {episode_alias}\n{page}{asset_lines}")
    return _PROMPT.format(episodes="\n\n".join(blocks))
