"""Hierarchical visual reading of every source-eligible episode."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from immich_memories.analysis.contact_sheets import (
    MAX_SHEET_TILES,
    ContactSheetPage,
    TileRef,
    build_contact_sheets,
)
from immich_memories.analysis.cull_answer import fused_episode_response_fits
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
    PeriodInsight,
    RequestTrace,
)
from immich_memories.analysis.editorial_gateway import (
    BankedVisualAnswer,
    EditorialGateway,
    VisualEditorialRequest,
)
from immich_memories.analysis.episode_scan_request import build_episode_request
from immich_memories.analysis.period_insight_answer import (
    PERIOD_INSIGHT_SCHEMA_VERSION,
    EpisodePageReading,
    read_episode_answers,
    read_period_answer,
)
from immich_memories.analysis.selection_flow import EditorialGroup, PreparedEditorialSource
from immich_memories.analysis.visual_atlas import VisualAtlas, build_visual_atlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits

PASS_ZERO_VERSION = "pass-0-v1"  # noqa: S105 - editorial pass identity
PERIOD_INSIGHT_PASS_VERSION = "period-insight-v1"  # noqa: S105 - editorial pass identity
PERIOD_INSIGHT_PROMPT_VERSION = "period-insight-prompt-v1"
_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1"


@dataclass(frozen=True)
class EpisodeSheet:
    """Every chronological contact-sheet page for one source episode."""

    episode_id: str
    candidates: tuple[EditorialCandidate, ...]
    pages: tuple[ContactSheetPage, ...]


@dataclass(frozen=True)
class EpisodePackScope:
    """The exact numbered tiles belonging to one episode on a shared pack page."""

    episode_id: str
    page_id: str
    episode_alias: int
    page_alias: int
    tile_refs: tuple[TileRef, ...]
    candidates: tuple[EditorialCandidate, ...]
    unavailable_asset_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpisodeScanPack:
    """One physical chronological page containing only complete episode groups."""

    pack_id: str
    page: ContactSheetPage
    scopes: tuple[EpisodePackScope, ...]
    continuation_number: int = 1
    continuation_count: int = 1


@dataclass(frozen=True)
class BankedEpisodeScan:
    """One reusable physical episode response and the request that produced it."""

    pack_id: str
    page_id: str
    answer: BankedVisualAnswer


@dataclass(frozen=True)
class EpisodeScanAttempt:
    """One pack's exact physical attempt, including fail-open transport failures."""

    pack_id: str
    page_id: str
    answer: BankedVisualAnswer | None
    request_trace: RequestTrace | None


@dataclass(frozen=True)
class EpisodePageObservation:
    """Whether one required visual page produced a valid episode namespace."""

    episode_id: str
    page_id: str
    reading: EpisodePageReading | None
    scan: BankedEpisodeScan | None


@dataclass(frozen=True)
class EpisodeReading:
    """The combined observations from every page of one complete episode."""

    episode_id: str
    visual_summary: str
    representative_asset_ids: tuple[str, ...]
    representative_reasons: tuple[str, ...]
    page_provenances: tuple[DecisionProvenance, ...]


