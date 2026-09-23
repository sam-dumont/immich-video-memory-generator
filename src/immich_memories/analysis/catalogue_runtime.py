"""Connect the period-account writer to the product's own source and episode readers.

Two callers reach the same writer. `prepare --overviews` reads a whole window's episodes and
banks an account per month it spans. A film run that finds its period unaccounted for banks the
one account it needs from the readings its own event pass has already paid for.

Both write through the identity in `library_catalogue`, so whichever runs first pays and the
other reads. The no-model reader writes nothing: an account is a reading, and there is no thesis
without a reader.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.editorial_text_gateway import (
    SyncTextPromptRequester,
    semantic_text_model_identity,
)
from immich_memories.analysis.library_catalogue import (
    LibraryCatalogue,
    LibraryEpisode,
    bank_month_accounts,
    bank_window_accounts,
    build_catalogue,
    window_bounds,
)
from immich_memories.analysis.text_episode_paging import TEXT_EPISODE_MAX_OUTPUT_TOKENS
from immich_memories.store.episode_readings import (
    EpisodeReadingIdentity,
    EpisodeReadingStore,
)
from immich_memories.store.library_catalogue import CatalogueStore, LibraryAccount

if TYPE_CHECKING:
    from immich_memories.analysis.selection_source import PreparedEditorialSource, SourceScope
    from immich_memories.api.models import Asset, VideoClipInfo
    from immich_memories.config_loader import Config


def catalogue_requester(config: Config) -> SyncTextPromptRequester:
    """Use the configured text provider and the existing cost and transport accounting."""
    if config.editorial.resolve_reader(config.llm.model) == "rules":
        raise ValueError("Library overviews need a configured model reader")
    return SyncTextPromptRequester(
        config.llm,
        max_tokens=TEXT_EPISODE_MAX_OUTPUT_TOKENS,
        timeout_seconds=config.llm.timeout_seconds,
        thinking=False,
        retry_larger=False,
    )


def catalogue_prepared_window(
    sources: Sequence[Asset | VideoClipInfo],
    *,
    scope: SourceScope,
    config: Config,
    unservable: Mapping[str, Any] | None = None,
    albums: Callable[[Sequence[str]], tuple[str, ...]] | None = None,
    requester: Callable[[str], str] | None = None,
) -> tuple[LibraryCatalogue, tuple[str, ...]]:
    """Read this window's episodes and bank an account for every month it covers.

    Returns the catalogue and the episodes that stayed unread; an unread episode keeps its
    membership and simply contributes nothing to its month's account.
    """
    prepared = _admitted(sources, scope, _screen_documents(sources, scope, config, unservable))
    store_path = config.editorial.resolve_annotation_database(config.cache.cache_path)
    ask = requester or catalogue_requester(config)
    episodes, unread = _banked_readings(prepared, config=config, requester=ask, albums=albums)
    if not episodes:
        return LibraryCatalogue((), {}, {}), unread
    with closing(CatalogueStore(store_path)) as store:
        catalogue = build_catalogue(
            episodes,
            store=store,
            requester=ask,
            producer=semantic_text_model_identity(config.llm, thinking=False),
        )
    return catalogue, unread


def catalogue_banked_episodes(
    identities: Sequence[EpisodeReadingIdentity],
    *,
    store_path: Path,
    capture_dates: Mapping[str, datetime],
    config: Config,
    requester: Callable[[str], str] | None = None,
    unread_facts: Sequence[LibraryAccount] = (),
    period: str = "",
) -> dict[str, LibraryAccount]:
    """Bank the accounts a film of `period` reads, from readings a run has already paid for.

    Nothing is re-read here: the only requests are the accounts themselves. A month's film
    banks one per month the readings or the `unread_facts` span, and leaves the year alone, so
    one month's reading never stands in for its year. A year's film ("2024") also banks the
    year over those months. A window over several years ("2005-12-03..2026-09-23") banks one
    account per year it touches and one for the window, never one per month.
    """
    with closing(EpisodeReadingStore(store_path)) as bank:
        readings = bank.readings_for(tuple(identities))
    episodes = [
        LibraryEpisode(reading=reading, taken_at=taken)
        for reading in readings.values()
        if (taken := _earliest(reading.full_asset_ids, capture_dates)) is not None
    ]
    if not episodes and not unread_facts:
        return {}
    options: dict[str, Any] = {
        "requester": requester or catalogue_requester(config),
        "producer": semantic_text_model_identity(config.llm, thinking=False),
        "unread_facts": unread_facts,
    }
    with closing(CatalogueStore(store_path)) as store:
        if window_bounds(period) is not None:
            return bank_window_accounts(episodes, store=store, period=period, **options)
        if len(period) != 4:
            return bank_month_accounts(episodes, store=store, **options)
        catalogue = build_catalogue(episodes, store=store, **options)
    return {**catalogue.months, **catalogue.years}


def banked_notable_records(
    identities: Sequence[EpisodeReadingIdentity], *, store_path: Path
) -> dict[str, str]:
    """What these readings recorded as a moment worth a place of its own, by picture.

    A reading that named none is an episode nothing stood out in. A bank written before the
    reading was asked the question has an empty lane and reads the same way.
    """
    with closing(EpisodeReadingStore(store_path)) as bank:
        readings = bank.readings_for(tuple(identities))
    return {
        moment.asset_id: moment.reason
        for reading in readings.values()
        for moment in reading.notable_moments
    }


def _earliest(asset_ids: Sequence[str], capture_dates: Mapping[str, datetime]) -> datetime | None:
    """The episode's own first capture, or nothing when a member is outside this corpus."""
    dates = [capture_dates[asset_id] for asset_id in asset_ids if asset_id in capture_dates]
    return min(dates) if len(dates) == len(asset_ids) and dates else None


