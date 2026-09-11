"""Read a period from banked episode meaning without constructing a visual wall."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import starmap

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    InsightEvidence,
    PeriodInsight,
    RequestTrace,
)
from immich_memories.analysis.text_episode_reader import (
    EpisodeEditorialEvidence,
    TextEpisodeReadResult,
)
from immich_memories.analysis.text_period_wire import (
    _PeriodEpisodeFacts,
    _prompt_for,
    _read_response,
    _repair_prompt,
    _response_problem,
    _synthesis_prompt,
)
from immich_memories.store.period_insights import (
    BankedPeriodInsight,
    PeriodEpisodeGrounding,
    PeriodInsightIdentity,
    PeriodInsightProducer,
    PeriodInsightStore,
)

TEXT_PERIOD_PROMPT_VERSION = "period-text-v2-compact-facts"
TEXT_PERIOD_MAX_OUTPUT_TOKENS = 3_000  # a ten-year, 38-range custom scope truncated at 1_500 (2026-09-05); the parser caps every field after parse
_DEFAULT_MAX_PROMPT_CHARS = 96_000
_LEAF_PROMPT_SUFFIX = "+leaf-v1"
_MERGE_PROMPT_SUFFIX = "+merge-v1"


@dataclass(frozen=True)
class TextPeriodRequestLimits:
    """Bound the complete serialized period request below model context."""

    max_prompt_chars: int = _DEFAULT_MAX_PROMPT_CHARS

    def __post_init__(self) -> None:
        if self.max_prompt_chars <= 0:
            raise ValueError("period request limit must be positive")


@dataclass(frozen=True)
class TextPeriodInsightResult:
    """One semantic period result and its non-visual request accounting."""

    insight: PeriodInsight
    episode_grounding: tuple[PeriodEpisodeGrounding, ...]
    warnings: tuple[str, ...]
    request_trace: RequestTrace | None
    actual_calls: int
    identity: PeriodInsightIdentity | None = None
    pages: int = 1

    def __post_init__(self) -> None:
        if self.actual_calls < 0:
            raise ValueError("period insight call count cannot be negative")
        if self.pages < 1:
            raise ValueError("a period insight covers at least one page")
        if self.insight.thesis is not None and not self.episode_grounding:
            raise ValueError("an available period insight needs episode grounding")


def _nothing_readable(
    producer: PeriodInsightProducer,
    input_ids: tuple[str, ...],
    warnings: tuple[str, ...],
) -> TextPeriodInsightResult:
    provenance = _provenance(
        producer, input_ids=input_ids, request_key="no-readable-episodes", cache_hit=False
    )
    return TextPeriodInsightResult(
        insight=PeriodInsight(
            thesis=None,
            evidence=(),
            tensions=(),
            recurring_threads=(),
            unavailable_reason="no readable episode evidence for period insight",
            revision=0,
            provenance=provenance,
        ),
        episode_grounding=(),
        warnings=warnings,
        request_trace=None,
        actual_calls=0,
    )


def run_text_period_insight(
    episodes: TextEpisodeReadResult,
    *,
    store: PeriodInsightStore,
    producer: PeriodInsightProducer,
    requester: Callable[[str], str],
    limits: TextPeriodRequestLimits | None = None,
) -> TextPeriodInsightResult:
    """Reuse or read one period over every successfully banked demanded episode."""
    limits = limits or TextPeriodRequestLimits()
    input_ids = tuple(
        dict.fromkeys(
            asset_id
            for episode in episodes.episodes
            for asset_id in episode.projection.group.candidate_ids
        )
    )
    readable = tuple(episode for episode in episodes.episodes if episode.reading is not None)
    unread_count = len(episodes.episodes) - len(readable)
    warnings = (
        *episodes.warnings,
        *(
            (f"!! {unread_count} demanded episode(s) omitted from period insight because unread",)
            if unread_count
            else ()
        ),
    )
    if not readable:
        return _nothing_readable(producer, input_ids, warnings)

    facts = tuple(_episode_facts(episode) for episode in readable)
    grounding = tuple(starmap(_episode_grounding, zip(readable, facts, strict=True)))
    identity = PeriodInsightIdentity.from_grounding(
        producer_key=producer.key(),
        episodes=grounding,
    )
    banked = store.insight_for(identity)
    if banked is not None:
        provenance = _provenance(
            producer,
            input_ids=input_ids,
            request_key=identity.evidence_key,
            cache_hit=True,
        )
        return TextPeriodInsightResult(
            insight=_period_insight(banked, provenance),
            episode_grounding=grounding,
            warnings=warnings,
            request_trace=_request_trace(provenance, producer, actual_calls=0),
            actual_calls=0,
            identity=identity,
        )

    prompt = _prompt_for(facts)
    if len(prompt) > limits.max_prompt_chars:
        return _paged_period_insight(
            facts,
            grounding,
            store=store,
            producer=producer,
            requester=requester,
            limits=limits,
            input_ids=input_ids,
            warnings=warnings,
        )
    return _one_call_period_insight(
        facts,
        grounding,
        identity,
        prompt=prompt,
        store=store,
        producer=producer,
        requester=requester,
        limits=limits,
        input_ids=input_ids,
        warnings=warnings,
    )


def _one_call_period_insight(
    facts: tuple[_PeriodEpisodeFacts, ...],
    grounding: tuple[PeriodEpisodeGrounding, ...],
    identity: PeriodInsightIdentity,
    *,
    prompt: str,
    store: PeriodInsightStore,
    producer: PeriodInsightProducer,
    requester: Callable[[str], str],
    limits: TextPeriodRequestLimits,
    input_ids: tuple[str, ...],
    warnings: tuple[str, ...],
) -> TextPeriodInsightResult:
    provenance = _provenance(
        producer, input_ids=input_ids, request_key=identity.evidence_key, cache_hit=False
    )
    request_trace = _request_trace(provenance, producer, actual_calls=1)
    try:
        raw = requester(prompt)
    except Exception as exc:  # WHY: a provider failure must leave a cold, visible period result
        if _is_truncation(exc) and len(grounding) >= 2:
            # WHY: a dense period's answer can overflow any output budget; the reader already pages an oversized
            # prompt, so an oversized answer is paged the same way, at half the prompt limit, and synthesized once.
            return _paged_period_insight(
                facts,
                grounding,
                store=store,
                producer=producer,
                requester=requester,
                limits=TextPeriodRequestLimits(
                    max_prompt_chars=max(1, min(limits.max_prompt_chars, len(prompt)) // 2)
                ),
                input_ids=input_ids,
                warnings=(
                    *warnings,
                    "period answer overflowed the output budget in one call; paged",
                ),
            )
        return _unavailable_result(
            grounding,
            warnings,
            provenance,
            request_trace,
            f"text period provider failed ({type(exc).__name__}: {str(exc)[:160]})",
        )
    banked = _read_response(raw, identity, grounding) if isinstance(raw, str) else None
    if banked is None:
        problem = _response_problem(raw if isinstance(raw, str) else "", grounding)
        request_trace = _request_trace(provenance, producer, actual_calls=2)
        try:
            raw = requester(_repair_prompt(prompt, problem, len(grounding)))
        except Exception as exc:  # WHY: a repair failure leaves the period unbanked
            return _unavailable_result(
                grounding,
                warnings,
                provenance,
                request_trace,
                f"text period provider failed on repair ({type(exc).__name__}: {str(exc)[:160]})",
            )
        banked = _read_response(raw, identity, grounding) if isinstance(raw, str) else None
        warnings = (*warnings, f"period reading repaired once ({problem})")
    if banked is None:
        return _unavailable_result(
            grounding,
            warnings,
            provenance,
            request_trace,
            "text period response was missing or invalid",
        )
    store.remember(banked)
    return TextPeriodInsightResult(
        insight=_period_insight(banked, provenance),
        episode_grounding=grounding,
        warnings=warnings,
        request_trace=request_trace,
        actual_calls=request_trace.actual_calls,
        identity=identity,
    )


def _episode_facts(episode: EpisodeEditorialEvidence) -> _PeriodEpisodeFacts:
    reading = episode.reading
    if reading is None:
        raise ValueError("cannot ground an unread episode")
    candidates = episode.projection.group.candidates
    place = _most_common_nonblank(_place(candidate.source.exif_info) for candidate in candidates)
    people = tuple(
        name
        for name, _count in Counter(
            person.name.strip()
            for candidate in candidates
            for person in (candidate.source.people or ())
            if person.name.strip()
        ).most_common(3)
    )
    return _PeriodEpisodeFacts(
        first_taken_at=candidates[0].taken_at,
        last_taken_at=candidates[-1].taken_at,
        place=place,
        people=people,
        asset_count=len(candidates),
        what_happened=reading.what_happened,
    )


def _episode_grounding(
    episode: EpisodeEditorialEvidence,
    facts: _PeriodEpisodeFacts,
) -> PeriodEpisodeGrounding:
    reading = episode.reading
    if reading is None:
        raise ValueError("cannot ground an unread episode")
    dates = f"{facts.first_taken_at.date().isoformat()}..{facts.last_taken_at.date().isoformat()}"
    rendered_line = " | ".join(
        (
            dates,
            facts.place or "no named place",
            ", ".join(facts.people) or "no named people",
            f"{facts.asset_count} assets",
            facts.what_happened,
        )
    )
    return PeriodEpisodeGrounding(
        episode_id=episode.projection.group.group_id,
        evidence_key=reading.identity.evidence_key,
        rendered_line=rendered_line,
        representative_asset_ids=tuple(
            representative.asset_id for representative in reading.representatives
        ),
    )


def _place(exif: object) -> str:
    if exif is None:
        return ""
    return ", ".join(
        str(value).strip()
        for value in (
            getattr(exif, "city", None),
            getattr(exif, "state", None),
            getattr(exif, "country", None),
        )
        if str(value or "").strip()
    )


def _most_common_nonblank(values: Iterable[str]) -> str:
    cleaned = tuple(value for value in values if value)
    return Counter(cleaned).most_common(1)[0][0] if cleaned else ""


def _provenance(
    producer: PeriodInsightProducer,
    *,
    input_ids: tuple[str, ...],
    request_key: str,
    cache_hit: bool,
) -> DecisionProvenance:
    return DecisionProvenance(
        pass_name="period-insight-text",  # noqa: S106 -- editorial pass, not a password.
        pass_version=producer.prompt_version,
        schema_version=producer.schema_version,
        model_identity=producer.model_id,
        input_ids=input_ids,
        sheet_hashes=(),
        request_key=request_key,
        cache_hit=cache_hit,
    )


def _request_trace(
    provenance: DecisionProvenance,
    producer: PeriodInsightProducer,
    *,
    actual_calls: int,
) -> RequestTrace:
    return RequestTrace(
        provenance=provenance,
        attached_sheet_hashes=(),
        actual_calls=actual_calls,
        cache_hit=provenance.cache_hit,
        tile_count=0,
        model=producer.model_id,
    )


def _period_insight(
    banked: BankedPeriodInsight,
    provenance: DecisionProvenance,
) -> PeriodInsight:
    return PeriodInsight(
        thesis=banked.thesis,
        evidence=tuple(
            InsightEvidence(item.observation, item.episode_ids, item.asset_ids)
            for item in banked.evidence
        ),
        tensions=banked.tensions,
        recurring_threads=banked.recurring_threads,
        unavailable_reason=None,
        revision=0,
        provenance=provenance,
    )


def _unavailable_result(
    grounding: tuple[PeriodEpisodeGrounding, ...],
    warnings: tuple[str, ...],
    provenance: DecisionProvenance,
    request_trace: RequestTrace,
    reason: str,
) -> TextPeriodInsightResult:
    return TextPeriodInsightResult(
        insight=PeriodInsight(
            thesis=None,
            evidence=(),
            tensions=(),
            recurring_threads=(),
            unavailable_reason=reason,
            revision=0,
            provenance=provenance,
        ),
        episode_grounding=grounding,
        warnings=(*warnings, f"!! {reason}"),
        request_trace=request_trace,
        actual_calls=request_trace.actual_calls,
    )


def _is_truncation(exc: Exception) -> bool:
    text = str(exc).lower()
    return "incomplete" in text or "truncat" in text or "max_tokens" in text


def _derived_producer(producer: PeriodInsightProducer, suffix: str) -> PeriodInsightProducer:
    return PeriodInsightProducer(
        model_id=producer.model_id,
        prompt_version=f"{producer.prompt_version}{suffix}",
        schema_version=producer.schema_version,
    )


def _parts(
    facts: tuple[_PeriodEpisodeFacts, ...],
    limits: TextPeriodRequestLimits,
) -> tuple[tuple[int, int], ...] | None:
    """Fewest contiguous chronological slices whose leaf prompt fits; None when one episode alone does not."""
    parts: list[tuple[int, int]] = []
    start = 0
    while start < len(facts):
        end = start + 1
        while (
            end < len(facts) and len(_prompt_for(facts[start : end + 1])) <= limits.max_prompt_chars
        ):
            end += 1
        if len(_prompt_for(facts[start:end])) > limits.max_prompt_chars:
            return None
        parts.append((start, end))
        start = end
    return tuple(parts)


def _read_part(
    requester: Callable[[str], str],
    facts: tuple[_PeriodEpisodeFacts, ...],
    identity: PeriodInsightIdentity,
    grounding: tuple[PeriodEpisodeGrounding, ...],
    number: int,
    page_warnings: list[str],
) -> BankedPeriodInsight | None:
    try:
        raw = requester(_prompt_for(facts))
    except Exception as exc:  # WHY: one failed page must not hide the other pages' readings
        page_warnings.append(
            f"!! period part {number} provider failed ({type(exc).__name__}: {str(exc)[:160]})"
        )
        return None
    leaf = _read_response(raw, identity, grounding) if isinstance(raw, str) else None
    if leaf is None:
        page_warnings.append(f"!! period part {number} response was missing or invalid")
    return leaf


def _read_parts(
    parts: tuple[tuple[int, int], ...],
    facts: tuple[_PeriodEpisodeFacts, ...],
    grounding: tuple[PeriodEpisodeGrounding, ...],
    *,
    store: PeriodInsightStore,
    leaf_producer: PeriodInsightProducer,
    requester: Callable[[str], str],
    page_warnings: list[str],
) -> tuple[tuple[tuple[tuple[int, int], BankedPeriodInsight], ...], int]:
    calls = 0
    leaves: list[tuple[tuple[int, int], BankedPeriodInsight]] = []
    for number, (start, end) in enumerate(parts, start=1):
        part_grounding = grounding[start:end]
        leaf_identity = PeriodInsightIdentity.from_grounding(
            producer_key=leaf_producer.key(), episodes=part_grounding
        )
        leaf = store.insight_for(leaf_identity)
        if leaf is None:
            calls += 1
            leaf = _read_part(
                requester,
                facts[start:end],
                leaf_identity,
                part_grounding,
                number,
                page_warnings,
            )
            if leaf is None:
                continue
            store.remember(leaf)
        leaves.append(((start, end), leaf))
    return tuple(leaves), calls


def _synthesize_parts(
    leaves: tuple[tuple[tuple[int, int], BankedPeriodInsight], ...],
    grounding: tuple[PeriodEpisodeGrounding, ...],
    composite_identity: PeriodInsightIdentity,
    *,
    store: PeriodInsightStore,
    requester: Callable[[str], str],
    merge_producer: PeriodInsightProducer,
    provenance: DecisionProvenance,
    calls: int,
    warnings: tuple[str, ...],
    page_warnings: list[str],
    pages: int,
) -> TextPeriodInsightResult:
    prompt = _synthesis_prompt(leaves, grounding)
    calls += 1
    request_trace = _request_trace(provenance, merge_producer, actual_calls=calls)
    try:
        raw = requester(prompt)
    except (
        Exception
    ) as exc:  # WHY: the leaves stay banked; the composite is retried on the next run
        return _unavailable_result(
            grounding,
            (*warnings, *page_warnings),
            provenance,
            request_trace,
            f"period synthesis provider failed ({type(exc).__name__}: {str(exc)[:160]})",
        )
    composite = _read_response(raw, composite_identity, grounding) if isinstance(raw, str) else None
    if composite is None:
        # WHY: one repair retry, quoting what was wrong; the leaves stay banked either way
        calls += 1
        request_trace = _request_trace(provenance, merge_producer, actual_calls=calls)
        problem = _response_problem(raw if isinstance(raw, str) else "", grounding)
        try:
            raw = requester(_repair_prompt(prompt, problem, len(grounding)))
        except (
            Exception
        ) as exc:  # WHY: the composite is retried on the next run; the leaves are banked
            return _unavailable_result(
                grounding,
                (*warnings, *page_warnings),
                provenance,
                request_trace,
                f"period synthesis provider failed on repair ({type(exc).__name__}: {str(exc)[:160]})",
            )
        composite = (
            _read_response(raw, composite_identity, grounding) if isinstance(raw, str) else None
        )
        page_warnings.append(f"period synthesis repaired once ({problem})")
    if composite is None:
        return _unavailable_result(
            grounding,
            (*warnings, *page_warnings),
            provenance,
            request_trace,
            f"period synthesis response was missing or invalid ({_response_problem(raw if isinstance(raw, str) else '', grounding)})",
        )
    store.remember(composite)
    return TextPeriodInsightResult(
        insight=_period_insight(composite, provenance),
        episode_grounding=grounding,
        warnings=(*warnings, *page_warnings),
        request_trace=request_trace,
        actual_calls=calls,
        identity=composite_identity,
        pages=pages,
    )


def _paged_period_insight(
    facts: tuple[_PeriodEpisodeFacts, ...],
    grounding: tuple[PeriodEpisodeGrounding, ...],
    *,
    store: PeriodInsightStore,
    producer: PeriodInsightProducer,
    requester: Callable[[str], str],
    limits: TextPeriodRequestLimits,
    input_ids: tuple[str, ...],
    warnings: tuple[str, ...],
) -> TextPeriodInsightResult:
    """Read an oversized period in contiguous parts, then synthesize ONE grounded whole-period insight.

    Leaves are banked under a leaf producer and the exact ordered grounding of their slice; the
    composite is banked under a merge producer and the exact ordered grounding of the whole period,
    so a warm replay costs no call and the planner can retrieve the composite by identity alone.
    """
    leaf_producer = _derived_producer(producer, _LEAF_PROMPT_SUFFIX)
    merge_producer = _derived_producer(producer, _MERGE_PROMPT_SUFFIX)
    composite_identity = PeriodInsightIdentity.from_grounding(
        producer_key=merge_producer.key(),
        episodes=grounding,
    )
    parts = _parts(facts, limits)
    if parts is None:
        provenance = _provenance(
            producer,
            input_ids=input_ids,
            request_key=composite_identity.evidence_key,
            cache_hit=False,
        )
        return _unavailable_result(
            grounding,
            warnings,
            provenance,
            _request_trace(provenance, producer, actual_calls=0),
            "one episode alone exceeds the period request limit",
        )
    banked_composite = store.insight_for(composite_identity)
    if banked_composite is not None:
        provenance = _provenance(
            merge_producer,
            input_ids=input_ids,
            request_key=composite_identity.evidence_key,
            cache_hit=True,
        )
        return TextPeriodInsightResult(
            insight=_period_insight(banked_composite, provenance),
            episode_grounding=grounding,
            warnings=(*warnings, f"period paged into {len(parts)} parts"),
            request_trace=_request_trace(provenance, merge_producer, actual_calls=0),
            actual_calls=0,
            identity=composite_identity,
            pages=len(parts),
        )
    page_warnings: list[str] = [f"period paged into {len(parts)} parts"]
    leaves, calls = _read_parts(
        parts,
        facts,
        grounding,
        store=store,
        leaf_producer=leaf_producer,
        requester=requester,
        page_warnings=page_warnings,
    )
    provenance = _provenance(
        merge_producer,
        input_ids=input_ids,
        request_key=composite_identity.evidence_key,
        cache_hit=False,
    )
    if not leaves:
        return _unavailable_result(
            grounding,
            (*warnings, *page_warnings),
            provenance,
            _request_trace(provenance, merge_producer, actual_calls=calls),
            "no period part produced a reading",
        )
    return _synthesize_parts(
        leaves,
        grounding,
        composite_identity,
        store=store,
        requester=requester,
        merge_producer=merge_producer,
        provenance=provenance,
        calls=calls,
        warnings=warnings,
        page_warnings=page_warnings,
        pages=len(parts),
    )
