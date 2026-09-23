"""Acquire the accepted annotation producers before any story selection calls."""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from immich_memories.analysis.editorial_clip_frames import (
    CLIP_FRAMES_HEAD,
    CLIP_FRAMES_VERSION,
)
from immich_memories.analysis.editorial_description_contract import DESCRIPTION_MODEL
from immich_memories.analysis.editorial_description_outcomes import cached_preview
from immich_memories.analysis.editorial_preparation_captions import prepare_captions
from immich_memories.analysis.editorial_preparation_detector_frames import DetectorFrames
from immich_memories.analysis.editorial_preparation_detectors import prepare_detectors
from immich_memories.analysis.editorial_preparation_heads import prepare_clip_frames, prepare_heads
from immich_memories.analysis.editorial_preparation_model_facts import (
    CLIP_COMPANION,
    acquire_clip_companions,
    acquire_model_facts,
)
from immich_memories.analysis.editorial_preparation_motion import (
    MOTION_PRODUCER,
    MotionSource,
    banked_residuals,
    missing_motion,
    motion_sources,
    playback_sampler,
    prepare_motion_lines,
    seat_asker,
)
from immich_memories.analysis.editorial_preparation_pixels import (
    PRODUCER_KEY,
    refresh_threshold,
    remember_pixel,
)
from immich_memories.analysis.editorial_preparation_remote import prepare_remote_facts
from immich_memories.analysis.remote_facts import RemoteFactsError
from immich_memories.analysis.subject_framing import FaceBox
from immich_memories.api.models import Asset
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
from immich_memories.config_models_inference import InferenceConfig
from immich_memories.config_models_triage import TriageConfig
from immich_memories.operations.cancellation import check_cancelled as current_check_cancelled
from immich_memories.store.caption_provenance import origins_for
from immich_memories.store.editorial_preparation import (
    faces_unread,
    heads_missing_for,
    initialize,
    missing_facts,
    private_database_path,
    remember_assets,
    remember_faces,
)


@dataclass(frozen=True)
class PreparationResult:
    """Unavailable captions are accounted failures; missing demanded facts still block selection.

    ``tier`` names which producers this run asked for. A producer the tier does not
    demand is absent from ``missing_by_producer`` entirely, so it can neither block
    the cut nor be mistaken later for one that was asked for and failed.
    """

    requested: int
    missing_by_producer: Mapping[str, tuple[str, ...]]
    failures: Mapping[str, str]
    produced: Mapping[str, int] = field(default_factory=dict)
    tier: str = "full"
    seconds_by_stage: Mapping[str, float] = field(default_factory=dict)
    pictures_by_stage: Mapping[str, int] = field(default_factory=dict)
    # What another machine charged itself for a stage that ran there. Wall clock
    # minus this is the wire and the waiting, which is what a remote pass is
    # usually spending, and the only number that says which to go and fix.
    service_seconds_by_stage: Mapping[str, float] = field(default_factory=dict)
    # The distinct caption origins behind this run's captions, largest group
    # first, with only the assets outside that group named one by one.
    caption_provenance: Mapping[str, object] = field(default_factory=dict)
    # Sources Immich itself will not serve, with the reason, one entry each.
    # They are named here rather than counted as a gap in every producer that
    # depends on a preview, because no rerun of any producer can fix them.
    unservable_sources: Mapping[str, str] = field(default_factory=dict)
    # Bytes, requests and model calls a stage paid for, where pictures are not the cost.
    transfer_by_stage: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """Whether the cut has what it was promised.

        A clip whose frames could not be sampled is not part of that: the exposure head
        read its preview instead, which is what every source was read on before there were
        frames, and the failure is named rather than blocking the cut. Nor is an attached
        clip Immich would not serve, which leaves its still in the film either way.
        """
        return not self.missing_by_producer and all(
            key.startswith(("detector_frames:", f"{CLIP_COMPANION}:", CLIP_FRAMES_HEAD))
            for key in self.failures
        )

    @property
    def producer_failures(self) -> tuple[str, ...]:
        """Failures that name a producer rather than one picture.

        The per-asset entries are keyed by asset id and there can be thousands
        of them; these are the ones worth putting in a message, because they say
        why a whole producer contributed nothing.
        """
        return tuple(
            reason
            for key, reason in sorted(self.failures.items())
            if not key.startswith(
                (
                    "preview:",
                    "pixel:",
                    "caption:",
                    "motion:",
                    "detector_frames:",
                    CLIP_COMPANION,
                    f"{CLIP_FRAMES_HEAD}:",
                )
            )
        )

    def stage_rates(self) -> dict[str, float]:
        """Seconds per picture for each stage that ran, rounded to the millisecond.

        A wall-clock total cannot tell a deployment which producer it cannot afford.
        These are the numbers the tier decision turns on, taken on the machine that
        will run it rather than on the machine the table was written on.
        """
        return _per_picture(self.seconds_by_stage, self.pictures_by_stage)

    def service_rates(self) -> dict[str, float]:
        """Seconds per picture the service itself reported, for the stages that ran there."""
        return _per_picture(self.service_seconds_by_stage, self.pictures_by_stage)