@dataclass(frozen=True)
class PassZeroResult:
    """Observation-only Pass 0 result; membership remains the prepared corpus."""

    retained_ids: tuple[str, ...]
    atlas: VisualAtlas
    episode_sheets: tuple[EpisodeSheet, ...]
    episode_packs: tuple[EpisodeScanPack, ...]
    scan_attempts: tuple[EpisodeScanAttempt, ...]
    page_observations: tuple[EpisodePageObservation, ...]
    episode_readings: tuple[EpisodeReading, ...]
    banked_scans: tuple[BankedEpisodeScan, ...]
    period_pages: tuple[ContactSheetPage, ...]
    period_answer: BankedVisualAnswer | None
    insight: PeriodInsight
    warnings: tuple[str, ...]
    actual_calls: int

    def __post_init__(self) -> None:
        if len(self.retained_ids) != len(set(self.retained_ids)):
            raise ValueError("Pass 0 retained IDs must be unique")
        episode_ids = {sheet.episode_id for sheet in self.episode_sheets}
        if any(reading.episode_id not in episode_ids for reading in self.episode_readings):
            raise ValueError("Pass 0 readings must belong to rendered episodes")
        scan_ids = tuple((scan.pack_id, scan.page_id) for scan in self.banked_scans)
        if len(scan_ids) != len(set(scan_ids)):
            raise ValueError("Pass 0 banked scan identities must be unique")
        attempt_ids = tuple((attempt.pack_id, attempt.page_id) for attempt in self.scan_attempts)
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("Pass 0 scan attempt identities must be unique")


def run_period_insight(
    prepared: PreparedEditorialSource,
    *,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    frame_cache_dir: Path | None,
    limits: VisionRequestLimits | None = None,
) -> PassZeroResult:
    """Read every required episode page before attempting a period thesis."""
    request_limits = limits or VisionRequestLimits(max_output_tokens=4000, timeout_seconds=120)
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=frame_cache_dir)
    warnings: list[str] = []
    for tile in atlas.tiles:
        if tile.kind == "unavailable":
            _warn_once(
                prepared,
                warnings,
                f"Pass 0 visual unavailable: {tile.entity_id} ({tile.unavailable_reason})",
            )
    # Nothing showable is a fact about the file, not a judgement about it, so it
    # leaves before any pass is asked anything. Carried onto a sheet it became a
    # numbered placeholder the model could promote as a representative, and it
    # voided its whole episode's reading: seventeen such assets in a real dense
    # month cost that month its thesis.
    viewable = _viewable_groups(prepared.episode_groups, atlas)
    retained_ids = tuple(candidate.asset_id for group in viewable for candidate in group.candidates)
    episode_sheets, episode_packs = _episode_material(
        viewable,
        atlas=atlas,
        output_dir=sheet_output_dir / "episodes",
        limits=request_limits,
    )
    request_start = len(prepared.trace.requests)
    observations, banked, scan_attempts = _read_episode_packs(
        episode_packs,
        requester=requester,
        limits=request_limits,
    )
    readings = _complete_episode_readings(episode_sheets, observations)
    period_pages: tuple[ContactSheetPage, ...] = ()
    period_answer: BankedVisualAnswer | None = None
    insight: PeriodInsight | None = None
    if not episode_sheets:
        insight = _unavailable_insight(
            prepared,
            episode_packs,
            banked,
            reason="source corpus was empty",
        )
    elif not readings:
        _warn_once(
            prepared,
            warnings,
            "Pass 0 no episode could be read; period thesis unavailable",
        )
        insight = _unavailable_insight(
            prepared,
            episode_packs,
            banked,
            reason="no episode page could be read",
        )
    else:
        # An episode nobody could read costs its own pictures a reading, not the
        # month its thesis: a real dense month read 100 of 101 and was refused a
        # thesis over the hundred-and-first. The wall is built from what was
        # read, the trace names what was not, and those pictures stay in the
        # corpus for the passes that come after.
        if len(readings) != len(episode_sheets):
            _warn_once(
                prepared,
                warnings,
                f"Pass 0 {len(episode_sheets) - len(readings)} of "
                f"{len(episode_sheets)} episodes could not be read",
            )
        period_pages, period_answer, insight = _read_period_wall(
            prepared,
            readings,
            atlas=atlas,
            requester=requester,
            output_dir=sheet_output_dir / "period",
            limits=request_limits,
        )
        if period_answer is None or insight is None:
            _warn_once(
                prepared,
                warnings,
                "Pass 0 period synthesis unreadable; thesis unavailable",
            )
            insight = _unavailable_insight(
                prepared,
                episode_packs,
                banked,
                period_pages=period_pages,
                period_answer=period_answer,
                reason="period synthesis was unreadable",
            )
    assert insight is not None
    provenance = _pass_provenance(
        prepared,
        episode_packs,
        banked,
        period_pages=period_pages,
        period_answer=period_answer,
    )
    logical_answers = tuple(scan.answer for scan in banked) + (
        (period_answer,) if period_answer is not None else ()
    )
    logical_requests = tuple(
        replace(answer.request_trace, actual_calls=0, attempts=()) for answer in logical_answers
    )
    _record_pass_zero(prepared, provenance, logical_requests)
    physical_requests = prepared.trace.requests[request_start:]
    return PassZeroResult(
        retained_ids=retained_ids,
        atlas=atlas,
        episode_sheets=episode_sheets,
        episode_packs=episode_packs,
        scan_attempts=scan_attempts,
        page_observations=observations,
        episode_readings=readings,
        banked_scans=banked,
        period_pages=period_pages,
        period_answer=period_answer,
        insight=insight,
        warnings=tuple(warnings),
        actual_calls=sum(request.actual_calls for request in physical_requests),
    )


