#!/usr/bin/env python3
"""Embed image previews with a pinned DINOv2 ONNX export.

The slice-1 cache keeps the raw 2,304-d pooled token pack in a staging table.
``train.py`` fits PCA on training assets only, projects every staged pack, and
then fills the design's 256-d fp16 ``embeddings`` table.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

if __package__:
    from .memory import (
        DEFAULT_OFFLINE_WORKING_SET_GIB,
        acquire_pipeline_lock,
        ensure_offline_process_memory,
        ensure_private_directory,
        validate_offline_working_set_gib,
    )
    from .preview_store import verify_committed_preview_store
else:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.triage_heads.memory import (
        DEFAULT_OFFLINE_WORKING_SET_GIB,
        acquire_pipeline_lock,
        ensure_offline_process_memory,
        ensure_private_directory,
        validate_offline_working_set_gib,
    )
    from scripts.triage_heads.preview_store import verify_committed_preview_store

PREPROCESS_VERSION = "dinov2-s14-224-crop-v1"
LAYOUT_VERSION = "cls+mean+quad2x2/pca256/fp16"
TOKEN_DIM = 384
PATCH_GRID = 16
PACK_DIM = 6 * TOKEN_DIM
PROJECTED_DIM = 256
RESIZE_SHORT_SIDE = 256
CROP_SIZE = 224
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MODEL_ID = "facebook/dinov2-small"
MODEL_REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
EXPECTED_ONNX_SHA256 = "478164cd290ee78e5ddb4fcc474136eec714b4b8253a3609cc7164b592e958af"
DEFAULT_ARTIFACT_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads"
DEFAULT_PREVIEW_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30" / "previews"
DEFAULT_TRUTH_DIR = Path.home() / ".immich-memories-matrix" / "description-truth-2026-08-31"
DEFAULT_FRESH_CERTIFICATION_APPROVAL = (
    DEFAULT_ARTIFACT_DIR / "recovery-1b" / "fresh-location-cert-v3-approval"
)
DEFAULT_TIMESTAMPS = (
    Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30" / "timestamps.json"
)
DEFAULT_RAW_METADATA = (
    Path.home() / ".immich-memories-matrix" / "slice4-metadata-2026-08-27" / "cache" / "raw"
)


@dataclass(frozen=True)
class EncoderSpec:
    """The four immutable fields that identify reusable image features."""

    encoder_id: str
    weights_sha256: str
    preprocess_version: str = PREPROCESS_VERSION
    layout_version: str = LAYOUT_VERSION

    def __post_init__(self) -> None:
        revision = self.encoder_id.rpartition("@")[2]
        if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
            raise ValueError("encoder_id must end in a lowercase 40-character git SHA")
        if len(self.weights_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.weights_sha256
        ):
            raise ValueError("weights_sha256 must be a lowercase SHA-256 digest")

    @property
    def key(self) -> str:
        # The design's ‖ notation means byte concatenation in this field order.
        material = (
            self.encoder_id + self.weights_sha256 + self.preprocess_version + self.layout_version
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AssetInput:
    asset_id: str
    image_path: Path
    source_updated: str
    preview_sha256: str | None = None


def _canonical_private_bytes(payload: object) -> bytes:
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


def _require_private_directory(path: Path, *, label: str) -> None:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"{label} must be a private local directory")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} must be a private local directory") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a private local directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PermissionError(f"{label} must have mode 0700")


def _require_single_link_private_file(path: Path, *, label: str) -> None:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"{label} must be a single-link regular file")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} must be a single-link regular file") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(f"{label} must be a single-link regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise PermissionError(f"{label} must have mode 0600")


def _validate_active_v2_preview_store(store: Path) -> None:
    """Require the exact committed store and reject mutable aliases."""
    preview_store = Path(store)
    _require_private_directory(preview_store.parent, label="active-v2 owner cohort root")
    _require_private_directory(preview_store, label="active-v2 preview store")
    images = preview_store / "images"
    _require_private_directory(images, label="active-v2 preview image directory")
    evidence_files = (
        preview_store / "private-index.json",
        preview_store / "manifest.json",
        preview_store / "selection-lock.json",
        *sorted(images.iterdir(), key=lambda path: path.name),
    )
    for path in evidence_files:
        _require_single_link_private_file(path, label="active-v2 preview evidence")
    try:
        verify_committed_preview_store(preview_store)
    except (OSError, PermissionError, RuntimeError, ValueError) as error:
        raise ValueError(f"active-v2 preview store is not committed: {error}") from error
    # Recheck link/mode state after the verifier has consumed every source file.
    for path in evidence_files:
        _require_single_link_private_file(path, label="active-v2 preview evidence")


def load_active_v2_fresh_truth_binding(
    cohort_index_path: Path,
    *,
    cohort_index: Any | None = None,
) -> dict[str, str]:
    """Authenticate the fresh400 approval identity sealed into the owner v2 run."""
    index_path = Path(cohort_index_path)
    if index_path.name != "private-index.json" or index_path.parent.name != "training-previews-v2":
        raise ValueError("active-v2 embedding requires the exact owner v2 index path")
    _validate_active_v2_preview_store(index_path.parent)
    if cohort_index is None:
        if __package__:
            from .data import load_cohort_preview_index
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.data import load_cohort_preview_index

        cohort_index = load_cohort_preview_index(index_path)
    manifest_path = index_path.parent.parent / "training-cohort-public.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("active-v2 owner manifest must be a regular local file")
    metadata = manifest_path.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        raise PermissionError("active-v2 owner manifest must be single-link mode 0600")
    raw = manifest_path.read_bytes()
    try:
        public = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("active-v2 owner manifest is not valid JSON") from error
    expected_keys = {
        "schema",
        "status",
        "privacy",
        "inventory_sha256",
        "inventory_asset_count",
        "truth_ledger_sha256",
        "required_truth_cohort",
        "fresh_truth_selection_sha256",
        "fresh_truth_approval",
        "semantic_inputs",
        "config",
        "source_v1",
        "acquisition",
        "raw_pixel_audit",
        "final_selection",
        "private_evidence",
        "memory_provenance",
        "run_sha256",
    }
    if not isinstance(public, dict) or set(public) != expected_keys:
        raise ValueError("active-v2 owner manifest does not match its exact schema")
    if raw != _canonical_private_bytes(public):
        raise ValueError("active-v2 owner manifest is not canonical")
    run_sha256 = public.get("run_sha256")
    unsigned = dict(public)
    unsigned.pop("run_sha256")
    if (
        not isinstance(run_sha256, str)
        or hashlib.sha256(
            b"triage-owner-training-cohort-v2\0" + _canonical_private_bytes(unsigned)
        ).hexdigest()
        != run_sha256
    ):
        raise ValueError("active-v2 owner manifest digest does not reproduce")
    fresh = public.get("fresh_truth_approval")
    final_selection = public.get("final_selection")
    private_evidence = public.get("private_evidence")
    if not all(isinstance(value, dict) for value in (fresh, final_selection, private_evidence)):
        raise ValueError("active-v2 owner fresh-truth lineage is malformed")
    if (
        public.get("schema") != "triage-owner-training-cohort-public-v2"
        or public.get("status") != "awaiting_visual_review"
        or public.get("required_truth_cohort") != "fresh-location-cert-v3"
        or public.get("semantic_inputs") != "disabled"
        or getattr(cohort_index, "cohort_name", None) != "location-training-v2"
        or public.get("inventory_sha256") != getattr(cohort_index, "inventory_sha256", None)
        or final_selection.get("selection_sha256")
        != getattr(cohort_index, "selection_sha256", None)
        or private_evidence.get("final_preview_manifest_sha256")
        != getattr(cohort_index, "manifest_sha256", None)
        or set(fresh) != {"authenticated", "public_sha256", "private_sha256"}
        or fresh.get("authenticated") is not True
    ):
        raise ValueError("active-v2 owner fresh-truth lineage does not reproduce")
    digests = {
        "inventory_sha256": public.get("inventory_sha256"),
        "fresh_truth_selection_sha256": public.get("fresh_truth_selection_sha256"),
        "fresh_truth_approval_private_sha256": fresh.get("private_sha256"),
        "fresh_truth_approval_public_sha256": fresh.get("public_sha256"),
        "owner_cohort_run_sha256": run_sha256,
    }
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in digests.values()
    ):
        raise ValueError("active-v2 owner fresh-truth lineage contains an invalid digest")
    return {key: str(value) for key, value in digests.items()}


def combine_active_v2_embedding_assets(
    owner_assets: Sequence[AssetInput],
    fresh_rows: Sequence[Any],
) -> list[AssetInput]:
    """Add the exact approved fresh400 pixels with their immutable cache lineage."""
    owner = list(owner_assets)
    owner_ids = [asset.asset_id for asset in owner]
    if len(owner_ids) != len(set(owner_ids)):
        raise ValueError("owner embedding inventory contains duplicate assets")
    owner_id_set = set(owner_ids)
    if len(fresh_rows) != 400:
        raise ValueError("active-v2 embedding requires the exact fresh400 approval")
    fresh: list[AssetInput] = []
    fresh_ids: set[str] = set()
    for row in fresh_rows:
        asset_id = str(getattr(row, "asset_id", ""))
        preview_sha256 = getattr(row, "preview_sha256", None)
        source_updated = str(getattr(row, "source_updated", ""))
        image_path = Path(getattr(row, "image_path", ""))
        if (
            not asset_id
            or asset_id in fresh_ids
            or asset_id in owner_id_set
            or not isinstance(preview_sha256, str)
            or len(preview_sha256) != 64
            or any(character not in "0123456789abcdef" for character in preview_sha256)
            or not source_updated
            or not image_path.is_absolute()
        ):
            raise ValueError("fresh400 embedding rows have invalid or overlapping lineage")
        fresh_ids.add(asset_id)
        fresh.append(
            AssetInput(
                asset_id=asset_id,
                image_path=image_path,
                source_updated=source_updated,
                preview_sha256=preview_sha256,
            )
        )
    return [*owner, *fresh]


@dataclass(frozen=True)
class EmbedRun:
    requested: int
    computed: int
    cached: int
    elapsed_seconds: float
    ms_per_computed_image: float


def pool_token_pack(tokens: np.ndarray) -> np.ndarray:
    """Pool ``[CLS | patches]`` into CLS, mean, and four 8x8 quadrant means."""
    array = np.asarray(tokens, dtype=np.float32)
    expected = (1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM)
    if array.ndim != 3 or array.shape[1:] != expected:
        raise ValueError(
            f"expected [batch, {expected[0]}, {expected[1]}] tokens, got {array.shape}"
        )
    cls = array[:, 0, :]
    patches = array[:, 1:, :].reshape(-1, PATCH_GRID, PATCH_GRID, TOKEN_DIM)
    pooled = [cls, patches.mean(axis=(1, 2))]
    half = PATCH_GRID // 2
    pooled.extend(
        quadrant.mean(axis=(1, 2))
        for quadrant in (
            patches[:, :half, :half, :],
            patches[:, :half, half:, :],
            patches[:, half:, :half, :],
            patches[:, half:, half:, :],
        )
    )
    return np.concatenate(pooled, axis=1).astype(np.float32, copy=False)


def preprocess_image_bytes(payload: bytes) -> np.ndarray:
    """Apply the DINOv2 transform to one immutable preview byte snapshot."""
    with Image.open(io.BytesIO(payload)) as handle:
        image = handle.convert("RGB")
    width, height = image.size
    if width <= height:
        resized = (RESIZE_SHORT_SIDE, round(height * RESIZE_SHORT_SIDE / width))
    else:
        resized = (round(width * RESIZE_SHORT_SIDE / height), RESIZE_SHORT_SIDE)
    image = image.resize(resized, Image.Resampling.BICUBIC)
    left = (resized[0] - CROP_SIZE) // 2
    top = (resized[1] - CROP_SIZE) // 2
    image = image.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    normalized = (pixels - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1), dtype=np.float32)


def preprocess_image(path: Path) -> np.ndarray:
    """Apply the reference DINOv2 transform to one file snapshot."""
    return preprocess_image_bytes(path.read_bytes())


class EmbeddingCache:
    """Disposable SQLite cache matching the architecture document's schema."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("embedding cache database cannot be a symlink")
        if self.path.exists() and not self.path.is_file():
            raise ValueError("embedding cache database must be a regular local file")
        ensure_private_directory(self.path.parent)
        self._initialize()
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise ValueError("embedding cache database cannot be a symlink")
        connection = sqlite3.connect(self.path)
        if self.path.is_symlink() or not self.path.is_file():
            connection.close()
            raise ValueError("embedding cache database must be a regular local file")
        self.path.chmod(0o600)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS encoder_registry (
                    encoder_key        TEXT PRIMARY KEY,
                    encoder_id         TEXT NOT NULL,
                    weights_sha256     TEXT NOT NULL,
                    preprocess_version TEXT NOT NULL,
                    layout_version     TEXT NOT NULL,
                    registered_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS embeddings (
                    asset_id       TEXT NOT NULL,
                    encoder_key    TEXT NOT NULL,
                    vector         BLOB NOT NULL,
                    preview_sha256 TEXT NOT NULL,
                    source_updated TEXT NOT NULL,
                    embedded_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (asset_id, encoder_key)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS head_facts (
                    asset_id     TEXT NOT NULL,
                    head_name    TEXT NOT NULL,
                    head_version TEXT NOT NULL,
                    encoder_key  TEXT NOT NULL,
                    label        TEXT NOT NULL,
                    confidence   REAL NOT NULL,
                    covered      INTEGER NOT NULL,
                    decided_at   TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (asset_id, head_name, head_version, encoder_key)
                ) WITHOUT ROWID;

                CREATE INDEX IF NOT EXISTS head_facts_lookup
                    ON head_facts (head_name, head_version, label);

                CREATE TABLE IF NOT EXISTS embedding_staging (
                    asset_id       TEXT NOT NULL,
                    encoder_key    TEXT NOT NULL,
                    pack           BLOB NOT NULL,
                    preview_sha256 TEXT NOT NULL,
                    source_updated TEXT NOT NULL,
                    embedded_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (asset_id, encoder_key)
                ) WITHOUT ROWID;
                """
            )

    def register_encoder(self, spec: EncoderSpec) -> None:
        fields = (
            spec.key,
            spec.encoder_id,
            spec.weights_sha256,
            spec.preprocess_version,
            spec.layout_version,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO encoder_registry (
                    encoder_key, encoder_id, weights_sha256,
                    preprocess_version, layout_version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                fields,
            )
            stored = connection.execute(
                """
                SELECT encoder_key, encoder_id, weights_sha256,
                       preprocess_version, layout_version
                FROM encoder_registry WHERE encoder_key = ?
                """,
                (spec.key,),
            ).fetchone()
        if stored != fields:
            raise RuntimeError("encoder registry collision")

    def remember_staging_pack(
        self,
        *,
        asset_id: str,
        encoder_key: str,
        pack: np.ndarray,
        preview_sha256: str,
        source_updated: str,
    ) -> None:
        vector = np.asarray(pack, dtype=np.float32)
        if vector.shape != (PACK_DIM,):
            raise ValueError(f"staging pack must have shape ({PACK_DIM},), got {vector.shape}")
        with self._connect() as connection:
            prior = connection.execute(
                """
                SELECT preview_sha256, source_updated FROM embedding_staging
                WHERE asset_id = ? AND encoder_key = ?
                """,
                (asset_id, encoder_key),
            ).fetchone()
            if prior != (preview_sha256, source_updated):
                # Pixel/source changes invalidate every downstream projection and
                # decision for this asset, including PCA-versioned encoder keys.
                connection.execute("DELETE FROM embeddings WHERE asset_id = ?", (asset_id,))
                connection.execute("DELETE FROM head_facts WHERE asset_id = ?", (asset_id,))
            connection.execute(
                """
                INSERT INTO embedding_staging (
                    asset_id, encoder_key, pack, preview_sha256, source_updated
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, encoder_key) DO UPDATE SET
                    pack = excluded.pack,
                    preview_sha256 = excluded.preview_sha256,
                    source_updated = excluded.source_updated,
                    embedded_at = datetime('now')
                """,
                (asset_id, encoder_key, vector.tobytes(), preview_sha256, source_updated),
            )

    def staging_packs(self, asset_ids: Sequence[str], encoder_key: str) -> dict[str, np.ndarray]:
        if not asset_ids:
            return {}
        rows: dict[str, np.ndarray] = {}
        # SQLite defaults to 999 bound parameters. Chunking also bounds memory.
        for start in range(0, len(asset_ids), 900):
            chunk = list(asset_ids[start : start + 900])
            placeholders = ",".join("?" for _ in chunk)
            with self._connect() as connection:
                result = connection.execute(
                    f"""
                    SELECT asset_id, pack FROM embedding_staging
                    WHERE encoder_key = ? AND asset_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders, not values, are interpolated
                    [encoder_key, *chunk],
                )
                for asset_id, blob in result:
                    pack = np.frombuffer(blob, dtype=np.float32)
                    if pack.shape != (PACK_DIM,):
                        raise ValueError(f"corrupt staging pack for {asset_id}")
                    rows[asset_id] = pack.copy()
        return rows

    def staging_provenance(
        self, asset_ids: Sequence[str], encoder_key: str
    ) -> dict[str, tuple[str, str]]:
        if not asset_ids:
            return {}
        rows: dict[str, tuple[str, str]] = {}
        for start in range(0, len(asset_ids), 900):
            chunk = list(asset_ids[start : start + 900])
            placeholders = ",".join("?" for _ in chunk)
            with self._connect() as connection:
                result = connection.execute(
                    f"""
                    SELECT asset_id, preview_sha256, source_updated
                    FROM embedding_staging
                    WHERE encoder_key = ? AND asset_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders, not values, are interpolated
                    [encoder_key, *chunk],
                )
                rows.update(
                    (asset_id, (preview_sha256, source_updated))
                    for asset_id, preview_sha256, source_updated in result
                )
        return rows

    def staging_snapshot(
        self, asset_ids: Sequence[str], encoder_key: str
    ) -> dict[str, tuple[np.ndarray, str, str]]:
        """Read packs and their lineage from one consistent SQLite snapshot."""
        if not asset_ids:
            return {}
        rows: dict[str, tuple[np.ndarray, str, str]] = {}
        with self._connect() as connection:
            connection.execute("BEGIN")
            for start in range(0, len(asset_ids), 900):
                chunk = list(asset_ids[start : start + 900])
                placeholders = ",".join("?" for _ in chunk)
                result = connection.execute(
                    f"""
                    SELECT asset_id, pack, preview_sha256, source_updated
                    FROM embedding_staging
                    WHERE encoder_key = ? AND asset_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders, not values, are interpolated
                    [encoder_key, *chunk],
                )
                for asset_id, blob, preview_sha256, source_updated in result:
                    pack = np.frombuffer(blob, dtype=np.float32)
                    if pack.shape != (PACK_DIM,):
                        raise ValueError(f"corrupt staging pack for {asset_id}")
                    rows[str(asset_id)] = (
                        pack.copy(),
                        str(preview_sha256),
                        str(source_updated),
                    )
        return rows

    def remember_vectors(
        self,
        vectors: dict[str, np.ndarray],
        encoder_key: str,
        *,
        provenance_encoder_key: str | None = None,
    ) -> None:
        provenance = self.staging_provenance(list(vectors), provenance_encoder_key or encoder_key)
        missing = len(vectors) - len(provenance)
        if missing:
            raise ValueError(f"{missing} projected vectors have no staging provenance")
        rows = []
        for asset_id, vector in vectors.items():
            array = np.asarray(vector, dtype=np.float16)
            if array.shape != (PROJECTED_DIM,):
                raise ValueError(
                    f"projected vector must have shape ({PROJECTED_DIM},), got {array.shape}"
                )
            preview_sha256, source_updated = provenance[asset_id]
            rows.append((asset_id, encoder_key, array.tobytes(), preview_sha256, source_updated))
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO embeddings (
                    asset_id, encoder_key, vector, preview_sha256, source_updated
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, encoder_key) DO UPDATE SET
                    vector = excluded.vector,
                    preview_sha256 = excluded.preview_sha256,
                    source_updated = excluded.source_updated,
                    embedded_at = datetime('now')
                """,
                rows,
            )

    def vectors_for(self, asset_ids: Sequence[str], encoder_key: str) -> dict[str, np.ndarray]:
        if not asset_ids:
            return {}
        rows: dict[str, np.ndarray] = {}
        for start in range(0, len(asset_ids), 900):
            chunk = list(asset_ids[start : start + 900])
            placeholders = ",".join("?" for _ in chunk)
            with self._connect() as connection:
                result = connection.execute(
                    f"""
                    SELECT asset_id, vector FROM embeddings
                    WHERE encoder_key = ? AND asset_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders, not values, are interpolated
                    [encoder_key, *chunk],
                )
                for asset_id, blob in result:
                    vector = np.frombuffer(blob, dtype=np.float16)
                    if vector.shape != (PROJECTED_DIM,):
                        raise ValueError(f"corrupt projected vector for {asset_id}")
                    rows[asset_id] = vector.copy()
        return rows

    def all_staging_ids(self, encoder_key: str) -> list[str]:
        with self._connect() as connection:
            result = connection.execute(
                "SELECT asset_id FROM embedding_staging WHERE encoder_key = ? ORDER BY asset_id",
                (encoder_key,),
            )
            return [str(row[0]) for row in result]

    def remember_facts(
        self,
        facts: Sequence[dict[str, Any]],
        *,
        head_name: str,
        head_version: str,
        encoder_key: str,
    ) -> None:
        rows = []
        for fact in facts:
            covered = int(bool(fact["covered"]))
            confidence = float(fact["confidence"])
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("head-fact confidence must be in [0, 1]")
            rows.append(
                (
                    str(fact["asset_id"]),
                    head_name,
                    head_version,
                    encoder_key,
                    str(fact["label"]),
                    confidence,
                    covered,
                )
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO head_facts (
                    asset_id, head_name, head_version, encoder_key,
                    label, confidence, covered
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, head_name, head_version, encoder_key) DO UPDATE SET
                    label = excluded.label,
                    confidence = excluded.confidence,
                    covered = excluded.covered,
                    decided_at = datetime('now')
                """,
                rows,
            )


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_onnx_artifact(path: Path) -> str:
    """Fail closed unless the graph is the validated export of the pinned revision."""
    digest = _file_sha256(path)
    if digest != EXPECTED_ONNX_SHA256:
        raise RuntimeError(
            "STOP: pinned ONNX digest mismatch; refusing to attribute an unknown graph "
            f"to {MODEL_ID}@{MODEL_REVISION}"
        )
    return digest


def _token_output(outputs: Sequence[Any]) -> np.ndarray:
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 3 and array.shape[1:] == (1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM):
            return array.astype(np.float32, copy=False)
    shapes = [np.asarray(output).shape for output in outputs]
    raise ValueError(f"ONNX export did not return [batch, 257, 384] tokens; outputs={shapes}")


def embed_assets(
    assets: Sequence[AssetInput],
    *,
    spec: EncoderSpec,
    cache: EmbeddingCache,
    session: Any,
    batch_size: int,
    memory_guard: Callable[[], int] | None = None,
) -> EmbedRun:
    """Compute cold or invalidated packs and leave valid cache rows untouched."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if memory_guard is not None:
        memory_guard()
    cache.register_encoder(spec)
    ids = [asset.asset_id for asset in assets]
    if len(set(ids)) != len(ids):
        raise ValueError("asset ids must be unique")
    provenance = cache.staging_provenance(ids, spec.key)
    pending: list[tuple[AssetInput, str]] = []
    for asset in assets:
        preview_sha256 = _file_sha256(asset.image_path)
        if asset.preview_sha256 is not None and preview_sha256 != asset.preview_sha256:
            raise ValueError("preview bytes differ from the authenticated cohort index")
        if provenance.get(asset.asset_id) != (preview_sha256, asset.source_updated):
            pending.append((asset, preview_sha256))

    started = time.monotonic()
    input_name = session.get_inputs()[0].name
    for start in range(0, len(pending), batch_size):
        if memory_guard is not None:
            memory_guard()
        chunk = pending[start : start + batch_size]
        snapshots: list[tuple[AssetInput, bytes, str]] = []
        for asset, _admission_sha256 in chunk:
            payload = asset.image_path.read_bytes()
            preview_sha256 = hashlib.sha256(payload).hexdigest()
            if asset.preview_sha256 is not None and preview_sha256 != asset.preview_sha256:
                raise ValueError("preview bytes differ from the authenticated cohort index")
            snapshots.append((asset, payload, preview_sha256))
        batch = np.stack(
            [preprocess_image_bytes(payload) for _asset, payload, _digest in snapshots]
        )
        tokens = _token_output(session.run(None, {input_name: batch}))
        packs = pool_token_pack(tokens)
        for (asset, _payload, preview_sha256), pack in zip(snapshots, packs, strict=True):
            cache.remember_staging_pack(
                asset_id=asset.asset_id,
                encoder_key=spec.key,
                pack=pack,
                preview_sha256=preview_sha256,
                source_updated=asset.source_updated,
            )
        if memory_guard is not None:
            memory_guard()
    elapsed = time.monotonic() - started
    computed = len(pending)
    return EmbedRun(
        requested=len(assets),
        computed=computed,
        cached=len(assets) - computed,
        elapsed_seconds=elapsed,
        ms_per_computed_image=(1000.0 * elapsed / computed) if computed else 0.0,
    )


def create_onnx_session(model_path: Path, provider: str) -> Any:
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    if provider == "cpu":
        providers: list[Any] = ["CPUExecutionProvider"]
    elif provider == "coreml":
        if "CoreMLExecutionProvider" not in available:
            raise RuntimeError("CoreMLExecutionProvider is unavailable")
        providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    elif provider == "auto":
        providers = (
            ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            if "CoreMLExecutionProvider" in available
            else ["CPUExecutionProvider"]
        )
    else:
        raise ValueError(f"unknown provider: {provider}")
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 2) - 1))
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(model_path, sess_options=options, providers=providers)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument(
        "--onnx",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR / "dinov2-small-ed25f3a" / "model.onnx",
    )
    parser.add_argument("--cache-db", type=Path, default=None)
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_PREVIEW_DIR)
    parser.add_argument(
        "--cohort-index",
        type=Path,
        default=None,
        help="authenticated durable-store private-index.json (uses opaque preview filenames)",
    )
    parser.add_argument("--truth-dir", type=Path, default=DEFAULT_TRUTH_DIR)
    parser.add_argument(
        "--fresh-certification-approval",
        type=Path,
        default=DEFAULT_FRESH_CERTIFICATION_APPROVAL,
        help="exact fresh-location-cert-v3 approval used with the owner v2 cohort",
    )
    parser.add_argument("--timestamps", type=Path, default=DEFAULT_TIMESTAMPS)
    parser.add_argument("--raw-metadata-dir", type=Path, default=DEFAULT_RAW_METADATA)
    parser.add_argument("--provider", choices=("cpu", "coreml", "auto"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--max-working-set-gib",
        type=float,
        default=DEFAULT_OFFLINE_WORKING_SET_GIB,
        help="embedding-process RSS ceiling (hard maximum: 8 GiB)",
    )
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 64:
        parser.error("--batch-size must be between 1 and 64")
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    try:
        validate_offline_working_set_gib(args.max_working_set_gib)
    except ValueError as error:
        parser.error(str(error))
    return args


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _acquire_artifact_lock(
    directory: Path,
    *,
    cache_db: Path | None = None,
    shared_root: Path | None = None,
):
    return acquire_pipeline_lock(
        directory,
        cache_db=cache_db,
        shared_root=shared_root,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)

    def memory_guard() -> int:
        return ensure_offline_process_memory(max_working_set_gib=args.max_working_set_gib)

    memory_guard()
    cache_path = args.cache_db or args.artifact_dir / "embeddings.db"
    ensure_private_directory(args.artifact_dir)
    try:
        _artifact_lock = _acquire_artifact_lock(
            args.artifact_dir,
            cache_db=cache_path,
            shared_root=DEFAULT_ARTIFACT_DIR,
        )
    except RuntimeError as error:
        raise SystemExit(f"STOP: {error}") from error
    if not args.onnx.is_file():
        raise SystemExit(f"STOP: pinned ONNX export not found at {args.onnx}")
    # Imported lazily to keep cache/pooling unit tests independent of inventory files.
    if __package__:
        from .data import build_asset_inventories, load_cohort_preview_index
    else:  # pragma: no cover - direct script invocation
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scripts.triage_heads.data import (
            build_asset_inventories,
            load_cohort_preview_index,
        )

    active_v2 = (
        args.cohort_index is not None
        and Path(args.cohort_index).parent.name == "training-previews-v2"
    )
    fresh_lineage: dict[str, Any] | None = None
    active_binding: dict[str, str] | None = None
    if active_v2:
        if args.limit:
            raise SystemExit("STOP: active-v2 embedding cannot use a pilot --limit")
        owner_index = load_cohort_preview_index(args.cohort_index)
        if owner_index.cohort_name != "location-training-v2" or len(owner_index.rows) != 20_000:
            raise SystemExit("STOP: active-v2 embedding requires the exact owner20k cohort")
        active_binding = load_active_v2_fresh_truth_binding(
            args.cohort_index,
            cohort_index=owner_index,
        )
        if __package__:
            from .calibrate_certify import load_fresh_certification_snapshot
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.calibrate_certify import (
                load_fresh_certification_snapshot,
            )

        fresh_snapshot = load_fresh_certification_snapshot(
            args.fresh_certification_approval,
            expected_selection_sha256=active_binding["fresh_truth_selection_sha256"],
            expected_approval_private_sha256=active_binding["fresh_truth_approval_private_sha256"],
            expected_approval_public_sha256=active_binding["fresh_truth_approval_public_sha256"],
            memory_guard=memory_guard,
        )
        owner_assets = [
            AssetInput(
                asset_id=row.asset_id,
                image_path=row.image_path,
                source_updated=row.source_updated,
                preview_sha256=row.preview_sha256,
            )
            for row in owner_index.rows
        ]
        assets = combine_active_v2_embedding_assets(owner_assets, fresh_snapshot.rows)
        training_asset_count = len(owner_assets)
        usable_truth_asset_count = len(fresh_snapshot.rows)
        missing_truth_images = 0
        excluded_truth_previews = 0
        fresh_lineage = {
            **active_binding,
            "certification_index_sha256": fresh_snapshot.certification_index_sha256,
            "fresh_inventory_sha256": fresh_snapshot.inventory_sha256,
            "image_set_sha256": fresh_snapshot.image_set_sha256,
            "pixel_snapshot_sha256": fresh_snapshot.pixel_snapshot_sha256,
            "selected_count": len(fresh_snapshot.rows),
        }
        del fresh_snapshot
    else:
        inventory = build_asset_inventories(
            preview_dir=args.preview_dir,
            truth_jsonl=args.truth_dir / "library_truth.jsonl",
            review_data_path=args.truth_dir / "review_data.json",
            timestamps_path=args.timestamps,
            raw_metadata_dir=args.raw_metadata_dir,
            cohort_index_path=args.cohort_index,
        )
        assets = inventory.embedding_assets[: args.limit or None]
        training_asset_count = len(inventory.label_assets)
        usable_truth_asset_count = len(inventory.embedding_assets) - len(inventory.label_assets)
        missing_truth_images = inventory.missing_truth_images
        excluded_truth_previews = inventory.excluded_truth_previews
    memory_guard()
    weights_sha256 = verify_onnx_artifact(args.onnx)
    spec = EncoderSpec(f"{MODEL_ID}@{MODEL_REVISION}", weights_sha256)
    cache = EmbeddingCache(cache_path)
    print(
        f"embedding inventory: {len(assets)} assets; "
        f"explicit truth exclusions={excluded_truth_previews}; "
        f"missing truth images={missing_truth_images}",
        flush=True,
    )
    session = create_onnx_session(args.onnx, args.provider)
    memory_guard()
    run = embed_assets(
        assets,
        spec=spec,
        cache=cache,
        session=session,
        batch_size=args.batch_size,
        memory_guard=memory_guard,
    )
    if active_v2:
        if active_binding is None or fresh_lineage is None:  # pragma: no cover - invariant
            raise AssertionError("active-v2 embedding lineage was not initialized")
        current_binding = load_active_v2_fresh_truth_binding(args.cohort_index)
        current_fresh = load_fresh_certification_snapshot(
            args.fresh_certification_approval,
            expected_selection_sha256=active_binding["fresh_truth_selection_sha256"],
            expected_approval_private_sha256=active_binding["fresh_truth_approval_private_sha256"],
            expected_approval_public_sha256=active_binding["fresh_truth_approval_public_sha256"],
            memory_guard=memory_guard,
        )
        current_fresh_lineage = {
            **current_binding,
            "certification_index_sha256": current_fresh.certification_index_sha256,
            "fresh_inventory_sha256": current_fresh.inventory_sha256,
            "image_set_sha256": current_fresh.image_set_sha256,
            "pixel_snapshot_sha256": current_fresh.pixel_snapshot_sha256,
            "selected_count": len(current_fresh.rows),
        }
        del current_fresh
        if current_fresh_lineage != fresh_lineage:
            raise SystemExit("STOP: active-v2 owner or fresh400 evidence changed during embedding")
    peak_rss_bytes = memory_guard()
    metadata = {
        "schema_version": "triage-embed-run-v1",
        "encoder_id": spec.encoder_id,
        "weights_sha256": spec.weights_sha256,
        "encoder_key": spec.key,
        "staging_encoder_key": spec.key,
        "preprocess_version": spec.preprocess_version,
        "layout_version": spec.layout_version,
        "provider_requested": args.provider,
        "providers_active": session.get_providers(),
        "batch_size": args.batch_size,
        "max_process_working_set_gib": args.max_working_set_gib,
        "peak_process_rss_bytes": peak_rss_bytes,
        "run": asdict(run),
        "inventory": {
            "training_assets": training_asset_count,
            "usable_truth_assets": usable_truth_asset_count,
            "missing_truth_images": missing_truth_images,
            "excluded_truth_previews": excluded_truth_previews,
        },
    }
    if fresh_lineage is not None:
        metadata["fresh_certification"] = fresh_lineage
    _write_json(args.artifact_dir / f"embed-meta-{args.provider}.json", metadata)
    print(
        f"computed={run.computed} cached={run.cached} "
        f"{run.ms_per_computed_image:.2f} ms/image ({args.provider})",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
