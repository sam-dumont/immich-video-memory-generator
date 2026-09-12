"""Acquire the accepted annotation producers before any story selection calls."""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing, suppress
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from immich_memories.analysis.editorial_description_contract import DESCRIPTION_MODEL
from immich_memories.analysis.editorial_description_outcomes import cached_preview
from immich_memories.analysis.editorial_preparation_captions import prepare_captions
from immich_memories.analysis.editorial_preparation_detectors import (
    DETECTOR_VERSIONS,
    prepare_detectors,
)
from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS, prepare_heads
from immich_memories.analysis.editorial_preparation_pixels import (
    PRODUCER_KEY,
    refresh_threshold,
    remember_pixel,
)
from immich_memories.api.models import Asset
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
from immich_memories.config_models_triage import TriageConfig
from immich_memories.operations.cancellation import check_cancelled as current_check_cancelled
from immich_memories.store.editorial_preparation import (
    initialize,
    missing_facts,
    private_database_path,
    remember_assets,
)


@dataclass(frozen=True)
class PreparationResult:
    """Unavailable captions are accounted failures; missing facts still block selection."""

    requested: int
    missing_by_producer: Mapping[str, tuple[str, ...]]
    failures: Mapping[str, str]
    produced: Mapping[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing_by_producer and not self.failures

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
            if not key.startswith(("preview:", "pixel:", "caption:"))
        )


@dataclass(frozen=True)
class PreparationPorts:
    """Stage seams for offline tests and embedders; defaults are real package producers."""

    captions: Callable = prepare_captions
    heads: Callable = prepare_heads
    detectors: Callable = prepare_detectors


def _noop_progress(_stage: str, _done: int, _total: int) -> None:
    pass


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
    store_path: Path
    preview_for: Callable[[str], bytes]
    check: Callable[[], None]
    report: Callable[[str, int, int], None]
    # Named separately from `report` because a count is not a picture: only the
    # per-asset loops here know which one they just finished, and the producers
    # `report` is also handed work in batches they cannot name.
    note: Callable[[str], None]
    failures: dict[str, str]

    def previews(
        self, ids: Sequence[str], cache_path: Path, fetch_preview
    ) -> tuple[dict[str, Path], list[str]]:
        paths: dict[str, Path] = {}
        unusable: list[str] = []
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
                self.failures[f"preview:{asset_id}"] = f"{type(exc).__name__}: {exc}"
            self.report("previews", index, len(ids))
        return paths, unusable

    def pixels(self, connection: sqlite3.Connection, asset_ids: Sequence[str]) -> None:
        for index, asset_id in enumerate(asset_ids, 1):
            self.check()
            try:
                remember_pixel(connection, asset_id, self.preview_for(asset_id))
                self.note(asset_id)
            except Exception as exc:
                self.failures[f"pixel:{asset_id}"] = f"{type(exc).__name__}: {exc}"
            self.report("pixels", index, len(asset_ids))
        refresh_threshold(connection)

    def public_heads(self, asset_ids: Sequence[str], head_versions: Mapping[str, str]) -> None:
        self.check()
        try:
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

    def detectors(
        self, pending: Mapping[str, Sequence[str]], preview_paths: Mapping[str, Path]
    ) -> None:
        self.check()
        try:
            errors = self.providers.detectors(
                pending=pending,
                store_path=self.store_path,
                preview_paths=preview_paths,
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

    def captions(self, connection: sqlite3.Connection, asset_ids: Sequence[str]) -> None:
        self.check()
        try:
            errors = self.providers.captions(
                connection=connection,
                asset_ids=tuple(asset_ids),
                preview_for=self.preview_for,
                base_url=self.preparation_config.caption_base_url,
                timeout=self.preparation_config.caption_timeout_seconds,
                concurrency=self.preparation_config.caption_concurrency,
                check_cancelled=self.check,
                progress=self.report,
            )
            self.failures.update({f"caption:{key}": value for key, value in errors.items()})
        except Exception as exc:
            self.failures["captions"] = (
                f"{type(exc).__name__}: {exc}; configure caption_base_url with the compact-v3 public model endpoint"
            )


def prepare_editorial_annotations(
    *,
    assets: Sequence[Asset],
    store_path: Path,
    thumbnail_cache,
    preparation_config: EditorialPreparationConfig,
    triage_config: TriageConfig,
    head_versions: Mapping[str, str],
    description_model: str = DESCRIPTION_MODEL,
    pixel_producer_key: str = PRODUCER_KEY,
    fetch_preview: Callable[[str], bytes | None] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    on_asset: Callable[[str], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    ports: PreparationPorts | None = None,
) -> PreparationResult:
    """Prepare the full source, never only the duration-limited selection demand.

    ``fetch_preview`` receives an asset ID and returns the Immich preview bytes.
    It is called once for an absent or corrupt preview. A caller may provide
    ThumbnailCache or its directory; successful fetches use the same native disk
    layout. ``on_asset`` is told the ID of each picture this pass finishes, so a
    surface watching a long stage can show them; it is never told about one
    whose preview could not be read.
    """
    cache_path = Path(getattr(thumbnail_cache, "cache_dir", thumbnail_cache))
    store_path = Path(store_path)
    source = tuple({asset.id: asset for asset in assets}.values())
    ids = tuple(asset.id for asset in source)
    stage = _Acquisition(
        providers=ports or PreparationPorts(),
        preparation_config=preparation_config,
        triage_config=triage_config,
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
        requested_public = {
            head: version for head, version in head_versions.items() if head in PUBLIC_HEAD_VERSIONS
        }
        public_ids = _public_head_ids(before, ids, available, requested_public)
        if public_ids:
            stage.public_heads(public_ids, requested_public)
        detector_pending = _detector_pending(pending, head_versions)
        if detector_pending:
            stage.detectors(detector_pending, preview_paths)
        _record_unpackaged_heads(pending, head_versions, stage.failures)
        _acquire_captions(
            stage, connection, pending(f"description:{description_model}"), description_model
        )
        stage.check()
        after, _unavailable = outstanding()
        if preview_missing:
            after["preview"] = tuple(preview_missing)
        produced = {key: len(values) - len(after.get(key, ())) for key, values in before.items()}
        return PreparationResult(len(ids), after, stage.failures, produced)


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
    pending: Callable[[str], tuple[str, ...]], head_versions: Mapping[str, str]
) -> dict[str, tuple[str, ...]]:
    demanded = {
        head: pending(f"head:{head}@{version}")
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
