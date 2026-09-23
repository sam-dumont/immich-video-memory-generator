"""Read canonical episodes from annotation text, filling only cold identities."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
from immich_memories.analysis.text_episode_paging import (
    TextEpisodeRequestLimits,
    episode_completion_budget,
    episode_page_scopes,
    pack_episode_scopes,
)
from immich_memories.analysis.text_episode_prompt import (
    AlbumNames,
    EpisodePromptFacts,
    episode_prompt,
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

logger = logging.getLogger(__name__)

_PROVIDER_FAILED = "text episode provider failed"
_UNUSABLE = "text episode response was missing or invalid; full membership retained"


class AnnotationLineReader(Protocol):
    """Build the one complete prompt line for every requested asset."""

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch: ...


@dataclass(frozen=True, slots=True)
class EpisodeCacheRequestPlan:
    """The exact cache partition and initial request shape before any model call."""

    requested_identities: tuple[EpisodeReadingIdentity, ...]
    cache_hit_identities: tuple[EpisodeReadingIdentity, ...]
    missing_identities: tuple[EpisodeReadingIdentity, ...]
    initial_page_count: int
    initial_pack_count: int
    oversized_page_count: int
    # Asked before and refused: neither answered nor owed, and never asked again under this key.
    refused_identities: tuple[EpisodeReadingIdentity, ...] = ()

    def __post_init__(self) -> None:
        requested = frozenset(self.requested_identities)
        parts = (
            frozenset(self.cache_hit_identities),
            frozenset(self.missing_identities),
            frozenset(self.refused_identities),
        )
        sizes = (
            len(self.cache_hit_identities),
            len(self.missing_identities),
            len(self.refused_identities),
        )
        if len(requested) != len(self.requested_identities):
            raise ValueError("episode request plan identities must be unique")
        if (
            [len(part) for part in parts] != list(sizes)
            or sum(sizes) != len(requested)
            or frozenset.union(*parts) != requested
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

    @property
    def judged_asset_ids(self) -> tuple[str, ...]:
        """Every picture a successful reading looked at, rejected or kept.

        The rejects alone cannot say which pictures were judged: an episode
        that failed to read also rejects nothing.
        """
        return tuple(
            dict.fromkeys(
                asset_id
                for episode in self.episodes
                if episode.reading is not None
                for asset_id in episode.reading.full_asset_ids
            )
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
        albums: AlbumNames | None = None,
        lean: bool = False,
        served_by: EpisodeReadingProducer | None = None,
    ) -> None:
        """`lean` asks the film's on-demand question (no Cull, one representative); a reading
        banked under `served_by`, a question that asks for more, answers it for free."""
        self._lean = lean
        self._served_by = served_by
        self._store = store
        self._producer = producer
        self._annotations = annotations
        self._requester = requester
        self._albums = albums
        self._limits = limits or TextEpisodeRequestLimits()
        self._request_plan_guard = request_plan_guard
        self._strict_persistence_readback = strict_persistence_readback
        self._record_evidence = record_evidence
        self._reported_failures: set[str] = set()

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
        facts = EpisodePromptFacts(lines=lines, album_names=self._albums, lean=self._lean)
        identities_by_group, unavailable_by_group = self._identities(projections, lines)
        if self._record_evidence is not None:
            self._record_evidence(_evidence_lines(projections, identities_by_group, lines))
        banked = self._store.readings_for(tuple(identities_by_group.values()))
        banked |= self._served_elsewhere(identities_by_group, banked)
        identities = tuple(identities_by_group.values())
        cache_hits = frozenset(banked)
        # An episode this exact question already failed to read is not asked again: the answer
        # would be the same until the prompt, the evidence or the reader changes, and all three
        # are in the key this refusal is filed under.
        refused = self._store.refusals_for(identities)
        unavailable_by_group.update(refused)
        missing = tuple(
            (identity, projection.group.candidate_ids)
            for projection in projections
            if (identity := identities_by_group.get(projection.group.group_id)) is not None
            if identity.group_id not in banked and identity.group_id not in refused
        )
        request_scopes = tuple(
            page
            for identity, full_asset_ids in missing
            for page in episode_page_scopes(
                identity,
                full_asset_ids,
                max_assets_per_page=self._limits.max_assets_per_page,
                facts=facts,
                max_prompt_chars=self._limits.max_prompt_chars,
            )
        )
        packs, oversized = pack_episode_scopes(request_scopes, facts, limits=self._limits)
        response_diagnostics: list[EpisodeResponseDiagnostic] = []
        request_plan = EpisodeCacheRequestPlan(
            requested_identities=identities,
            cache_hit_identities=tuple(
                identity
                for identity in identities
                if identity.group_id in banked and identity.group_id not in refused
            ),
            missing_identities=tuple(identity for identity, _membership in missing),
            refused_identities=tuple(
                identity for identity in identities if identity.group_id in refused
            ),
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
                facts=facts,
                banked=banked,
                unavailable_by_group=unavailable_by_group,
                diagnostics=response_diagnostics,
            )
            self._bank_refusals(missing, banked, unavailable_by_group)
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

    def _served_elsewhere(
        self,
        identities_by_group: dict[str, EpisodeReadingIdentity],
        banked: Mapping[str, BankedEpisodeReading],
    ) -> dict[str, BankedEpisodeReading]:
        """Readings a wider question already banked for the episodes this one has not.

        The identity of each episode it answers becomes that reading's own, so the evidence
        and everything read back later name the reading that was actually used.
        """
        if self._served_by is None:
            return {}
        wider = {
            group: replace(identity, producer_key=self._served_by.key())
            for group, identity in identities_by_group.items()
            if group not in banked
        }
        found = self._store.readings_for(tuple(wider.values())) if wider else {}
        identities_by_group.update({group: wider[group] for group in found})
        return found

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
        facts,
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
        _offer_batch(self._requester, packs, facts, self._limits)
        for pack, response in self._read_packs(packs, facts):
            calls += 1
            announce_stage(
                StageUpdate("event evidence", done=calls, total=len(packs), verb="Reading")
            )
            page_readings.extend(
                self._record_response(pack, response, diagnostics, unavailable_by_group, failed)
            )
            self._bank_complete(missing, request_scopes, page_readings, banked)
        for _round in range(self._limits.unread_retry_rounds):
            unread = _unread_scopes(request_scopes, page_readings, failed)
            if not unread:
                break
            retries = tuple((scope,) for scope in unread)
            for pack, response in self._read_packs(retries, facts):
                calls += 1
                page_readings.extend(
                    self._record_response(pack, response, diagnostics, unavailable_by_group, failed)
                )
                self._bank_complete(missing, request_scopes, page_readings, banked)
        return calls

    def _read_packs(self, packs, facts):
        def read(requester, pack):
            return self._ask(requester, pack, facts)

        run = getattr(self._requester, "iter_independent", None)
        if callable(run):
            yield from run(read, packs)
        else:
            for pack in packs:
                yield pack, read(self._requester, pack)

    def _ask(self, requester, pack, facts):
        try:
            return _read_missing(
                requester,
                pack,
                facts,
                max_tokens=episode_completion_budget(pack, self._limits),
            )
        except Exception as exc:  # WHY: one failed pack cannot remove other episodes
            return exc

    def _record_response(self, pack, response, diagnostics, unavailable_by_group, failed):
        """Mutate diagnostics and semantic state only after returning to the store's thread."""
        if isinstance(response, Exception):
            exc = response
            detail = str(exc).strip() or type(exc).__name__
            reason = f"text episode provider failed ({type(exc).__name__}): {detail}"
            # Without this the run only ever says "no readable episode evidence" (#908).
            if reason not in self._reported_failures:
                self._reported_failures.add(reason)
                logger.warning(reason)
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

    def _bank_refusals(self, missing, banked, unavailable_by_group) -> None:
        """File what this contract could not read, once its retries are spent.

        A provider that never answered has refused nothing: the next run may reach it, so its
        failure is not banked. Everything else is a verdict on this exact question -- a reply
        that could not be used, or evidence that will not fit a request -- and asking it again
        every run costs the same and answers the same.
        """
        refusals = [
            (identity, reason)
            for identity, _membership in missing
            if identity.group_id not in banked
            if not (reason := unavailable_by_group.get(identity.group_id, _UNUSABLE)).startswith(
                _PROVIDER_FAILED
            )
        ]
        if not refusals:
            return
        logger.warning(
            "%d episode(s) could not be read and will not be asked again until the prompt, "
            "the evidence or the reader changes",
            len(refusals),
        )
        self._store.remember_refusals(refusals)

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
                else unavailable_by_group.get(projection.group.group_id, _UNUSABLE)
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