def _per_picture(seconds: Mapping[str, float], pictures: Mapping[str, int]) -> dict[str, float]:
    return {
        stage: round(seconds[stage] / count, 4)
        for stage, count in pictures.items()
        if count and stage in seconds
    }


@dataclass(frozen=True)
class PreparationPorts:
    """Stage seams for offline tests and embedders; defaults are real package producers."""

    captions: Callable = prepare_captions
    heads: Callable = prepare_heads
    detectors: Callable = prepare_detectors
    motion: Callable = prepare_motion_lines
    clip_frames: Callable = prepare_clip_frames


PREVIEW_UNAVAILABLE = "preview unavailable at Immich (HTTP 404)"


def _preview_refused(exc: BaseException) -> bool:
    """Whether Immich answered about this source, rather than failing to answer at all.

    The client has already spent its retries by the time either arrives here: a
    404 is never retried and a timeout is raised only after the last attempt. So
    a 404 is the server's settled answer -- it has no preview for this asset and
    a rerun will not change that -- while everything else is unfinished work.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 404


def _noop_progress(_stage: str, _done: int, _total: int) -> None:
    pass


def _naming_somebody(source: Sequence[Asset]) -> tuple[str, ...]:
    return tuple(a.id for a in source if any(p.name.strip() for p in a.people))


def _noop_asset(_asset_id: str) -> None:
    pass


def _ensure_preview(path: Path, asset_id: str, fetch_preview) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError):
        pass
    else:
        # Reuse is use. The cache evicts oldest mtime first, so without this a
        # preview an earlier run downloaded still looks as old as that run while
        # this one reads it back for pixels, heads, sheets and the caption --
        # and a preview that vanishes between those stages is recorded as a
        # missing fact rather than fetched again.
        with suppress(OSError):
            os.utime(path)
        return
    payload = fetch_preview(asset_id) if fetch_preview else None
    if not payload:
        raise FileNotFoundError(
            "Immich preview missing or corrupt; provide fetch_preview or populate the configured thumbnail cache"
        )
    with Image.open(io.BytesIO(payload)) as image:
        image.verify()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class _Acquisition:
    """The seams, callbacks and accumulated failures every producer stage shares."""

    providers: PreparationPorts
    preparation_config: EditorialPreparationConfig
    triage_config: TriageConfig
    inference_config: InferenceConfig
    store_path: Path
    preview_for: Callable[[str], bytes]
    check: Callable[[], None]
    report: Callable[[str, int, int], None]
    # Named separately from `report` because a count is not a picture: only the
    # per-asset loops here know which one they just finished, and the producers
    # `report` is also handed work in batches they cannot name.
    note: Callable[[str], None]
    failures: dict[str, str]
    # Sources the server refused by name, kept apart from `failures` so the
    # completeness check never reads them as a producer that went down.
    unservable: dict[str, str] = field(default_factory=dict)
    # Seconds and pictures per stage, so one real run over a real library yields the
    # per-producer numbers a wall-clock total cannot: the tier decision turns on them.
    seconds: dict[str, float] = field(default_factory=dict)
    pictures: dict[str, int] = field(default_factory=dict)
    service_seconds: dict[str, float] = field(default_factory=dict)
    transfer: dict[str, dict[str, int]] = field(default_factory=dict)

    def transfers(self) -> dict[str, dict[str, int]]:
        return {stage: counts.copy() for stage, counts in self.transfer.items()}

    @contextmanager
    def timed(self, stage: str, pictures: int) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[stage] = self.seconds.get(stage, 0.0) + time.perf_counter() - started
            self.pictures[stage] = self.pictures.get(stage, 0) + pictures

    def previews(
        self, ids: Sequence[str], cache_path: Path, fetch_preview
    ) -> tuple[dict[str, Path], list[str]]:
        paths: dict[str, Path] = {}
        unusable: list[str] = []
        with self.timed("previews", len(ids)):
            for index, asset_id in enumerate(ids, 1):
                self.check()
                subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
                path = cache_path / subdir / f"{asset_id}_preview.jpg"
                try:
                    _ensure_preview(path, asset_id, fetch_preview)
                    paths[asset_id] = path
                    self.note(asset_id)
                except Exception as exc:
                    unusable.append(asset_id)
                    if _preview_refused(exc):
                        self.unservable[asset_id] = PREVIEW_UNAVAILABLE
                    else:
                        self.failures[f"preview:{asset_id}"] = f"{type(exc).__name__}: {exc}"
                self.report("previews", index, len(ids))
        return paths, unusable

    def pixels(self, connection: sqlite3.Connection, asset_ids: Sequence[str]) -> None:
        with self.timed("pixels", len(asset_ids)):
            for index, asset_id in enumerate(asset_ids, 1):
                self.check()
                try:
                    remember_pixel(connection, asset_id, self.preview_for(asset_id))
                    self.note(asset_id)
                except Exception as exc:
                    self.failures[f"pixel:{asset_id}"] = f"{type(exc).__name__}: {exc}"
                self.report("pixels", index, len(asset_ids))
        refresh_threshold(connection)

    def faces(self, connection: sqlite3.Connection, source: Sequence[Asset], fetch_faces) -> None:
        """Bank where each face sits in the pictures that name somebody.

        Only those pictures: a frame naming nobody has no subject to be framed well
        or badly, so its geometry answers no question the cut asks. Without a reader
        for them nothing is banked and every line stays what it was.
        """
        if fetch_faces is None:
            return
        asset_ids = faces_unread(connection, _naming_somebody(source))
        with self.timed("faces", len(asset_ids)):
            for index, asset_id in enumerate(asset_ids, 1):
                self.check()
                try:
                    remember_faces(connection, asset_id, fetch_faces(asset_id))
                except Exception as exc:
                    self.failures[f"faces:{asset_id}"] = f"{type(exc).__name__}: {exc}"
                self.report("faces", index, len(asset_ids))
        connection.commit()

    def public_heads(self, asset_ids: Sequence[str], head_versions: Mapping[str, str]) -> None:
        self.check()
        try:
            with self.timed("public_heads", len(asset_ids)):
                self.providers.heads(
                    asset_ids=asset_ids,
                    store_path=self.store_path,
                    bundle_path=self.preparation_config.head_bundle_path,
                    encoder_path=self.triage_config.encoder_path,
                    head_versions=head_versions,
                    preview_for=self.preview_for,
                    batch_size=self.preparation_config.batch_size,
                    check_cancelled=self.check,
                    progress=self.report,
                    provider=self.triage_config.provider,
                )
        except Exception as exc:
            self.failures["public_heads"] = f"{type(exc).__name__}: {exc}"

    def remote_facts(self, pending: Mapping[str, Mapping[str, str]]) -> bool:
        """Bank the offloaded producers from the service; False when it did not answer.

        A failure is never silent: it is recorded against the endpoint in the
        preparation report, which both surfaces print, and says whether the local
        producers took over. Whatever the service banked before failing stays
        banked; the local path re-derives only what is still missing.
        """
        if not pending:
            return True
        self.check()
        try:
            with self.timed("remote_facts", len(pending)):
                charged = prepare_remote_facts(
                    pending=pending,
                    store_path=self.store_path,
                    config=self.inference_config,
                    preview_for=self.preview_for,
                    check_cancelled=self.check,
                    progress=self.report,
                    on_asset=self.note,
                )
            if charged is not None:
                self.service_seconds["remote_facts"] = (
                    self.service_seconds.get("remote_facts", 0.0) + charged
                )
            return True
        except RemoteFactsError as exc:
            endpoint = self.inference_config.facts_base_url
            outcome = (
                "the local producers took over"
                if self.inference_config.fallback_to_local
                else "no local fallback (inference.fallback_to_local is off)"
            )
            self.failures["remote_facts"] = f"inference service at {endpoint}: {exc}; {outcome}"
            return False

    def detectors(
        self,
        pending: Mapping[str, Sequence[str]],
        preview_paths: Mapping[str, Path],
        frame_paths: Mapping[str, Sequence[Path]],
    ) -> None:
        self.check()
        demanded = sum(len(ids) for ids in pending.values())
        try:
            with self.timed("detectors", demanded):
                errors = self.providers.detectors(
                    pending=pending,
                    store_path=self.store_path,
                    preview_paths=preview_paths,
                    frame_paths=frame_paths,
                    python=self.preparation_config.detector_python,
                    cache_dir=self.preparation_config.detector_cache_dir,
                    marqo_onnx=self.preparation_config.marqo_onnx_path,
                    allow_downloads=self.preparation_config.allow_model_downloads,
                    batch_size=self.preparation_config.batch_size,
                    check_cancelled=self.check,
                    progress=self.report,
                )
            self.failures.update({f"detector:{key}": value for key, value in errors.items()})
        except Exception as exc:
            self.failures["detectors"] = f"{type(exc).__name__}: {exc}"

    def clip_frames(self, frame_paths: Mapping[str, Sequence[Path]]) -> None:
        """Read each clip's sampled frames with the frame head; a clip it cannot read, or a
        producer that cannot run, leaves the clip on its preview's reading and never the cut."""
        self.check()
        try:
            with self.timed(CLIP_FRAMES_HEAD, len(frame_paths)):
                errors = self.providers.clip_frames(
                    frame_paths=frame_paths,
                    store_path=self.store_path,
                    bundle_path=self.preparation_config.head_bundle_path,
                    encoder_path=self.triage_config.encoder_path,
                    check_cancelled=self.check,
                    provider=self.triage_config.provider,
                )
            self.failures.update({f"{CLIP_FRAMES_HEAD}:{k}": v for k, v in errors.items()})
        except Exception as exc:
            self.failures[CLIP_FRAMES_HEAD] = f"{type(exc).__name__}: {exc}"

    def captions(self, connection: sqlite3.Connection, asset_ids: Sequence[str]) -> None:
        self.check()
        try:
            with self.timed("captions", len(asset_ids)):
                errors = self.providers.captions(
                    connection=connection,
                    asset_ids=tuple(asset_ids),
                    preview_for=self.preview_for,
                    base_url=self.preparation_config.caption_base_url,
                    api_key=self.preparation_config.caption_api_key,
                    artifact_id=self.preparation_config.caption_artifact_id,
                    timeout=self.preparation_config.caption_timeout_seconds,
                    concurrency=self.preparation_config.caption_concurrency,
                    check_cancelled=self.check,
                    progress=self.report,
                )
            self.failures.update({f"caption:{key}": value for key, value in errors.items()})
        except PermissionError as exc:
            # The endpoint answered and asked for a credential; repointing the URL is not the fix.
            self.failures["captions"] = str(exc)
        except Exception as exc:
            self.failures["captions"] = (
                f"{type(exc).__name__}: {exc}; configure caption_base_url with the compact-v3 public model endpoint"
            )

    def motion(
        self,
        connection: sqlite3.Connection,
        sources: Sequence[MotionSource],
        read_playback: Callable[[str, int, int], tuple[bytes, int]],
    ) -> None:
        self.check()
        config = self.preparation_config
        try:
            with self.timed("motion", len(sources)):
                outcome = self.providers.motion(
                    connection=connection,
                    sources=tuple(sources),
                    sample=playback_sampler(read_playback),
                    ask=seat_asker(
                        config.caption_base_url,
                        api_key=config.caption_api_key,
                        timeout=config.caption_timeout_seconds,
                    ),
                    concurrency=config.caption_concurrency,
                    check_cancelled=self.check,
                    progress=self.report,
                )
        except PermissionError as exc:
            self.failures["motion"] = str(exc)
            return
        except Exception as exc:
            self.failures["motion"] = f"{type(exc).__name__}: {exc}"
            return
        self.failures.update({f"motion:{key}": value for key, value in outcome.failures.items()})
        self.transfer["motion"] = {
            "bytes": outcome.bytes_read,
            "requests": outcome.requests,
            "seat_calls": outcome.seat_calls,
        }