def _read_period_wall(
    prepared: PreparedEditorialSource,
    readings: tuple[EpisodeReading, ...],
    *,
    atlas: VisualAtlas,
    requester: EditorialGateway,
    output_dir: Path,
    limits: VisionRequestLimits,
) -> tuple[tuple[ContactSheetPage, ...], BankedVisualAnswer | None, PeriodInsight | None]:
    candidates = {candidate.asset_id: candidate for candidate in prepared.candidates}
    episode_by_asset = {
        asset_id: reading.episode_id
        for reading in readings
        for asset_id in reading.representative_asset_ids
    }
    representative_ids = tuple(
        sorted(
            episode_by_asset,
            key=lambda asset_id: (candidates[asset_id].taken_at, asset_id),
        )
    )
    period_pages = build_contact_sheets(
        tuple(atlas.tile_for(asset_id) for asset_id in representative_ids),
        "period-wall",
        output_dir,
    )
    if not period_pages or len(period_pages) > limits.max_pages_per_request:
        return period_pages, None, None
    upstream = tuple(
        f"episode:{reading.episode_id} | {reading.visual_summary} | "
        f"representatives:{','.join(reading.representative_asset_ids)} | "
        f"reasons:{' / '.join(reading.representative_reasons)}"
        for reading in readings
    )
    request = VisualEditorialRequest(
        pass_name="period-insight",  # noqa: S106 - versioned editorial pass identity
        pass_version=PERIOD_INSIGHT_PASS_VERSION,
        prompt=_period_prompt(upstream, period_pages),
        prompt_version=PERIOD_INSIGHT_PROMPT_VERSION,
        schema_version=PERIOD_INSIGHT_SCHEMA_VERSION,
        pages=period_pages,
        ordered_input_ids=representative_ids,
        ordered_group_ids=tuple(reading.episode_id for reading in readings),
        grounded_annotations=upstream,
        upstream_material=upstream,
        render_version=_RENDER_VERSION,
        limits=limits,
    )
    try:
        answer = requester.ask(request)
    except Exception:  # WHY: a missing thesis cannot remove otherwise valid source membership
        return period_pages, None, None
    tile_map = {
        (page.sheet_id, ref.number): (episode_by_asset[ref.entity_id], ref.entity_id)
        for page in period_pages
        for ref in page.tile_refs
    }
    parsed = read_period_answer(
        answer.raw_text,
        page_ids=tuple(page.sheet_id for page in period_pages),
        tile_map=tile_map,
    )
    if parsed is None:
        return period_pages, answer, None
    return (
        period_pages,
        answer,
        PeriodInsight(
            thesis=parsed.thesis,
            evidence=parsed.evidence,
            tensions=parsed.tensions,
            recurring_threads=parsed.recurring_threads,
            unavailable_reason=parsed.unavailable_reason,
            revision=0,
            provenance=answer.provenance,
        ),
    )