def _offer_batch(requester, packs, facts, limits) -> None:
    """Hand the whole page fan-out to the provider's batch route in one go.

    Every pack carries one or more episodes' own annotation lines and nothing
    else: no pack is shown another pack's answer, which is the property a batch
    needs. The moment inventory next door does not have it -- each of its pages
    is told what the pages before it found -- so it stays a sequence.
    """
    offer = getattr(requester, "prefetch", None)
    if not callable(offer):
        return
    offer(
        tuple(
            (episode_prompt(pack, facts), episode_completion_budget(pack, limits)) for pack in packs
        )
    )


def _read_missing(
    requester: Callable[[str], str],
    scopes: tuple[_EpisodeRequestScope, ...],
    facts: EpisodePromptFacts,
    *,
    max_tokens: int,
):
    prompt = episode_prompt(scopes, facts)
    budgeted_request = getattr(requester, "request_with_budget", None)
    raw = (
        budgeted_request(prompt, max_tokens=max_tokens)
        if callable(budgeted_request)
        else requester(prompt)
    )
    return _read_response_result(raw, scopes)


def _scope_key(scope: _EpisodeRequestScope) -> tuple[str, int]:
    return scope.identity.group_id, scope.page_number


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
    notable_moments = _first_per_asset(
        moment for reading in ordered for moment in reading.notable_moments
    )
    kept = {item.asset_id for item in (*representatives, *notable_moments)}
    if (
        what_happened is None
        or not representatives
        or kept.intersection(decision.asset_id for decision in cull_decisions)
    ):
        return None
    return BankedEpisodeReading(
        identity=identity,
        full_asset_ids=full_asset_ids,
        what_happened=what_happened,
        representatives=representatives,
        cull_decisions=cull_decisions,
        notable_moments=notable_moments,
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
