"""Durable, non-LRU preview stores for training and certification cohorts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import resource
import stat
import sys
import tempfile
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

DEFAULT_MEMORY_LIMIT_BYTES = 64 * 1024**3
MAX_PREVIEW_BYTES = 32 * 1024**2
_BASE_PRIVATE_ROW_KEYS = frozenset(
    {
        "audit_id",
        "asset_id",
        "source_updated",
        "captured_at",
        "capture_day",
        "moment_key",
        "component_key",
        "image_relpath",
    }
)
_PREVIEW_ROW_KEYS = _BASE_PRIVATE_ROW_KEYS | {
    "preview_sha256",
    "preview_bytes",
    "preview_width",
    "preview_height",
    "preview_format",
}
_PUBLIC_PREVIEW_ROW_KEYS = frozenset(
    {
        "audit_id",
        "image_relpath",
        "preview_sha256",
        "preview_bytes",
        "preview_width",
        "preview_height",
        "preview_format",
    }
)
_UNAVAILABLE_ROW_KEYS = frozenset({"audit_id", "asset_id", "source_updated", "error_category"})


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_create_only(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_create_only_idempotent(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"existing {path.name} has different immutable content")
        return
    _write_create_only(path, payload)


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _assert_memory_limit(limit_bytes: int) -> None:
    if limit_bytes < 1:
        raise ValueError("memory limit must be positive")
    observed = _peak_rss_bytes()
    if observed >= limit_bytes:
        raise MemoryError(
            f"preview-store process reached {observed / 1024**3:.2f} GiB; "
            f"limit is {limit_bytes / 1024**3:.2f} GiB"
        )


def _validate_preview(payload: bytes) -> tuple[str, int, int, str]:
    if not payload or len(payload) > MAX_PREVIEW_BYTES:
        raise ValueError("preview payload is empty or exceeds the bounded per-image limit")
    with Image.open(io.BytesIO(payload)) as image:
        image.verify()
    with Image.open(io.BytesIO(payload)) as image:
        width, height = image.size
        image_format = str(image.format or "unknown").lower()
    if width < 1 or height < 1:
        raise ValueError("preview has invalid dimensions")
    return hashlib.sha256(payload).hexdigest(), width, height, image_format


def _private_row(row: Any, *, audit_id: str, image_relpath: str) -> dict[str, object]:
    return {
        "audit_id": audit_id,
        "asset_id": str(row.asset_id),
        "source_updated": str(row.updated_at),
        "captured_at": str(row.captured_at),
        "capture_day": str(row.capture_day),
        "moment_key": str(row.moment_key),
        "component_key": str(row.component_key),
        "image_relpath": image_relpath,
    }


def _fetch_and_pin(
    row: dict[str, object],
    *,
    destination: Path,
    fetch_preview: Callable[[str], bytes],
) -> dict[str, object]:
    target = destination / str(row["image_relpath"])
    if target.exists():
        payload = target.read_bytes()
    else:
        payload = fetch_preview(str(row["asset_id"]))
        _validate_preview(payload)
        try:
            _write_create_only(target, payload)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise RuntimeError("concurrent preview fetch produced different immutable bytes")
    digest, width, height, image_format = _validate_preview(payload)
    return {
        **row,
        "preview_sha256": digest,
        "preview_bytes": len(payload),
        "preview_width": width,
        "preview_height": height,
        "preview_format": image_format,
    }


def _bounded_fetch(
    rows: Sequence[dict[str, object]],
    *,
    destination: Path,
    fetch_preview: Callable[[str], bytes],
    workers: int,
    memory_limit_bytes: int,
    progress: Callable[[int, int], None] | None,
) -> list[dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="triage-preview") as executor:
        inflight: deque[tuple[str, Future[dict[str, object]]]] = deque()
        iterator = iter(rows)
        while True:
            while len(inflight) < workers * 2:
                try:
                    row = next(iterator)
                except StopIteration:
                    break
                future = executor.submit(
                    _fetch_and_pin,
                    row,
                    destination=destination,
                    fetch_preview=fetch_preview,
                )
                inflight.append((str(row["audit_id"]), future))
            if not inflight:
                break
            audit_id, future = inflight.popleft()
            results[audit_id] = future.result()
            _assert_memory_limit(memory_limit_bytes)
            if progress is not None:
                progress(len(results), len(rows))
    return [results[str(row["audit_id"])] for row in rows]


def _preview_error_category(error: Exception) -> str:
    name = type(error).__name__.casefold().replace("_", "")
    if isinstance(error, FileNotFoundError) or "notfound" in name:
        return "not_found"
    if isinstance(error, TimeoutError) or "timeout" in name:
        return "timeout"
    if isinstance(error, PermissionError) or any(
        token in name for token in ("forbidden", "unauthorized", "authentication")
    ):
        return "access_denied"
    return "request_error"


def _safe_image_path(store: Path, image_relpath: str) -> Path:
    root = store.resolve()
    path = (store / image_relpath).resolve()
    if not path.is_relative_to(root):
        raise ValueError("preview selection lock contains a path outside its store")
    return path


def _require_private_regular_file(path: Path, *, what: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{what} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError(f"{what} must be mode 0600")


def _verify_self_digest(payload: Mapping[str, object], field: str, *, what: str) -> str:
    claimed = str(payload.get(field, ""))
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if _canonical_sha256(unsigned) != claimed:
        raise RuntimeError(f"{what} self-digest failed")
    return claimed


def _mapping_rows(value: object, *, what: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise RuntimeError(f"{what} rows are malformed")
    return list(value)


def _verify_preview_image(
    store: Path,
    row: Mapping[str, object],
    *,
    seen_paths: set[Path],
) -> Path:
    image_relpath = str(row["image_relpath"])
    image_path = _safe_image_path(store, image_relpath)
    if image_path.parent != (store / "images").resolve():
        raise RuntimeError("committed preview path is outside the opaque image directory")
    _require_private_regular_file(image_path, what="committed preview")
    if image_path in seen_paths:
        raise RuntimeError("committed preview rows reuse an image path")
    payload = image_path.read_bytes()
    digest, width, height, image_format = _validate_preview(payload)
    expected = (
        str(row["preview_sha256"]),
        int(row["preview_bytes"]),
        int(row["preview_width"]),
        int(row["preview_height"]),
        str(row["preview_format"]),
    )
    actual = (digest, len(payload), width, height, image_format)
    if actual != expected:
        raise RuntimeError("committed preview bytes differ from their sealed row")
    seen_paths.add(image_path)
    return image_path


def _verify_store_payload(
    destination: Path,
    manifest: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    store = Path(destination)
    manifest_path = store / "manifest.json"
    private_path = store / "private-index.json"
    lock_path = store / "selection-lock.json"
    _require_private_regular_file(private_path, what="committed private index")
    _require_private_regular_file(lock_path, what="committed selection lock")
    if manifest_path.exists():
        _require_private_regular_file(manifest_path, what="committed manifest")
        disk_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if disk_manifest != manifest:
            raise RuntimeError("committed manifest differs from the replayed payload")
    private_bytes = private_path.read_bytes()
    private = json.loads(private_bytes)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(private, dict) or not isinstance(lock, dict):
        raise RuntimeError("committed private lineage is malformed")
    manifest_dict = dict(manifest)
    manifest_sha256 = _verify_self_digest(
        manifest_dict,
        "manifest_sha256",
        what="committed manifest",
    )
    lock_sha256 = _verify_self_digest(
        lock,
        "selection_lock_sha256",
        what="committed selection lock",
    )
    if _file_sha256(private_path) != manifest_dict.get("private_index_sha256"):
        raise RuntimeError("committed private preview index failed its digest")
    header_fields = ("cohort_name", "inventory_sha256", "selection_sha256")
    for field in header_fields:
        if len({str(payload.get(field, "")) for payload in (manifest_dict, private, lock)}) != 1:
            raise RuntimeError(f"committed preview store disagrees about {field}")
    if {
        str(manifest_dict.get("selection_lock_sha256", "")),
        str(private.get("selection_lock_sha256", "")),
        lock_sha256,
    } != {lock_sha256}:
        raise RuntimeError("committed preview store disagrees about its selection lock")
    if len(manifest_sha256) != 64:
        raise RuntimeError("committed manifest digest is malformed")

    strict = manifest_dict.get("schema") == "triage-pinned-preview-store-v1"
    available = manifest_dict.get("schema") == "triage-pinned-preview-availability-v1"
    if strict == available:
        raise RuntimeError("committed preview manifest has an unsupported schema")
    expected_schemas = (
        (
            "triage-private-preview-index-v1",
            "triage-preview-selection-lock-v1",
        )
        if strict
        else (
            "triage-private-preview-availability-index-v1",
            "triage-preview-availability-selection-lock-v1",
        )
    )
    if (private.get("schema"), lock.get("schema")) != expected_schemas:
        raise RuntimeError("committed preview store mixes incompatible schemas")

    lock_rows = _mapping_rows(lock.get("rows"), what="selection lock")
    if any(set(row) != _BASE_PRIVATE_ROW_KEYS for row in lock_rows):
        raise RuntimeError("committed selection lock row violates its schema")
    lock_audits = [str(row["audit_id"]) for row in lock_rows]
    lock_assets = [str(row["asset_id"]) for row in lock_rows]
    lock_paths = [str(row["image_relpath"]) for row in lock_rows]
    if (
        len(set(lock_audits)) != len(lock_rows)
        or len(set(lock_assets)) != len(lock_rows)
        or len(set(lock_paths)) != len(lock_rows)
    ):
        raise RuntimeError("committed selection lock identities are not unique")
    lock_by_audit = {str(row["audit_id"]): row for row in lock_rows}
    seen_paths: set[Path] = set()

    if strict:
        private_rows = _mapping_rows(private.get("rows"), what="private index")
        public_rows = _mapping_rows(manifest_dict.get("rows"), what="public manifest")
        if any(set(row) != _PREVIEW_ROW_KEYS for row in private_rows):
            raise RuntimeError("committed private preview row violates its schema")
        if any(set(row) != _PUBLIC_PREVIEW_ROW_KEYS for row in public_rows):
            raise RuntimeError("committed public preview row violates its schema")
        if not (
            len(lock_rows)
            == len(private_rows)
            == len(public_rows)
            == int(manifest_dict.get("row_count", -1))
        ):
            raise RuntimeError("committed preview row counts disagree")
        for lock_row, private_row, public_row in zip(
            lock_rows,
            private_rows,
            public_rows,
            strict=True,
        ):
            if {key: private_row[key] for key in _BASE_PRIVATE_ROW_KEYS} != dict(lock_row):
                raise RuntimeError("committed selection lock differs from its private row")
            if {key: private_row[key] for key in _PUBLIC_PREVIEW_ROW_KEYS} != dict(public_row):
                raise RuntimeError("committed public manifest differs from its private row")
            _verify_preview_image(store, private_row, seen_paths=seen_paths)
        if sum(int(row["preview_bytes"]) for row in private_rows) != int(
            manifest_dict.get("total_preview_bytes", -1)
        ):
            raise RuntimeError("committed preview byte total disagrees")
    else:
        available_rows = _mapping_rows(private.get("available_rows"), what="available index")
        unavailable_rows = _mapping_rows(
            private.get("unavailable_rows"),
            what="unavailable index",
        )
        if any(set(row) != _PREVIEW_ROW_KEYS for row in available_rows):
            raise RuntimeError("committed available preview row violates its schema")
        if any(set(row) != _UNAVAILABLE_ROW_KEYS for row in unavailable_rows):
            raise RuntimeError("committed unavailable preview row violates its schema")
        combined_audits = [str(row["audit_id"]) for row in (*available_rows, *unavailable_rows)]
        if len(set(combined_audits)) != len(combined_audits) or set(combined_audits) != set(
            lock_audits
        ):
            raise RuntimeError("committed availability rows differ from their selection lock")
        for row in available_rows:
            lock_row = lock_by_audit[str(row["audit_id"])]
            if {key: row[key] for key in _BASE_PRIVATE_ROW_KEYS} != dict(lock_row):
                raise RuntimeError("committed available row differs from its selection lock")
            _verify_preview_image(store, row, seen_paths=seen_paths)
        for row in unavailable_rows:
            lock_row = lock_by_audit[str(row["audit_id"])]
            for key in ("audit_id", "asset_id", "source_updated"):
                if row[key] != lock_row[key]:
                    raise RuntimeError("committed unavailable row differs from its selection lock")
        available_count = len(available_rows)
        unavailable_count = len(unavailable_rows)
        error_counts = Counter(str(row["error_category"]) for row in unavailable_rows)
        expected_aggregates = (
            len(lock_rows),
            available_count,
            unavailable_count,
            int(lock.get("minimum_successes", -1)),
            {key: error_counts[key] for key in sorted(error_counts)},
            sum(int(row["preview_bytes"]) for row in available_rows),
        )
        actual_aggregates = (
            int(manifest_dict.get("attempted_count", -1)),
            int(manifest_dict.get("available_count", -1)),
            int(manifest_dict.get("unavailable_count", -1)),
            int(manifest_dict.get("minimum_successes", -1)),
            manifest_dict.get("error_counts"),
            int(manifest_dict.get("total_preview_bytes", -1)),
        )
        if actual_aggregates != expected_aggregates or available_count < expected_aggregates[3]:
            raise RuntimeError("committed availability aggregates disagree")

    images_dir = store / "images"
    actual_images = {
        path.resolve() for path in images_dir.iterdir() if path.is_file() and not path.is_symlink()
    }
    if actual_images != seen_paths or any(
        path.is_dir() or path.is_symlink() for path in images_dir.iterdir()
    ):
        raise RuntimeError("committed preview image directory contains unexpected entries")
    return manifest_dict, private, lock


def verify_committed_preview_store(
    destination: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Fully authenticate a committed strict or availability preview store."""
    manifest_path = Path(destination) / "manifest.json"
    _require_private_regular_file(manifest_path, what="committed manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("committed manifest is malformed")
    return _verify_store_payload(Path(destination), manifest)


def _load_reusable_previews(
    stores: Sequence[Path], *, inventory_sha256: str
) -> dict[tuple[str, str], Path]:
    reusable: dict[tuple[str, str], tuple[Path, str]] = {}
    supported_schemas = {
        "triage-preview-selection-lock-v1",
        "triage-preview-availability-selection-lock-v1",
    }
    for raw_store in stores:
        store = Path(raw_store)
        if not store.exists():
            continue
        lock_path = store / "selection-lock.json"
        if not lock_path.is_file():
            raise ValueError("preview reuse store has no selection lock")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("schema") not in supported_schemas:
            raise ValueError("preview reuse store has an unsupported selection lock")
        declared_digest = str(lock.get("selection_lock_sha256", ""))
        unsigned_lock = dict(lock)
        unsigned_lock.pop("selection_lock_sha256", None)
        if _canonical_sha256(unsigned_lock) != declared_digest:
            raise ValueError("preview reuse selection lock digest does not reproduce")
        if lock.get("inventory_sha256") != inventory_sha256:
            raise ValueError("preview reuse store belongs to a different inventory")
        rows = lock.get("rows")
        if not isinstance(rows, list):
            raise ValueError("preview reuse selection lock has no row list")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("preview reuse selection lock contains an invalid row")
            path = _safe_image_path(store, str(row["image_relpath"]))
            if not path.is_file():
                continue
            payload = path.read_bytes()
            digest, _width, _height, _image_format = _validate_preview(payload)
            key = (str(row["asset_id"]), str(row["source_updated"]))
            prior = reusable.get(key)
            if prior is not None and prior[1] != digest:
                raise RuntimeError("preview reuse stores disagree about immutable bytes")
            reusable[key] = (path, digest)
    return {key: value[0] for key, value in reusable.items()}


def _fetch_available(
    row: dict[str, object],
    *,
    destination: Path,
    fetch_preview: Callable[[str], bytes],
    reusable_previews: Mapping[tuple[str, str], Path],
) -> tuple[str, dict[str, object]]:
    target = destination / str(row["image_relpath"])
    if target.exists():
        payload = target.read_bytes()
    else:
        reuse_key = (str(row["asset_id"]), str(row["source_updated"]))
        reuse_path = reusable_previews.get(reuse_key)
        if reuse_path is not None:
            payload = reuse_path.read_bytes()
            _validate_preview(payload)
        else:
            try:
                payload = fetch_preview(str(row["asset_id"]))
            except Exception as error:
                return (
                    "unavailable",
                    {
                        "audit_id": row["audit_id"],
                        "asset_id": row["asset_id"],
                        "source_updated": row["source_updated"],
                        "error_category": _preview_error_category(error),
                    },
                )
            try:
                _validate_preview(payload)
            except ValueError:
                return (
                    "unavailable",
                    {
                        "audit_id": row["audit_id"],
                        "asset_id": row["asset_id"],
                        "source_updated": row["source_updated"],
                        "error_category": "invalid_preview",
                    },
                )
        try:
            _write_create_only(target, payload)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise RuntimeError("concurrent preview fetch produced different immutable bytes")
    digest, width, height, image_format = _validate_preview(payload)
    return (
        "available",
        {
            **row,
            "preview_sha256": digest,
            "preview_bytes": len(payload),
            "preview_width": width,
            "preview_height": height,
            "preview_format": image_format,
        },
    )


def _bounded_available_fetch(
    rows: Sequence[dict[str, object]],
    *,
    destination: Path,
    fetch_preview: Callable[[str], bytes],
    reusable_previews: Mapping[tuple[str, str], Path],
    workers: int,
    memory_limit_bytes: int,
    progress: Callable[[int, int], None] | None,
) -> list[tuple[str, dict[str, object]]]:
    results: dict[str, tuple[str, dict[str, object]]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="triage-preview") as executor:
        inflight: deque[tuple[str, Future[tuple[str, dict[str, object]]]]] = deque()
        iterator = iter(rows)
        while True:
            while len(inflight) < workers * 2:
                try:
                    row = next(iterator)
                except StopIteration:
                    break
                future = executor.submit(
                    _fetch_available,
                    row,
                    destination=destination,
                    fetch_preview=fetch_preview,
                    reusable_previews=reusable_previews,
                )
                inflight.append((str(row["audit_id"]), future))
            if not inflight:
                break
            audit_id, future = inflight.popleft()
            results[audit_id] = future.result()
            _assert_memory_limit(memory_limit_bytes)
            if progress is not None:
                progress(len(results), len(rows))
    return [results[str(row["audit_id"])] for row in rows]


def _verify_committed_store(destination: Path, manifest: dict[str, Any]) -> None:
    verified, _private, _lock = _verify_store_payload(destination, manifest)
    if verified.get("schema") != "triage-pinned-preview-store-v1":
        raise RuntimeError("committed preview store has the wrong manifest schema")


def _verify_available_store(destination: Path, manifest: dict[str, Any]) -> None:
    verified, _private, _lock = _verify_store_payload(destination, manifest)
    if verified.get("schema") != "triage-pinned-preview-availability-v1":
        raise RuntimeError("committed availability store has the wrong manifest schema")


def _available_public_manifest(
    private_index: Mapping[str, object],
    private_index_bytes: bytes,
    *,
    minimum_successes: int,
    memory_limit_bytes: int,
) -> dict[str, Any]:
    available_rows = list(private_index["available_rows"])
    unavailable_rows = list(private_index["unavailable_rows"])
    error_counts = Counter(str(row["error_category"]) for row in unavailable_rows)
    manifest: dict[str, Any] = {
        "schema": "triage-pinned-preview-availability-v1",
        "privacy": "aggregate only; asset mapping stored in private index",
        "cohort_name": private_index["cohort_name"],
        "inventory_sha256": private_index["inventory_sha256"],
        "selection_sha256": private_index["selection_sha256"],
        "selection_lock_sha256": private_index["selection_lock_sha256"],
        "private_index_sha256": hashlib.sha256(private_index_bytes).hexdigest(),
        "attempted_count": len(available_rows) + len(unavailable_rows),
        "available_count": len(available_rows),
        "unavailable_count": len(unavailable_rows),
        "minimum_successes": minimum_successes,
        "error_counts": {key: error_counts[key] for key in sorted(error_counts)},
        "total_preview_bytes": sum(int(row["preview_bytes"]) for row in available_rows),
        "memory_limit_bytes": memory_limit_bytes,
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    return manifest


def pin_available_preview_store(
    destination: Path,
    rows: Sequence[Any],
    *,
    cohort_name: str,
    inventory_sha256: str,
    selection_sha256: str,
    fetch_preview: Callable[[str], bytes],
    audit_prefix: str,
    minimum_successes: int,
    workers: int = 4,
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES,
    progress: Callable[[int, int], None] | None = None,
    reuse_stores: Sequence[Path] = (),
) -> dict[str, Any]:
    """Pin every retrievable preview while recording unavailable rows privately."""
    if not cohort_name.strip() or not audit_prefix.strip():
        raise ValueError("cohort_name and audit_prefix must not be empty")
    if len(inventory_sha256) != 64 or len(selection_sha256) != 64:
        raise ValueError("inventory and selection digests must be SHA-256 hex values")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between one and eight")
    materialized = tuple(rows)
    if not 1 <= minimum_successes <= len(materialized):
        raise ValueError("minimum successes must fit inside the preview candidates")
    asset_ids = [str(row.asset_id) for row in materialized]
    if len(set(asset_ids)) != len(asset_ids):
        raise ValueError("preview store asset ids must be unique")
    _assert_memory_limit(memory_limit_bytes)

    ordered = sorted(
        materialized,
        key=lambda row: hashlib.sha256(f"{selection_sha256}\0{row.asset_id}".encode()).hexdigest(),
    )
    width = max(4, len(str(len(ordered))))
    private_rows = [
        _private_row(
            row,
            audit_id=f"{audit_prefix}{index:0{width}d}",
            image_relpath=f"images/{audit_prefix}{index:0{width}d}.jpg",
        )
        for index, row in enumerate(ordered, start=1)
    ]
    selection_lock = {
        "schema": "triage-preview-availability-selection-lock-v1",
        "cohort_name": cohort_name,
        "inventory_sha256": inventory_sha256,
        "selection_sha256": selection_sha256,
        "minimum_successes": minimum_successes,
        "rows": private_rows,
    }
    selection_lock["selection_lock_sha256"] = _canonical_sha256(selection_lock)

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    destination.mkdir(parents=False, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    images_dir = destination / "images"
    images_dir.mkdir(exist_ok=True, mode=0o700)
    images_dir.chmod(0o700)
    _write_create_only_idempotent(
        destination / "selection-lock.json", _canonical_bytes(selection_lock)
    )

    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("selection_lock_sha256") != selection_lock["selection_lock_sha256"]:
            raise RuntimeError("committed availability store has a different selection")
        _verify_available_store(destination, manifest)
        return manifest

    private_index_path = destination / "private-index.json"
    if private_index_path.exists():
        private_index_bytes = private_index_path.read_bytes()
        private_index = json.loads(private_index_bytes)
        expected_header = (
            "triage-private-preview-availability-index-v1",
            cohort_name,
            inventory_sha256,
            selection_sha256,
            selection_lock["selection_lock_sha256"],
        )
        actual_header = (
            private_index.get("schema"),
            private_index.get("cohort_name"),
            private_index.get("inventory_sha256"),
            private_index.get("selection_sha256"),
            private_index.get("selection_lock_sha256"),
        )
        if actual_header != expected_header:
            raise RuntimeError("uncommitted private availability index has different lineage")
        if len(private_index.get("available_rows", [])) < minimum_successes:
            raise RuntimeError("uncommitted private availability index is below its success floor")
        manifest = _available_public_manifest(
            private_index,
            private_index_bytes,
            minimum_successes=minimum_successes,
            memory_limit_bytes=memory_limit_bytes,
        )
        _verify_available_store(destination, manifest)
        _write_create_only_idempotent(manifest_path, _canonical_bytes(manifest))
        _fsync_directory(destination)
        return manifest

    reusable_previews = _load_reusable_previews(
        reuse_stores,
        inventory_sha256=inventory_sha256,
    )
    outcomes = _bounded_available_fetch(
        private_rows,
        destination=destination,
        fetch_preview=fetch_preview,
        reusable_previews=reusable_previews,
        workers=workers,
        memory_limit_bytes=memory_limit_bytes,
        progress=progress,
    )
    available_rows = [row for status, row in outcomes if status == "available"]
    unavailable_rows = [row for status, row in outcomes if status == "unavailable"]
    if len(available_rows) < minimum_successes:
        raise RuntimeError(
            f"only {len(available_rows)} of {len(private_rows)} previews were available; "
            f"need {minimum_successes}"
        )
    private_index = {
        "schema": "triage-private-preview-availability-index-v1",
        "cohort_name": cohort_name,
        "inventory_sha256": inventory_sha256,
        "selection_sha256": selection_sha256,
        "selection_lock_sha256": selection_lock["selection_lock_sha256"],
        "available_rows": available_rows,
        "unavailable_rows": unavailable_rows,
    }
    private_index_bytes = _canonical_bytes(private_index)
    _write_create_only_idempotent(destination / "private-index.json", private_index_bytes)
    manifest = _available_public_manifest(
        private_index,
        private_index_bytes,
        minimum_successes=minimum_successes,
        memory_limit_bytes=memory_limit_bytes,
    )
    _write_create_only(manifest_path, _canonical_bytes(manifest))
    _fsync_directory(destination)
    return manifest


def pin_preview_store(
    destination: Path,
    rows: Sequence[Any],
    *,
    cohort_name: str,
    inventory_sha256: str,
    selection_sha256: str,
    fetch_preview: Callable[[str], bytes],
    audit_prefix: str,
    workers: int = 4,
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Fetch, verify, and commit one private cohort outside the thumbnail LRU.

    ``fetch_preview`` must be safe to call concurrently when ``workers > 1``.
    The store is resumable until ``manifest.json`` is written; after that it is
    immutable and every invocation performs a full digest verification.
    """
    if not cohort_name.strip() or not audit_prefix.strip():
        raise ValueError("cohort_name and audit_prefix must not be empty")
    if len(inventory_sha256) != 64 or len(selection_sha256) != 64:
        raise ValueError("inventory and selection digests must be SHA-256 hex values")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between one and eight")
    materialized = tuple(rows)
    if not materialized:
        raise ValueError("preview store cannot pin an empty cohort")
    asset_ids = [str(row.asset_id) for row in materialized]
    if len(set(asset_ids)) != len(asset_ids):
        raise ValueError("preview store asset ids must be unique")
    _assert_memory_limit(memory_limit_bytes)

    ordered = sorted(
        materialized,
        key=lambda row: hashlib.sha256(f"{selection_sha256}\0{row.asset_id}".encode()).hexdigest(),
    )
    width = max(4, len(str(len(ordered))))
    private_rows = [
        _private_row(
            row,
            audit_id=f"{audit_prefix}{index:0{width}d}",
            image_relpath=f"images/{audit_prefix}{index:0{width}d}.jpg",
        )
        for index, row in enumerate(ordered, start=1)
    ]
    selection_lock = {
        "schema": "triage-preview-selection-lock-v1",
        "cohort_name": cohort_name,
        "inventory_sha256": inventory_sha256,
        "selection_sha256": selection_sha256,
        "rows": private_rows,
    }
    selection_lock["selection_lock_sha256"] = _canonical_sha256(selection_lock)

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    destination.mkdir(parents=False, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    images_dir = destination / "images"
    images_dir.mkdir(exist_ok=True, mode=0o700)
    images_dir.chmod(0o700)
    _write_create_only_idempotent(
        destination / "selection-lock.json", _canonical_bytes(selection_lock)
    )

    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("selection_lock_sha256") != selection_lock["selection_lock_sha256"]:
            raise RuntimeError("committed preview store has a different selection")
        _verify_committed_store(destination, manifest)
        return manifest

    finalized_rows = _bounded_fetch(
        private_rows,
        destination=destination,
        fetch_preview=fetch_preview,
        workers=workers,
        memory_limit_bytes=memory_limit_bytes,
        progress=progress,
    )
    private_index = {
        "schema": "triage-private-preview-index-v1",
        "cohort_name": cohort_name,
        "inventory_sha256": inventory_sha256,
        "selection_sha256": selection_sha256,
        "selection_lock_sha256": selection_lock["selection_lock_sha256"],
        "rows": finalized_rows,
    }
    private_index_bytes = _canonical_bytes(private_index)
    _write_create_only_idempotent(destination / "private-index.json", private_index_bytes)

    public_rows = [
        {
            key: row[key]
            for key in (
                "audit_id",
                "image_relpath",
                "preview_sha256",
                "preview_bytes",
                "preview_width",
                "preview_height",
                "preview_format",
            )
        }
        for row in finalized_rows
    ]
    manifest: dict[str, Any] = {
        "schema": "triage-pinned-preview-store-v1",
        "privacy": "private local evidence; asset mapping stored separately",
        "cohort_name": cohort_name,
        "inventory_sha256": inventory_sha256,
        "selection_sha256": selection_sha256,
        "selection_lock_sha256": selection_lock["selection_lock_sha256"],
        "private_index_sha256": hashlib.sha256(private_index_bytes).hexdigest(),
        "row_count": len(public_rows),
        "total_preview_bytes": sum(int(row["preview_bytes"]) for row in public_rows),
        "memory_limit_bytes": memory_limit_bytes,
        "rows": public_rows,
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    _write_create_only(manifest_path, _canonical_bytes(manifest))
    _fsync_directory(destination)
    return manifest
