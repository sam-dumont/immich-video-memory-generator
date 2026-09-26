"""Which model producer still owes a fact, and who is allowed to answer for it.

Four decisions live here, in this order: what the inference service may be asked for,
which pictures the packaged public heads still owe, which sources the detector worker
must read, and which attached clips owe the exposure head a row of their own. A head
nothing packages is named rather than silently skipped.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

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
from immich_memories.api.models import Asset
from immich_memories.config_models_inference import InferenceConfig
from immich_memories.store.editorial_preparation import heads_missing_for


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

    def clip_frames(self, frame_paths: Mapping[str, Sequence[Path]]) -> None: ...

    def video_motion(
        self, frame_paths: Mapping[str, Sequence[Path]], videos: Mapping[str, Asset]
    ) -> None: ...

    def previews(
        self, ids: Sequence[str], cache_path: Path, fetch_preview: Any
    ) -> tuple[dict[str, Path], list[str]]: ...

    @property
    def unservable(self) -> dict[str, str]: ...


CLIP_COMPANION = "clip_companion"


def deferred_exposure(
    missing: Mapping[str, tuple[str, ...]], source: Sequence[Asset]
) -> dict[str, tuple[str, ...]]:
    """Defer video exposure entirely; a preview must not earn the sampled-frame version.

    The selected-candidate pass will owe this producer's actual frame inspection. Other
    cheap heads can still read a video's preview while the NAS draft is being built.
    """
    key = f"head:{MARQO_HEAD}@{DETECTOR_VERSIONS[MARQO_HEAD]}"
    videos = {asset.id for asset in source if asset.is_video}
    result = dict(missing)
    if key in result:
        result[key] = tuple(asset for asset in result[key] if asset not in videos)
        if not result[key]:
            del result[key]
    return result


def acquire_clip_companions(
    stage: ModelFactStage,
    connection: sqlite3.Connection,
    frames: DetectorFrames,
    cache_path: Path,
    fetch_preview: Any,
    head_versions: Mapping[str, str],
) -> None:
    """Read a Live Photo's clip the way any clip is read; its still never stood for it.

    Bounded on purpose: the exposure head only, over the attached clips of the Live Photos
    in scope. No caption, no context head, no pixel fact -- a clip is not a candidate, it
    is material a unit can play, and the audience gate is the only pass that asks about it.
    Nothing here can block a cut either: a clip Immich will not serve leaves its still in
    the film and one named failure behind, because a missing clip row is exactly the
    evidence every Live Photo had before this existed.
    """
    version = head_versions.get(MARQO_HEAD, "")
    if not frames.companion_ids or DETECTOR_VERSIONS.get(MARQO_HEAD) != version:
        return
    owed = heads_missing_for(connection, sorted(frames.companion_ids), MARQO_HEAD, version)
    # The worker is another process writing this same file: staging the question must not
    # leave a transaction open across it, or it meets a locked database.
    connection.commit()
    if not owed:
        return
    refused = set(stage.unservable)
    paths, _unusable = stage.previews(owed, cache_path, fetch_preview)
    # A clip Immich will not preview is not a source leaving the film: its still stays.
    # Nor is it unread: Immich keeps no preview for many Live Photo clips and plays them all.
    no_preview = {
        asset_id: stage.unservable.pop(asset_id) for asset_id in set(stage.unservable) - refused
    }
    with frames.sampled(
        owed,
        check=stage.check,
        report=stage.report,
        failures=stage.failures,
        timed=stage.timed,
    ) as sampled:
        for asset_id, reason in no_preview.items():
            if asset_id not in sampled:
                stage.failures[f"{CLIP_COMPANION}:{asset_id}"] = reason
        readable = tuple(asset_id for asset_id in owed if asset_id in paths or asset_id in sampled)
        if readable:
            stage.detectors({MARQO_HEAD: readable}, paths, sampled)


def acquire_model_facts(
    stage: ModelFactStage,
    before: Mapping[str, Sequence[str]],
    ids: Sequence[str],
    available: set[str],
    pending: Callable[[str], tuple[str, ...]],
    head_versions: Mapping[str, str],
    preview_paths: Mapping[str, Path],
    frames: DetectorFrames,
    clips: Sequence[str] = (),
    motion: Mapping[str, Asset] | None = None,
) -> None:
    """``clips`` are the videos that owe their frame reading and ``motion`` the ones that owe a
    measured residual; they share the exposure head's sampled frames, so a clip is read off
    Immich once for all three."""
    motion = motion or {}
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
    if detector_pending or clips or motion:
        exposure = detector_pending.get(MARQO_HEAD, ())
        with frames.sampled(
            tuple(dict.fromkeys((*exposure, *clips, *motion))),
            check=stage.check,
            report=stage.report,
            failures=stage.failures,
            timed=stage.timed,
        ) as sampled:
            if detector_pending:
                stage.detectors(detector_pending, preview_paths, sampled)
            if owed := {clip: sampled[clip] for clip in clips if clip in sampled}:
                stage.clip_frames(owed)
            if measurable := {video: sampled[video] for video in motion if video in sampled}:
                stage.video_motion(measurable, motion)
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