def period_response_shape(*, tile: int) -> str:
    """One complete example envelope for the period synthesis.

    Its nesting and its scalar types are the part a prose description leaves to
    the model, and the parser voids the whole answer on either.
    """
    return json.dumps(
        {
            "schema_version": PERIOD_INSIGHT_SCHEMA_VERSION,
            "period_insight": {
                "thesis": "what this period was about, or null",
                "evidence": [
                    {
                        "observation": "what these tiles show",
                        "representative_tiles": [tile],
                    }
                ],
                "tensions": ["one sentence, as plain text"],
                "recurring_threads": ["one sentence, as plain text"],
                "unavailable_reason": None,
            },
        },
        separators=(",", ":"),
    )


def _period_prompt(upstream: tuple[str, ...], pages: tuple[ContactSheetPage, ...]) -> str:
    page_names = ", ".join(page.sheet_id for page in pages)
    return (
        "Read this complete chronological period wall of actual representative pixels. The prior "
        "episode observations follow as grounded context, not substitutes for the images:\n"
        + "\n".join(upstream)
        + f"\nReturn one complete JSON object for pages {page_names}. A null thesis is honest "
        "when the material does not support one, and it is the only case that fills "
        "unavailable_reason. tensions and recurring_threads are lists of plain sentences. "
        "Use exactly this shape and these keys:\n" + period_response_shape(tile=1)
    )


def _viewable_groups(
    groups: tuple[EditorialGroup, ...], atlas: VisualAtlas
) -> tuple[EditorialGroup, ...]:
    """The same groups without the candidates that have no pixels to show."""
    kept = []
    for group in groups:
        members = tuple(
            candidate
            for candidate in group.candidates
            if atlas.tile_for(candidate.asset_id).kind != "unavailable"
        )
        if members:
            kept.append(replace(group, candidates=members))
    return tuple(kept)


def _episode_material(
    groups: tuple[EditorialGroup, ...],
    *,
    atlas: VisualAtlas,
    output_dir: Path,
    limits: VisionRequestLimits,
) -> tuple[tuple[EpisodeSheet, ...], tuple[EpisodeScanPack, ...]]:
    sheets: list[EpisodeSheet] = []
    packs: list[EpisodeScanPack] = []
    pending: list[EditorialGroup] = []
    pending_count = 0

    def flush_pending() -> None:
        nonlocal pending, pending_count
        if not pending:
            return
        page, scopes = _shared_pack_page(tuple(pending), atlas=atlas, output_dir=output_dir)
        pack_id = page.sheet_id.removesuffix("-001")
        packs.append(EpisodeScanPack(pack_id, page, scopes))
        sheets.extend(EpisodeSheet(group.group_id, group.candidates, (page,)) for group in pending)
        pending, pending_count = [], 0

    for group in groups:
        size = len(group.candidates)
        if size > MAX_SHEET_TILES or not _episode_groups_fit((group,), limits):
            flush_pending()
            episode_sheet, continuation_packs = _episode_continuation_material(
                group,
                atlas=atlas,
                output_dir=output_dir,
                limits=limits,
            )
            sheets.append(episode_sheet)
            packs.extend(continuation_packs)
            continue
        proposed = (*pending, group)
        if pending and (
            pending_count + size > MAX_SHEET_TILES or not _episode_groups_fit(proposed, limits)
        ):
            flush_pending()
        pending.append(group)
        pending_count += size
    flush_pending()
    return tuple(sheets), tuple(packs)