def _admitted(
    sources: Sequence[Asset | VideoClipInfo],
    scope: SourceScope,
    exclusions: Mapping[str, Any],
) -> PreparedEditorialSource:
    from immich_memories.analysis.selection_source import (
        EditorialDependencies,
        EditorialSelectionRequest,
        prepare_editorial_source,
    )

    return prepare_editorial_source(
        EditorialSelectionRequest(scope=scope, evidence_exclusions=dict(exclusions)),
        EditorialDependencies(source_fetcher=lambda _scope: sources),
    )


def _screen_documents(
    sources: Sequence[Asset | VideoClipInfo],
    scope: SourceScope,
    config: Config,
    unservable: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The same gate a cut applies, so both read one corpus and one set of episodes."""
    from immich_memories.analysis.editorial_source_gate import screen_document_rejections

    exclusions: dict[str, Any] = dict(unservable or {})
    preliminary = _admitted(sources, scope, exclusions)
    readable = tuple(a for a in preliminary.candidate_ids if a not in exclusions)
    if not readable:
        return exclusions
    lines = _annotations(preliminary, config).lines_for(readable)
    return exclusions | screen_document_rejections(lines)


def _annotations(prepared: PreparedEditorialSource, config: Config):
    from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
    from immich_memories.people.context import load_people_prompt_context

    editorial = config.editorial
    return StoredAnnotationLineReader(
        store_path=editorial.resolve_annotation_database(config.cache.cache_path),
        candidates=prepared.candidates,
        description_model=editorial.description_model,
        head_versions=editorial.head_versions,
        pixel_producer_key=editorial.pixel_producer_key,
        people_context=load_people_prompt_context(include_derived=True),
    )


def _banked_readings(
    prepared: PreparedEditorialSource,
    *,
    config: Config,
    requester: Callable[[str], str],
    albums: Callable[[Sequence[str]], tuple[str, ...]] | None,
) -> tuple[list[LibraryEpisode], tuple[str, ...]]:
    from immich_memories.analysis.selection_source_groups import project_episode_groups
    from immich_memories.analysis.text_episode_answers import TEXT_EPISODE_SCHEMA_VERSION
    from immich_memories.analysis.text_episode_prompt import TEXT_EPISODE_PROMPT_VERSION
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
    from immich_memories.store.episode_readings import EpisodeReadingProducer

    annotations = _annotations(prepared, config)
    contract = annotations.contract
    producer = EpisodeReadingProducer(
        model_id=semantic_text_model_identity(config.llm, thinking=False),
        prompt_version=TEXT_EPISODE_PROMPT_VERSION,
        schema_version=TEXT_EPISODE_SCHEMA_VERSION,
        annotation_renderer_version=contract.renderer_version,
        annotation_versions=contract.producer_versions,
    )
    store_path = config.editorial.resolve_annotation_database(config.cache.cache_path)
    with closing(EpisodeReadingStore(store_path)) as store:
        result = CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=annotations,
            requester=requester,
            albums=albums,
        ).read(project_episode_groups(prepared, prepared.candidate_ids))
    dates = {candidate.asset_id: candidate.taken_at for candidate in prepared.candidates}
    episodes = [
        LibraryEpisode(
            reading=evidence.reading,
            taken_at=min(dates[asset_id] for asset_id in evidence.reading.full_asset_ids),
        )
        for evidence in result.episodes
        if evidence.reading is not None
    ]
    unread = tuple(
        evidence.projection.group.group_id
        for evidence in result.episodes
        if evidence.reading is None
    )
    return episodes, unread
