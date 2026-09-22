"""Local-only inventory joins shared by the slice-1 scripts."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .embed import AssetInput
from .generate_labels import LabelAsset


@dataclass(frozen=True)
class AssetInventories:
    embedding_assets: list[AssetInput]
    label_assets: list[LabelAsset]
    never_train_ids: set[str]
    excluded_truth_previews: int
    missing_truth_images: int


@dataclass(frozen=True)
class IndexedPreview:
    """One authenticated private-index row, safe for local pipeline use only."""

    asset_id: str
    image_path: Path
    captured_at: str
    capture_day: str
    source_updated: str
    preview_sha256: str
    audit_id: str
    moment_key: str
    component_key: str


@dataclass(frozen=True)
class CohortPreviewIndex:
    """Authenticated durable-store contents and non-identifying lineage."""

    rows: tuple[IndexedPreview, ...]
    cohort_name: str
    inventory_sha256: str
    selection_sha256: str
    private_index_sha256: str
    manifest_sha256: str


_PRIVATE_INDEX_SCHEMA = "triage-private-preview-index-v1"
_PUBLIC_MANIFEST_SCHEMA = "triage-pinned-preview-store-v1"
_SELECTION_LOCK_SCHEMA = "triage-preview-selection-lock-v1"
_MAX_MEMORY_LIMIT_BYTES = 64 * 1024**3
_PRIVATE_TOP_LEVEL_KEYS = {
    "schema",
    "cohort_name",
    "inventory_sha256",
    "selection_sha256",
    "selection_lock_sha256",
    "rows",
}
_MANIFEST_TOP_LEVEL_KEYS = {
    "schema",
    "privacy",
    "cohort_name",
    "inventory_sha256",
    "selection_sha256",
    "selection_lock_sha256",
    "private_index_sha256",
    "row_count",
    "total_preview_bytes",
    "memory_limit_bytes",
    "rows",
    "manifest_sha256",
}
_SELECTION_LOCK_TOP_LEVEL_KEYS = {
    "schema",
    "cohort_name",
    "inventory_sha256",
    "selection_sha256",
    "rows",
    "selection_lock_sha256",
}
_PRIVATE_ROW_KEYS = {
    "audit_id",
    "asset_id",
    "source_updated",
    "captured_at",
    "capture_day",
    "moment_key",
    "component_key",
    "image_relpath",
    "preview_sha256",
    "preview_bytes",
    "preview_width",
    "preview_height",
    "preview_format",
}
_SELECTION_ROW_KEYS = {
    "audit_id",
    "asset_id",
    "source_updated",
    "captured_at",
    "capture_day",
    "moment_key",
    "component_key",
    "image_relpath",
}
_PUBLIC_ROW_KEYS = {
    "audit_id",
    "image_relpath",
    "preview_sha256",
    "preview_bytes",
    "preview_width",
    "preview_height",
    "preview_format",
}


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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_canonical_object(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular local file")
    if path.stat().st_mode & 0o077:
        raise PermissionError(f"{label} must not be group- or world-accessible")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    try:
        canonical = _canonical_bytes(payload)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not canonical JSON") from error
    if raw != canonical:
        raise ValueError(f"{label} bytes are not in the immutable canonical encoding")
    return payload, raw


def _require_exact_keys(payload: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"{label} does not match its declared schema")


def _require_nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _self_digest(payload: dict[str, Any], field: str, *, label: str) -> str:
    claimed = payload.get(field)
    if not _is_sha256(claimed):
        raise ValueError(f"{label} has an invalid digest")
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if _sha256_bytes(_canonical_bytes(unsigned)) != claimed:
        raise ValueError(f"{label} digest does not reproduce")
    return claimed


def _preview_details(path: Path) -> tuple[str, int, int, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("indexed preview must be a regular local file")
    if path.stat().st_mode & 0o077:
        raise PermissionError("indexed preview must not be group- or world-accessible")
    payload = path.read_bytes()
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            image_format = str(image.format or "unknown").lower()
    except (OSError, SyntaxError) as error:
        raise ValueError("indexed preview bytes are not a valid image") from error
    return _sha256_bytes(payload), len(payload), width, height, image_format


def load_cohort_preview_index(path: Path) -> CohortPreviewIndex:
    """Authenticate a pinned private index before exposing any asset mapping.

    All failures are deliberately aggregate: exception text never includes a
    private asset identifier or a path copied from the untrusted index.
    """
    index_path = Path(path)
    store = index_path.parent
    manifest_path = store / "manifest.json"
    selection_lock_path = store / "selection-lock.json"
    private, private_bytes = _load_canonical_object(index_path, label="private preview index")
    manifest, _ = _load_canonical_object(manifest_path, label="public preview manifest")
    selection_lock, _ = _load_canonical_object(selection_lock_path, label="preview selection lock")

    _require_exact_keys(private, _PRIVATE_TOP_LEVEL_KEYS, label="private preview index")
    _require_exact_keys(manifest, _MANIFEST_TOP_LEVEL_KEYS, label="public preview manifest")
    _require_exact_keys(
        selection_lock,
        _SELECTION_LOCK_TOP_LEVEL_KEYS,
        label="preview selection lock",
    )
    if private["schema"] != _PRIVATE_INDEX_SCHEMA:
        raise ValueError("unsupported private preview index schema")
    if manifest["schema"] != _PUBLIC_MANIFEST_SCHEMA:
        raise ValueError("unsupported public preview manifest schema")
    if selection_lock["schema"] != _SELECTION_LOCK_SCHEMA:
        raise ValueError("unsupported preview selection lock schema")

    private_index_sha256 = _sha256_bytes(private_bytes)
    if not _is_sha256(manifest["private_index_sha256"]):
        raise ValueError("public preview manifest has an invalid private-index digest")
    if manifest["private_index_sha256"] != private_index_sha256:
        raise ValueError("private preview index digest does not reproduce")
    manifest_sha256 = _self_digest(manifest, "manifest_sha256", label="public preview manifest")
    selection_lock_sha256 = _self_digest(
        selection_lock,
        "selection_lock_sha256",
        label="preview selection lock",
    )

    lineage_fields = ("cohort_name", "inventory_sha256", "selection_sha256")
    for field in lineage_fields:
        values = (private.get(field), manifest.get(field), selection_lock.get(field))
        if not all(value == values[0] for value in values[1:]):
            raise ValueError("pinned preview lineage differs across its sealed files")
    cohort_name = _require_nonempty_string(private["cohort_name"], label="cohort name")
    inventory_sha256 = private["inventory_sha256"]
    selection_sha256 = private["selection_sha256"]
    if not _is_sha256(inventory_sha256) or not _is_sha256(selection_sha256):
        raise ValueError("pinned preview lineage has an invalid digest")
    if (
        private.get("selection_lock_sha256") != selection_lock_sha256
        or manifest.get("selection_lock_sha256") != selection_lock_sha256
    ):
        raise ValueError("preview selection-lock digest differs across sealed files")

    memory_limit_bytes = _require_positive_int(
        manifest["memory_limit_bytes"], label="preview-store memory limit"
    )
    if memory_limit_bytes > _MAX_MEMORY_LIMIT_BYTES:
        raise ValueError("preview store exceeds the hard 64 GiB memory ceiling")
    private_rows = private["rows"]
    public_rows = manifest["rows"]
    selection_rows = selection_lock["rows"]
    if not all(isinstance(rows, list) for rows in (private_rows, public_rows, selection_rows)):
        raise ValueError("pinned preview row collections must be arrays")
    if not private_rows:
        raise ValueError("private preview index cannot be empty")
    if not (len(private_rows) == len(public_rows) == len(selection_rows)):
        raise ValueError("pinned preview row counts differ across sealed files")
    row_count = _require_positive_int(manifest["row_count"], label="preview row count")
    if row_count != len(private_rows):
        raise ValueError("public preview manifest row count does not reproduce")

    asset_ids: set[str] = set()
    audit_ids: set[str] = set()
    image_relpaths: set[str] = set()
    indexed: list[IndexedPreview] = []
    total_preview_bytes = 0
    for private_row, public_row, selection_row in zip(
        private_rows, public_rows, selection_rows, strict=True
    ):
        if not all(isinstance(row, dict) for row in (private_row, public_row, selection_row)):
            raise ValueError("pinned preview rows must be JSON objects")
        _require_exact_keys(private_row, _PRIVATE_ROW_KEYS, label="private preview row")
        _require_exact_keys(public_row, _PUBLIC_ROW_KEYS, label="public preview row")
        _require_exact_keys(selection_row, _SELECTION_ROW_KEYS, label="preview selection row")
        if selection_row != {key: private_row[key] for key in _SELECTION_ROW_KEYS}:
            raise ValueError("private preview row differs from its sealed selection")
        if public_row != {key: private_row[key] for key in _PUBLIC_ROW_KEYS}:
            raise ValueError("private preview row differs from its public manifest row")

        asset_id = _require_nonempty_string(private_row["asset_id"], label="asset id")
        audit_id = _require_nonempty_string(private_row["audit_id"], label="audit id")
        source_updated = _require_nonempty_string(
            private_row["source_updated"], label="source update timestamp"
        )
        captured_at = _require_nonempty_string(
            private_row["captured_at"], label="capture timestamp"
        )
        capture_day = _require_nonempty_string(private_row["capture_day"], label="capture day")
        moment_key = _require_nonempty_string(private_row["moment_key"], label="moment key")
        component_key = _require_nonempty_string(
            private_row["component_key"], label="component key"
        )
        if _parse_day(captured_at) != capture_day:
            raise ValueError("indexed capture timestamp and capture day differ")
        image_relpath = _require_nonempty_string(
            private_row["image_relpath"], label="preview relative path"
        )
        if image_relpath != f"images/{audit_id}.jpg":
            raise ValueError("indexed preview path is not the expected opaque audit path")
        if asset_id in asset_ids or audit_id in audit_ids or image_relpath in image_relpaths:
            raise ValueError("private preview index contains duplicate identities or paths")
        asset_ids.add(asset_id)
        audit_ids.add(audit_id)
        image_relpaths.add(image_relpath)

        image_path = store / image_relpath
        digest, size, width, height, image_format = _preview_details(image_path)
        if not _is_sha256(private_row["preview_sha256"]):
            raise ValueError("private preview row has an invalid pixel digest")
        expected_details = (
            private_row["preview_sha256"],
            _require_positive_int(private_row["preview_bytes"], label="preview byte count"),
            _require_positive_int(private_row["preview_width"], label="preview width"),
            _require_positive_int(private_row["preview_height"], label="preview height"),
            _require_nonempty_string(private_row["preview_format"], label="preview format"),
        )
        if (digest, size, width, height, image_format) != expected_details:
            raise ValueError("indexed preview bytes differ from their sealed metadata")
        total_preview_bytes += size
        indexed.append(
            IndexedPreview(
                asset_id=asset_id,
                image_path=image_path,
                captured_at=captured_at,
                capture_day=capture_day,
                source_updated=source_updated,
                preview_sha256=digest,
                audit_id=audit_id,
                moment_key=moment_key,
                component_key=component_key,
            )
        )

    manifest_total = _require_positive_int(
        manifest["total_preview_bytes"], label="total preview byte count"
    )
    if manifest_total != total_preview_bytes:
        raise ValueError("public preview manifest byte total does not reproduce")
    if asset_ids & set(_all_string_values(manifest)):
        raise ValueError("public preview manifest exposes a private asset identifier")
    return CohortPreviewIndex(
        rows=tuple(indexed),
        cohort_name=cohort_name,
        inventory_sha256=inventory_sha256,
        selection_sha256=selection_sha256,
        private_index_sha256=private_index_sha256,
        manifest_sha256=manifest_sha256,
    )


def _all_string_values(payload: object) -> list[str]:
    if isinstance(payload, dict):
        return [value for item in payload.values() for value in _all_string_values(item)]
    if isinstance(payload, list):
        return [value for item in payload for value in _all_string_values(item)]
    return [payload] if isinstance(payload, str) else []


def _parse_day(timestamp: str) -> str:
    normalized = timestamp.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError as error:
        raise ValueError("invalid capture timestamp in local metadata") from error


def _truth_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            ids.add(str(json.loads(line)["image_id"]))
    return ids


def _raw_metadata(directory: Path, wanted_ids: set[str]) -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("raw Immich metadata file must contain an array")
        for row in payload:
            asset_id = str(row.get("id", ""))
            if asset_id not in wanted_ids:
                continue
            capture = str(row.get("fileCreatedAt") or row.get("localDateTime") or "")
            updated = str(row.get("updatedAt") or capture)
            if not capture:
                raise ValueError("raw metadata row has no capture timestamp")
            prior = rows.get(asset_id)
            value = (capture, updated)
            if prior is not None and prior != value:
                raise ValueError("conflicting raw metadata for one training asset")
            rows[asset_id] = value
    return rows


def build_asset_inventories(
    *,
    preview_dir: Path,
    truth_jsonl: Path,
    review_data_path: Path,
    timestamps_path: Path,
    raw_metadata_dir: Path,
    cohort_index_path: Path | None = None,
) -> AssetInventories:
    """Build disjoint label/eval inventories from existing local artifacts."""
    indexed_rows: dict[str, IndexedPreview] = {}
    if cohort_index_path is None:
        preview_paths = {path.stem: path for path in sorted(preview_dir.glob("*.jpg"))}
    else:
        cohort_index = load_cohort_preview_index(cohort_index_path)
        indexed_rows = {row.asset_id: row for row in cohort_index.rows}
        preview_paths = {asset_id: row.image_path for asset_id, row in indexed_rows.items()}
    if not preview_paths:
        raise ValueError("preview directory contains no JPEGs")
    never_train_ids = _truth_ids(truth_jsonl)
    training_ids = set(preview_paths) - never_train_ids
    if cohort_index_path is None:
        timestamp_payload = json.loads(timestamps_path.read_text(encoding="utf-8"))
        timestamps = {
            str(asset_id): str(value)
            for asset_id, value in timestamp_payload.get("timestamps", {}).items()
        }
        raw = _raw_metadata(raw_metadata_dir, training_ids)
    else:
        timestamps = {}
        raw = {}

    label_assets: list[LabelAsset] = []
    embedding_assets: list[AssetInput] = []
    missing_group_count = 0
    for asset_id in sorted(training_ids):
        indexed = indexed_rows.get(asset_id)
        if indexed is not None:
            capture = indexed.captured_at
            updated = indexed.source_updated
            preview_sha256 = indexed.preview_sha256
            capture_day = indexed.capture_day
        else:
            capture = timestamps.get(asset_id)
            updated = capture or ""
            if capture is None and asset_id in raw:
                capture, updated = raw[asset_id]
            elif asset_id in raw:
                updated = raw[asset_id][1]
            preview_sha256 = None
            capture_day = _parse_day(capture) if capture else ""
        if not capture:
            missing_group_count += 1
            continue
        label_asset = LabelAsset(
            asset_id=asset_id,
            image_path=preview_paths[asset_id],
            group_key=f"capture-day:{capture_day}",
            source_updated=updated,
            preview_sha256=preview_sha256,
        )
        label_assets.append(label_asset)
        embedding_assets.append(
            AssetInput(
                asset_id,
                label_asset.image_path,
                label_asset.source_updated,
                preview_sha256=preview_sha256,
            )
        )
    if missing_group_count:
        raise ValueError(f"{missing_group_count} training assets have no safe capture-day group")

    review_payload = json.loads(review_data_path.read_text(encoding="utf-8"))
    missing_truth_images = 0
    review_ids: set[str] = set()
    for row in review_payload:
        asset_id = str(row["image_id"])
        review_ids.add(asset_id)
        if asset_id not in never_train_ids:
            raise ValueError("review_data asset is absent from the verified truth set")
        image_path = Path(row["image"])
        if not image_path.is_file():
            missing_truth_images += 1
            continue
        # Offline truth artifacts do not carry Immich updatedAt. Pixel hashing
        # remains the authoritative invalidator for these evaluation-only rows.
        embedding_assets.append(AssetInput(asset_id, image_path, "eval-only:unknown"))
    if review_ids != never_train_ids:
        raise ValueError("review_data and verified truth asset sets differ")

    return AssetInventories(
        embedding_assets=embedding_assets,
        label_assets=label_assets,
        never_train_ids=never_train_ids,
        excluded_truth_previews=len(set(preview_paths) & never_train_ids),
        missing_truth_images=missing_truth_images,
    )