def _episode_continuation_material(
    group: EditorialGroup,
    *,
    atlas: VisualAtlas,
    output_dir: Path,
    limits: VisionRequestLimits,
) -> tuple[EpisodeSheet, tuple[EpisodeScanPack, ...]]:
    size = len(group.candidates)
    page_sizes = _continuation_page_sizes(size, limits)
    pages = build_contact_sheets(
        tuple(atlas.tile_for(candidate.asset_id) for candidate in group.candidates),
        _pack_id((group.group_id,)),
        output_dir,
        page_sizes=page_sizes,
    )
    packs = tuple(
        _episode_continuation_pack(
            group,
            page=page,
            number=number,
            count=len(pages),
            atlas=atlas,
        )
        for number, page in enumerate(pages, start=1)
    )
    return EpisodeSheet(group.group_id, group.candidates, pages), packs


def _continuation_page_sizes(size: int, limits: VisionRequestLimits) -> tuple[int, ...]:
    sizes: list[int] = []
    offset = 0
    while offset < size:
        count = min(MAX_SHEET_TILES, size - offset)
        while count and not _episode_response_fits(
            (tuple(range(offset + 1, offset + count + 1)),), limits
        ):
            count -= 1
        if count == 0:
            raise ValueError("episode scan output budget cannot fit one continuation tile")
        sizes.append(count)
        offset += count
    return tuple(sizes)


def _episode_continuation_pack(
    group: EditorialGroup,
    *,
    page: ContactSheetPage,
    number: int,
    count: int,
    atlas: VisualAtlas,
) -> EpisodeScanPack:
    page_asset_ids = {ref.entity_id for ref in page.tile_refs}
    page_candidates = tuple(
        candidate for candidate in group.candidates if candidate.asset_id in page_asset_ids
    )
    unavailable_asset_ids = tuple(
        candidate.asset_id
        for candidate in page_candidates
        if atlas.tile_for(candidate.asset_id).kind == "unavailable"
    )
    return EpisodeScanPack(
        pack_id=page.sheet_id.rsplit("-", 1)[0],
        page=page,
        scopes=(
            EpisodePackScope(
                group.group_id,
                page.sheet_id,
                1,
                1,
                page.tile_refs,
                page_candidates,
                unavailable_asset_ids,
            ),
        ),
        continuation_number=number,
        continuation_count=count,
    )


