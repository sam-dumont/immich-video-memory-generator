"""Replaceable production edges for the store-backed editorial planner."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.editorial_attached_outcomes import AttachedAttemptOutcomes
from immich_memories.analysis.editorial_attached_samples import AttachedVideoSamples
from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_final_attached import FinalAttachedPictures
from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.editorial_sampled_pair_confirmation import CachedSampledPairConfirmer
from immich_memories.analysis.editorial_source import (
    FullEditorialSource,
    fetch_full_window_source,
)
from immich_memories.analysis.editorial_story_motion import StoryMotionFacts
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningInput,
    StructurePlanningResult,
)
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.editorial_text_gateway import SyncTextPromptRequester
from immich_memories.analysis.llm_batch import BatchCoordinator, BatchPolicy
from immich_memories.analysis.selection_source import SourceScope
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.text_episode_reader import TEXT_EPISODE_MAX_OUTPUT_TOKENS
from immich_memories.analysis.text_period_insight import TEXT_PERIOD_MAX_OUTPUT_TOKENS
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.people.context import PersonPromptContext, load_people_prompt_context
from immich_memories.store.episode_readings import EpisodeReadingStore
from immich_memories.store.period_insights import PeriodInsightStore

if TYPE_CHECKING:
    from immich_memories.config_loader import Config


def _load_people() -> Mapping[str, PersonPromptContext]:
    return load_people_prompt_context(include_derived=True)


@dataclass(frozen=True, slots=True)
class EditorialRuntimePorts:
    """Explicit replaceable edges around production I/O, suitable for public tests."""

    load_people: Callable[[], Mapping[str, PersonPromptContext]] = _load_people
    fetch_preview: Callable[[Any, str], bytes | None] = lambda client, asset_id: (
        client.get_asset_thumbnail(asset_id, size="preview")
    )
    fetch_full_source: Callable[
        [FullEditorialSource, SourceScope], Sequence[Asset | VideoClipInfo]
    ] = fetch_full_window_source
    # The episode reads are the one stage with a fan-out worth queueing, so the
    # batch coordinator is built here and nowhere else. The period account is a
    # single prompt and the story picks read the stages before them.
    episode_requester_factory: Callable[[Config], Callable[[str], str]] = lambda config: (
        SyncTextPromptRequester(
            config.llm,
            max_tokens=TEXT_EPISODE_MAX_OUTPUT_TOKENS,
            timeout_seconds=config.llm.timeout_seconds,
            thinking=False,
            batch=BatchCoordinator(config.llm, BatchPolicy.from_config(config.llm)),
        )
    )
    period_requester_factory: Callable[[Config], Callable[[str], str]] = lambda config: (
        SyncTextPromptRequester(
            config.llm,
            max_tokens=TEXT_PERIOD_MAX_OUTPUT_TOKENS,
            timeout_seconds=config.llm.timeout_seconds,
            thinking=False,
            # The period reader handles overflow by paging the evidence.
            retry_larger=False,
        )
    )
    episode_store_factory: Callable[[Path], EpisodeReadingStore] = EpisodeReadingStore
    period_store_factory: Callable[[Path], PeriodInsightStore] = PeriodInsightStore
    structure_planner: Callable[
        [StructurePlanningInput, StructurePlannerPorts], StructurePlanningResult
    ] = plan_structure
    structure_ports_factory: Callable[[StructurePlanningInput], StructurePlannerPorts] | None = None
    monotonic: Callable[[], float] = time.monotonic
    report: Callable[[str], None] = lambda _message: None
    prepare_annotations: Callable[..., Any] | None = None


def production_sampled_pair_confirmer(
    source: StructurePlanningInput, *, cache_path: Path, trace: Trace
) -> CachedSampledPairConfirmer:
    """Share the exact sampled-picture composition with the sealed matrix adapter."""
    return CachedSampledPairConfirmer(
        assets=source.assets,
        allowed_ids=set(chain.from_iterable(source.moment_asset_ids.values())),
        llm_config=source.config.llm,
        cache_path=cache_path,
        image_dir=cache_path.parent / "picture-facts-images",
        trace=trace,
        sheet_dir=source.artifact_dir / "sampled-pair-sheets",
    )


def production_attached_pictures(source, *, cache_path, pictures, pairs, resources):
    """Shared product/matrix composition; no client opens until selected material needs it."""
    client = None

    def fetch(video_id):
        nonlocal client
        if client is None:
            from immich_memories.api.sync_client import SyncImmichClient

            config = source.config.immich
            client = SyncImmichClient(
                base_url=config.url, api_key=config.api_key, api_version=config.api_version
            )
            resources.callback(client.close)
        return client.get_video_playback(video_id)

    scope = {
        "case": asdict(source.case),
        "intent": source.intent.prompt_block(),
        "wall_sha256": hashlib.sha256(source.wall_bytes).hexdigest(),
        "moments": list(source.moment_asset_ids.items()),
        "primaries": [
            (key, source_metadata_digest(asset)) for key, asset in sorted(source.assets.items())
        ],
        "companions": [
            (key, source_metadata_digest(asset))
            for key, asset in sorted(source.companion_assets.items())
        ],
        "allow_live_motion": source.allow_live_motion,
        "audience": source.audience,
    }
    scope_key = hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    outcomes = AttachedAttemptOutcomes(
        output=source.artifact_dir,
        scope={"input_key": scope_key},
        replay=source.attached_outcome_replay,
    )
    samples = AttachedVideoSamples(
        assets=source.assets,
        allowed_ids=set(chain.from_iterable(source.moment_asset_ids.values())),
        companion_assets=source.companion_assets,
        cache_dir=cache_path.parent / "attached-material",
        fetch_playback=fetch,
        outcomes=outcomes,
    )
    return FinalAttachedPictures(samples, pictures, pairs), samples


def production_story_motion(source, *, cache_path, trace, resources):
    """Lazy playback acquisition for shortlisted ordinary-video depth comparisons."""
    client = None

    def fetch(asset_id):
        nonlocal client
        if client is None:
            from immich_memories.api.sync_client import SyncImmichClient

            config = source.config.immich
            client = SyncImmichClient(
                base_url=config.url, api_key=config.api_key, api_version=config.api_version
            )
            resources.callback(client.close)
        return client.get_video_playback(asset_id)

    requester = VisualEditorialGateway(
        llm_config=source.config.llm, cache_path=cache_path, trace=trace
    )
    resources.callback(requester.close)
    return StoryMotionFacts(
        assets=source.assets,
        allowed_ids=set(chain.from_iterable(source.moment_asset_ids.values())),
        fetch_playback=fetch,
        requester=requester,
        trace=trace,
        cache_dir=cache_path.parent / "story-motion",
        output_dir=source.artifact_dir / "story-motion",
    )


def production_live_clock_offsets(source, *, resources):
    """Measure Live companion clock offsets for content-aligned stitch joins (#1012).

    Lazy and memoised: companions are downloaded only when a burst is actually
    planned, each pair once per run. A pair the correlation refuses (a hard
    cut, exposure shift, or too little shared content) answers None, and the
    caller keeps the metadata model for that join.
    """
    from immich_memories.processing.stitch_alignment import (
        CompanionUndecodable,
        companion_frames,
        pairwise_clock_offset,
    )

    client = None
    memo: dict[tuple[str, str], float | None] = {}

    def fetch(video_id: str) -> bytes:
        nonlocal client
        if client is None:
            from immich_memories.api.sync_client import SyncImmichClient

            config = source.config.immich
            client = SyncImmichClient(
                base_url=config.url, api_key=config.api_key, api_version=config.api_version
            )
            resources.callback(client.close)
        return client.get_video_playback(video_id)

    def measure(video_ids) -> list[float | None]:
        offsets: list[float | None] = []
        for first, second in zip(video_ids, video_ids[1:], strict=False):
            key = (first, second)
            if key not in memo:
                try:
                    measured = pairwise_clock_offset(
                        companion_frames(fetch(first)), companion_frames(fetch(second))
                    )
                    memo[key] = measured.seconds if measured is not None else None
                except (CompanionUndecodable, OSError):
                    # One unreadable companion leaves every join it touches on
                    # the metadata model; the burst still renders.
                    memo[key] = None
            offsets.append(memo[key])
        return offsets

    return measure


def production_speech_resolver(source, *, resources):
    """Detect retained speech before final timing, with lazy transport and local inference."""
    import logging

    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.speech.facts import SpeechFacts

    if not source.config.speech.enabled:
        return None
    client = None

    def fetch(asset_id):
        nonlocal client
        if client is None:
            from immich_memories.api.sync_client import SyncImmichClient

            config = source.config.immich
            client = SyncImmichClient(
                base_url=config.url, api_key=config.api_key, api_version=config.api_version
            )
            resources.callback(client.close)
        return client.get_video_playback(asset_id)

    facts = SpeechFacts(
        assets=dict(source.assets) | dict(source.companion_assets),
        cache_dir=source.bank_dir.parent / "speech-facts",
        fetch=fetch,
        config=source.config.speech,
    )

    def resolve(carriers):
        if not any(c["kind"] in {"video", "live-motion"} for c in carriers):
            return carriers
        if not facts.detector.available:
            logging.getLogger(__name__).warning(
                "Speech boundary detection is unavailable; cuts may interrupt speech. "
                "Install immich-memories[editorial] to enable the local detector."
            )
            return carriers
        return resolve_speech_cuts(
            carriers, facts, buffer=min(0.3, source.config.speech.min_silence_ms / 1000 * 0.4)
        )

    return resolve