def prepare_editorial_annotations(
    *,
    assets: Sequence[Asset],
    store_path: Path,
    thumbnail_cache,
    preparation_config: EditorialPreparationConfig,
    triage_config: TriageConfig,
    head_versions: Mapping[str, str],
    inference_config: InferenceConfig | None = None,
    description_model: str = DESCRIPTION_MODEL,
    pixel_producer_key: str = PRODUCER_KEY,
    fetch_preview: Callable[[str], bytes | None] | None = None,
    fetch_faces: Callable[[str], Sequence[FaceBox]] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    on_asset: Callable[[str], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    ports: PreparationPorts | None = None,
    read_playback: Callable[[str, int, int], tuple[bytes, int]] | None = None,
) -> PreparationResult:
    """Prepare the full source, never only the duration-limited selection demand.

    ``fetch_preview`` receives an asset ID and returns the Immich preview bytes.
    It is called once for an absent or corrupt preview. A caller may provide
    ThumbnailCache or its directory; successful fetches use the same native disk
    layout. ``on_asset`` is told the ID of each picture this pass finishes, so a
    surface watching a long stage can show them; it is never told about one
    whose preview could not be read. ``read_playback`` answers a byte range of a video's
    playback rendition with its full size; without it no motion line is produced.
    """
    cache_path = Path(getattr(thumbnail_cache, "cache_dir", thumbnail_cache))
    store_path = Path(store_path)
    source = tuple({asset.id: asset for asset in assets}.values())
    ids = tuple(asset.id for asset in source)
    stage = _Acquisition(
        providers=ports or PreparationPorts(),
        preparation_config=preparation_config,
        triage_config=triage_config,
        inference_config=inference_config or InferenceConfig(),
        store_path=store_path,
        preview_for=lambda asset_id: cached_preview(cache_path, asset_id),
        check=check_cancelled or current_check_cancelled,
        report=progress or _noop_progress,
        note=on_asset or _noop_asset,
        failures={},
    )
    stage.check()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    preview_paths, preview_missing = stage.previews(ids, cache_path, fetch_preview)
    # This stage writes into the cache layout directly rather than through `put`,
    # so the periodic check `put` performs never sees the previews a scope brings
    # in -- and on a rerun, where nothing is fetched, nothing checks at all. The
    # cap belongs to the directory, not to whoever last wrote to it.
    enforce_budget = getattr(thumbnail_cache, "enforce_budget", None)
    if callable(enforce_budget):
        enforce_budget()
    with closing(sqlite3.connect(private_database_path(store_path), timeout=60)) as connection:

        def outstanding() -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
            return missing_facts(
                connection,
                ids,
                description_model=description_model,
                head_versions=head_versions,
                pixel_producer_key=pixel_producer_key,
                preview_for=stage.preview_for,
            )

        initialize(connection)
        remember_assets(connection, source)
        _ensure_sharpness_threshold(connection, pixel_producer_key)
        before, _ = outstanding()
        connection.commit()
        available = set(preview_paths)

        def pending(key: str) -> tuple[str, ...]:
            return tuple(asset_id for asset_id in before.get(key, ()) if asset_id in available)

        _acquire_pixels(
            stage, connection, pending(f"pixel:{pixel_producer_key}"), pixel_producer_key
        )
        stage.faces(connection, source, fetch_faces)
        if preparation_config.demands_models:
            frames = DetectorFrames(source, read_playback)
            clips = heads_missing_for(
                connection, sorted(frames.video_ids), CLIP_FRAMES_HEAD, CLIP_FRAMES_VERSION
            )
            connection.commit()
            acquire_model_facts(
                stage, before, ids, available, pending, head_versions, preview_paths, frames, clips
            )
            acquire_clip_companions(
                stage, connection, frames, cache_path, fetch_preview, head_versions
            )
        if preparation_config.demands_captions:
            _acquire_captions(
                stage, connection, pending(f"description:{description_model}"), description_model
            )
        motion = _MotionScope(
            source, store_path, read_playback, demanded=preparation_config.demands_captions
        )
        motion.acquire(stage, connection, before)
        stage.check()
        after, _unavailable = outstanding()
        motion.report(connection, after)
        produced = {key: len(values) - len(after.get(key, ())) for key, values in before.items()}
        # A source the server will not serve is not a gap any producer can close,
        # so it leaves the run by name instead of being counted as one missing
        # fact per dependent producer. Only the rest still block the cut.
        after = _without(after, stage.unservable)
        if still_missing := [a for a in preview_missing if a not in stage.unservable]:
            after["preview"] = tuple(still_missing)
        demanded = _demanded_producers(preparation_config)
        return PreparationResult(
            len(ids),
            {key: value for key, value in after.items() if demanded(key)},
            stage.failures,
            {key: value for key, value in produced.items() if demanded(key)},
            preparation_config.tier,
            stage.seconds.copy(),
            stage.pictures.copy(),
            stage.service_seconds.copy(),
            caption_provenance=origins_for(connection, ids, description_model)
            if preparation_config.demands_captions
            else {},
            unservable_sources=dict(sorted(stage.unservable.items())),
            transfer_by_stage=stage.transfers(),
        )


class _MotionScope:
    """The videos this pass owes a motion line: true videos, and Live Photos that play.

    Nothing is owed without a playback reader, or when the tier has no caption seat.
    """

    key = f"motion:{MOTION_PRODUCER}"

    def __init__(
        self,
        assets: Sequence[Asset],
        store_path: Path,
        read_playback: Callable[[str, int, int], tuple[bytes, int]] | None,
        *,
        demanded: bool,
    ) -> None:
        self._read_playback = read_playback
        self._sources = (
            motion_sources(assets, residual_of=banked_residuals(store_path))
            if read_playback and demanded
            else ()
        )

    def acquire(self, stage: _Acquisition, connection: sqlite3.Connection, before: dict) -> None:
        owed = missing_motion(connection, self._sources) if self._sources else ()
        if owed and self._read_playback is not None:
            before[self.key] = tuple(source.asset_id for source in owed)
            stage.motion(connection, owed, self._read_playback)

    def report(self, connection: sqlite3.Connection, after: dict) -> None:
        if self._sources and (owed := missing_motion(connection, self._sources)):
            after[self.key] = tuple(source.asset_id for source in owed)


def _without(
    missing: Mapping[str, tuple[str, ...]], excluded: Mapping[str, str]
) -> dict[str, tuple[str, ...]]:
    if not excluded:
        return dict(missing)
    remaining = {key: tuple(a for a in ids if a not in excluded) for key, ids in missing.items()}
    return {key: ids for key, ids in remaining.items() if ids}


def _demanded_producers(
    preparation_config: EditorialPreparationConfig,
) -> Callable[[str], bool]:
    """Whether a producer key was asked for at all, so an absence can be named or ignored."""

    def demanded(key: str) -> bool:
        if key.startswith(("description:", "motion:")):
            return preparation_config.demands_captions
        if key.startswith("head:"):
            return preparation_config.demands_models
        return True

    return demanded


def _ensure_sharpness_threshold(connection: sqlite3.Connection, pixel_producer_key: str) -> None:
    if pixel_producer_key != PRODUCER_KEY:
        return
    banked = connection.execute(
        "SELECT 1 FROM pixel_facts_thresholds WHERE name='sharpness_p10' AND producer_key=?",
        (PRODUCER_KEY,),
    ).fetchone()
    if not banked:
        refresh_threshold(connection)


def _acquire_pixels(
    stage: _Acquisition,
    connection: sqlite3.Connection,
    asset_ids: Sequence[str],
    pixel_producer_key: str,
) -> None:
    if not asset_ids:
        return
    if pixel_producer_key != PRODUCER_KEY:
        stage.failures["pixel_provider"] = (
            f"no packaged producer for {pixel_producer_key}; accepted producer is {PRODUCER_KEY}"
        )
        return
    stage.pixels(connection, asset_ids)


def _conflicting_caption_rows(
    connection: sqlite3.Connection, asset_id: str, description_model: str
) -> bool:
    touched = connection.execute(
        "SELECT 1 FROM descriptions WHERE asset_id=? AND model=? UNION ALL "
        "SELECT 1 FROM description_fields WHERE asset_id=? AND model=?",
        (asset_id, description_model, asset_id, description_model),
    ).fetchone()
    outcome_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='description_unavailable'"
    ).fetchone()
    stale = (
        outcome_table
        and connection.execute(
            "SELECT 1 FROM description_unavailable WHERE asset_id=? AND model=?",
            (asset_id, description_model),
        ).fetchone()
    )
    return bool(touched or stale)


def _acquire_captions(
    stage: _Acquisition,
    connection: sqlite3.Connection,
    asset_ids: Sequence[str],
    description_model: str,
) -> None:
    if not asset_ids:
        return
    if description_model != DESCRIPTION_MODEL:
        stage.failures["caption_provider"] = (
            f"no packaged producer for {description_model}; accepted producer is {DESCRIPTION_MODEL}"
        )
        return
    # Do not pay for captions that would collide with malformed immutable rows.
    clean_ids = []
    for asset_id in asset_ids:
        if _conflicting_caption_rows(connection, asset_id, description_model):
            stage.failures[f"caption:{asset_id}"] = (
                "invalid or conflicting existing compact caption evidence; store repair is required"
            )
        else:
            clean_ids.append(asset_id)
    if clean_ids:
        stage.captions(connection, clean_ids)