def _shared_pack_page(
    groups: tuple[EditorialGroup, ...],
    *,
    atlas: VisualAtlas,
    output_dir: Path,
) -> tuple[ContactSheetPage, tuple[EpisodePackScope, ...]]:
    candidates = tuple(
        sorted(
            (candidate for group in groups for candidate in group.candidates),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    pages = build_contact_sheets(
        tuple(atlas.tile_for(candidate.asset_id) for candidate in candidates),
        _pack_id(tuple(group.group_id for group in groups)),
        output_dir,
    )
    if len(pages) != 1:
        raise ValueError("complete episode pack must fit one contact-sheet page")
    page = pages[0]
    episode_by_asset = {
        candidate.asset_id: group.group_id for group in groups for candidate in group.candidates
    }
    scopes = tuple(
        EpisodePackScope(
            episode_id=group.group_id,
            page_id=page.sheet_id,
            episode_alias=episode_alias,
            page_alias=1,
            tile_refs=tuple(
                ref for ref in page.tile_refs if episode_by_asset[ref.entity_id] == group.group_id
            ),
            candidates=group.candidates,
            unavailable_asset_ids=tuple(
                candidate.asset_id
                for candidate in group.candidates
                if atlas.tile_for(candidate.asset_id).kind == "unavailable"
            ),
        )
        for episode_alias, group in enumerate(groups, start=1)
    )
    return page, scopes


def _pack_id(group_ids: tuple[str, ...]) -> str:
    digest = sha256("\x00".join(group_ids).encode()).hexdigest()
    return f"episode-pack-v1-{digest}"


# The output budget bounds how much a pack can SAY, not how much it can be asked
# about. Measured at temperature 0 on two real months: a 36-episode pack came
# back complete, valid, and with every Cull list empty -- no refusal, no warning,
# just silence on the second half of the question. The same month split into six
# smaller packs culled 4.6%, and a dense month whose packs held 4 to 14 episodes
# culled 4.4%. Fourteen answered; thirty-six did not.
MAX_EPISODES_PER_PACK = 14


def _episode_groups_fit(groups: tuple[EditorialGroup, ...], limits: VisionRequestLimits) -> bool:
    if len(groups) > MAX_EPISODES_PER_PACK:
        return False
    candidates = tuple(
        sorted(
            (candidate for group in groups for candidate in group.candidates),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    number_by_asset = {
        candidate.asset_id: number for number, candidate in enumerate(candidates, start=1)
    }
    displayed_by_episode = tuple(
        tuple(number_by_asset[candidate.asset_id] for candidate in group.candidates)
        for group in groups
    )
    return _episode_response_fits(displayed_by_episode, limits)


def _episode_response_fits(
    displayed_by_episode: tuple[tuple[int, ...], ...], limits: VisionRequestLimits
) -> bool:
    return fused_episode_response_fits(
        displayed_by_episode,
        max_output_tokens=limits.max_output_tokens,
    )


def _read_episode_packs(
    packs: tuple[EpisodeScanPack, ...],
    *,
    requester: EditorialGateway,
    limits: VisionRequestLimits,
) -> tuple[
    tuple[EpisodePageObservation, ...],
    tuple[BankedEpisodeScan, ...],
    tuple[EpisodeScanAttempt, ...],
]:
    observations: list[EpisodePageObservation] = []
    banked: list[BankedEpisodeScan] = []
    attempts: list[EpisodeScanAttempt] = []
    for pack in packs:
        scan: BankedEpisodeScan | None = None
        parsed = None
        request_trace: RequestTrace | None = None
        try:
            answer = requester.ask(build_episode_request(pack, limits=limits))
        except Exception as exc:  # WHY: optional model failure cannot remove source membership
            answer = None
            request_trace = getattr(exc, "request_trace", None)
            if not isinstance(request_trace, RequestTrace):
                request_trace = None
        if answer is not None:
            scan = BankedEpisodeScan(pack.pack_id, pack.page.sheet_id, answer)
            banked.append(scan)
            tile_map = {
                (scope.episode_alias, scope.page_alias, ref.number): ref.entity_id
                for scope in pack.scopes
                for ref in scope.tile_refs
            }
            observation_map = {
                (scope.episode_alias, scope.page_alias): (scope.episode_id, scope.page_id)
                for scope in pack.scopes
            }
            parsed = read_episode_answers(
                answer.raw_text,
                pack_alias=1,
                expected_observations=tuple(
                    (scope.episode_id, scope.page_id) for scope in pack.scopes
                ),
                observation_map=observation_map,
                tile_map=tile_map,
            )
            request_trace = answer.request_trace
        attempts.append(EpisodeScanAttempt(pack.pack_id, pack.page.sheet_id, answer, request_trace))
        by_identity = {
            (reading.episode_id, reading.page_id): reading
            for reading in (() if parsed is None else parsed.readings)
        }
        invalid_observations = set(() if parsed is None else parsed.invalid_observations)
        observations.extend(
            EpisodePageObservation(
                scope.episode_id,
                scope.page_id,
                # Unviewable candidates left before the sheet was built, so a
                # scope with no tiles is an empty episode rather than a blind one.
                None
                if not scope.tile_refs or (scope.episode_id, scope.page_id) in invalid_observations
                else by_identity.get((scope.episode_id, scope.page_id)),
                scan,
            )
            for scope in pack.scopes
        )
    return tuple(observations), tuple(banked), tuple(attempts)


def _complete_episode_readings(
    episodes: tuple[EpisodeSheet, ...],
    observations: tuple[EpisodePageObservation, ...],
) -> tuple[EpisodeReading, ...]:
    readings: list[EpisodeReading] = []
    for episode in episodes:
        page_observations = tuple(
            observation
            for observation in observations
            if observation.episode_id == episode.episode_id
        )
        if len(page_observations) != len(episode.pages) or any(
            observation.reading is None or observation.scan is None
            for observation in page_observations
        ):
            continue
        page_readings: list[EpisodePageReading] = []
        page_provenances: list[DecisionProvenance] = []
        for observation in page_observations:
            assert observation.reading is not None
            assert observation.scan is not None
            page_readings.append(observation.reading)
            page_provenances.append(observation.scan.answer.provenance)
        readings.append(
            EpisodeReading(
                episode_id=episode.episode_id,
                visual_summary=" ".join(
                    page_reading.visual_summary for page_reading in page_readings
                ),
                representative_asset_ids=tuple(
                    asset_id
                    for page_reading in page_readings
                    for asset_id in page_reading.representative_asset_ids
                ),
                representative_reasons=tuple(
                    page_reading.representative_reason for page_reading in page_readings
                ),
                page_provenances=tuple(page_provenances),
            )
        )
    return tuple(readings)


def _pass_provenance(
    prepared: PreparedEditorialSource,
    packs: tuple[EpisodeScanPack, ...],
    scans: tuple[BankedEpisodeScan, ...],
    *,
    period_pages: tuple[ContactSheetPage, ...] = (),
    period_answer: BankedVisualAnswer | None = None,
) -> DecisionProvenance:
    answers = tuple(scan.answer for scan in scans) + (
        (period_answer,) if period_answer is not None else ()
    )
    request_keys = tuple(answer.provenance.request_key for answer in answers)
    pages = (*(pack.page for pack in packs), *period_pages)
    return DecisionProvenance(
        pass_name="pass-0",  # noqa: S106 - public editorial pass identity
        pass_version=PASS_ZERO_VERSION,
        schema_version="period-insight-v1",
        model_identity=answers[0].provenance.model_identity if answers else "",
        input_ids=prepared.candidate_ids,
        sheet_hashes=tuple(page.sha256 for page in pages),
        request_key=sha256("\x00".join(request_keys).encode()).hexdigest(),
        cache_hit=bool(answers) and all(answer.provenance.cache_hit for answer in answers),
    )


def _unavailable_insight(
    prepared: PreparedEditorialSource,
    packs: tuple[EpisodeScanPack, ...],
    scans: tuple[BankedEpisodeScan, ...],
    *,
    reason: str,
    period_pages: tuple[ContactSheetPage, ...] = (),
    period_answer: BankedVisualAnswer | None = None,
) -> PeriodInsight:
    return PeriodInsight(
        thesis=None,
        evidence=(),
        tensions=(),
        recurring_threads=(),
        unavailable_reason=reason,
        revision=0,
        provenance=_pass_provenance(
            prepared,
            packs,
            scans,
            period_pages=period_pages,
            period_answer=period_answer,
        ),
    )


def _record_pass_zero(
    prepared: PreparedEditorialSource,
    provenance: DecisionProvenance,
    request_traces: tuple[RequestTrace, ...],
) -> None:
    prepared.trace.record_editorial_pass(
        PassTrace(
            name="pass-0",
            input_ids=prepared.candidate_ids,
            kept_ids=prepared.candidate_ids,
            rejected=(),
            unresolved=(),
            duration_before=sum(item.shippable_duration for item in prepared.candidates),
            duration_after=sum(item.shippable_duration for item in prepared.candidates),
            provenance=provenance,
            request_traces=request_traces,
        )
    )


def _warn_once(
    prepared: PreparedEditorialSource,
    result_warnings: list[str],
    message: str,
) -> None:
    marked = f"!! {message}"
    if marked not in prepared.trace.warnings:
        prepared.trace.warnings.append(marked)
    if marked not in result_warnings:
        result_warnings.append(marked)
