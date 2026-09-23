"""Which model producer still owes a fact, and who is allowed to answer for it.

Three decisions live here, in this order: what the inference service may be asked for,
which pictures the packaged public heads still owe, and which sources the detector
worker must read. A head nothing packages is named rather than silently skipped.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from immich_memories.analysis.editorial_preparation_detector_frames import (
    DetectorFrames,
    served_locally,
)
from immich_memories.analysis.editorial_preparation_detectors import (
    DETECTOR_VERSIONS,
    MARQO_HEAD,
)
from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS
from immich_memories.analysis.remote_facts import offloaded_versions
from immich_memories.config_models_inference import InferenceConfig


class ModelFactStage(Protocol):
    """The preparation seams this plan drives; `_Acquisition` satisfies it structurally."""

    @property
    def failures(self) -> dict[str, str]: ...

    @property
    def inference_config(self) -> InferenceConfig: ...

    @property
    def check(self) -> Callable[[], None]: ...

    @property
    def report(self) -> Callable[[str, int, int], None]: ...

    def timed(self, stage: str, pictures: int) -> AbstractContextManager[None]: ...

    def public_heads(self, asset_ids: Sequence[str], head_versions: Mapping[str, str]) -> None: ...

    def detectors(
        self,
        pending: Mapping[str, Sequence[str]],
        preview_paths: Mapping[str, Path],
        frame_paths: Mapping[str, Sequence[Path]],
    ) -> None: ...

    def remote_facts(self, pending: Mapping[str, Mapping[str, str]]) -> bool: ...


def acquire_model_facts(
    stage: ModelFactStage,
    before: Mapping[str, Sequence[str]],
    ids: Sequence[str],
    available: set[str],
    pending: Callable[[str], tuple[str, ...]],
    head_versions: Mapping[str, str],
    preview_paths: Mapping[str, Path],
    frames: DetectorFrames,
) -> None:
    head_versions, offloaded_exposure = _after_remote(
        stage, before, ids, available, head_versions, frames.video_ids
    )
    requested_public = {
        head: version for head, version in head_versions.items() if head in PUBLIC_HEAD_VERSIONS
    }
    public_ids = _public_head_ids(before, ids, available, requested_public)
    if public_ids:
        stage.public_heads(public_ids, requested_public)
    detector_pending = _detector_pending(pending, head_versions, offloaded_exposure)
    if detector_pending:
        with frames.sampled(
            detector_pending.get(MARQO_HEAD, ()),
            check=stage.check,
            report=stage.report,
            failures=stage.failures,
            timed=stage.timed,
        ) as sampled:
            stage.detectors(detector_pending, preview_paths, sampled)
    _record_unpackaged_heads(pending, head_versions, stage.failures)


def _after_remote(
    stage: ModelFactStage,
    before: Mapping[str, Sequence[str]],
    ids: Sequence[str],
    available: set[str],
    head_versions: Mapping[str, str],
    videos: frozenset[str],
) -> tuple[Mapping[str, str], frozenset[str]]:
    """Offload what the service answers for.

    Returns the head versions the local producers still owe, and the sources whose
    exposure head the service did answer for -- a head that stays local for the videos
    alone must not be paid for again on every still.
    """
    if not stage.inference_config.enabled:
        return head_versions, frozenset()
    offloaded = offloaded_versions(head_versions, stage.inference_config.producers)
    pending, withheld = _offload_requests(before, ids, available, offloaded, videos)
    served = stage.remote_facts(pending)
    if not served and stage.inference_config.fallback_to_local:
        return head_versions, frozenset()
    # A head the service was not allowed to answer for on every source is still owed in
    # process, whatever the service did with the sources it was given.
    answered = {head for head in offloaded if not (withheld and head == MARQO_HEAD)}
    exposure = (
        frozenset(a for a, heads in pending.items() if MARQO_HEAD in heads)
        if served
        else frozenset()
    )
    return {h: v for h, v in head_versions.items() if h not in answered}, exposure


def _offload_requests(
    before: Mapping[str, Sequence[str]],
    ids: Sequence[str],
    available: set[str],
    offloaded: Mapping[str, str],
    videos: frozenset[str],
) -> tuple[dict[str, dict[str, str]], bool]:
    """What each source still owes that the service may answer, and whether any was withheld."""
    missing = {
        head: set(before.get(f"head:{head}@{version}", ())) for head, version in offloaded.items()
    }
    pending: dict[str, dict[str, str]] = {}
    withheld = False
    for asset_id in (asset_id for asset_id in ids if asset_id in available):
        local = served_locally(offloaded, asset_id, videos)
        withheld = withheld or local
        wanted = {
            head: version
            for head, version in offloaded.items()
            if asset_id in missing[head] and not (local and head == MARQO_HEAD)
        }
        if wanted:
            pending[asset_id] = wanted
    return pending, withheld


def _public_head_ids(
    before: Mapping[str, Sequence[str]],
    ids: Sequence[str],
    available: set[str],
    requested_public: Mapping[str, str],
) -> tuple[str, ...]:
    missing_public = set().union(
        *(
            set(before.get(f"head:{head}@{version}", ()))
            for head, version in requested_public.items()
        )
    )
    return tuple(
        asset_id for asset_id in ids if asset_id in available and asset_id in missing_public
    )


def _detector_pending(
    pending: Callable[[str], tuple[str, ...]],
    head_versions: Mapping[str, str],
    offloaded_exposure: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    demanded = {
        head: tuple(
            asset_id
            for asset_id in pending(f"head:{head}@{version}")
            if head != MARQO_HEAD or asset_id not in offloaded_exposure
        )
        for head, version in head_versions.items()
        if DETECTOR_VERSIONS.get(head) == version
    }
    return {head: values for head, values in demanded.items() if values}


def _record_unpackaged_heads(
    pending: Callable[[str], tuple[str, ...]],
    head_versions: Mapping[str, str],
    failures: dict[str, str],
) -> None:
    supported = PUBLIC_HEAD_VERSIONS | DETECTOR_VERSIONS
    for head, version in head_versions.items():
        if pending(f"head:{head}@{version}") and supported.get(head) != version:
            failures[f"head_provider:{head}"] = f"no packaged producer for {head}@{version}"
