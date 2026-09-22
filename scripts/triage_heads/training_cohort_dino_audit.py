"""Seal a label-blind DINO diversity audit for owner-training cohort v2.

This is review evidence, not a training feature source.  It authenticates the
exact pinned v2 pixels, computes one frozen semantic image vector per preview,
finds the exact global nearest pairs, and renders only opaque-id review sheets.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import math
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from . import training_cohort as _v1
from .data import CohortPreviewIndex, IndexedPreview, load_cohort_preview_index
from .embed import (
    EXPECTED_ONNX_SHA256,
    MODEL_ID,
    MODEL_REVISION,
    PREPROCESS_VERSION,
    TOKEN_DIM,
    _token_output,
    create_onnx_session,
    pool_token_pack,
    preprocess_image_bytes,
    verify_onnx_artifact,
)
from .memory import (
    ensure_offline_process_memory,
    validate_offline_working_set_gib,
)
from .training_cohort_v2 import (
    _canonical_bytes,
    _canonical_sha256,
    _load_private_json,
    _load_visual_review_context,
    _read_private_bytes,
    _verify_self_digest,
    _write_create_only,
)

DINO_AUDIT_SCHEMA: Final = "triage-private-owner-training-dino-diversity-audit-v1"
DINO_AUDIT_PURPOSE: Final = (
    "label-blind diversity review only; forbidden as training or certification evidence"
)
DINO_AUDIT_ALGORITHM: Final = "exact-normalized-float64-blockwise-top-32-v1"
DINO_AUDIT_POOLING_VERSION: Final = "dinov2-six-pool-unit-mean-l2-384-v1"
DINO_AUDIT_ROOT_SUFFIX: Final = "-dino-audit"
DINO_AUDIT_MANIFEST_FILENAME: Final = "audit-manifest.json"
DINO_NEAREST_PAIRS_FILENAME: Final = "nearest-pairs.json"
DINO_AUDIT_MANIFEST_DOMAIN: Final = b"triage-owner-training-dino-audit-v1\0"
DINO_NEAREST_PAIRS_DOMAIN: Final = b"triage-owner-training-dino-nearest-pairs-v1\0"
DINO_ENCODER_DOMAIN: Final = b"triage-owner-training-dino-encoder-v1\0"
DINO_PIXEL_SET_DOMAIN: Final = b"triage-owner-training-dino-pixel-set-v1\0"
DINO_VECTOR_SET_DOMAIN: Final = b"triage-owner-training-dino-vector-set-v1\0"
DINO_AUDIT_VECTOR_DIM: Final = TOKEN_DIM
DINO_AUDIT_OWNER_COUNT: Final = 20_000
DINO_AUDIT_PAIR_COUNT: Final = 32
DINO_AUDIT_BLOCK_ROWS: Final = 512
DINO_AUDIT_PROCESS_CAP_BYTES: Final = 8 * 1024**3
DINO_AUDIT_GLOBAL_CEILING_BYTES: Final = 64 * 1024**3


@dataclass(frozen=True, slots=True)
class DinoNearestPair:
    rank: int
    left_audit_id: str
    right_audit_id: str
    similarity: float


@dataclass(frozen=True, slots=True)
class DinoAuditContext:
    cohort_run_sha256: str
    final_selection_sha256: str
    final_preview_manifest_sha256: str
    private_index_sha256: str
    pixel_set_sha256: str
    encoder_sha256: str
    vector_count: int
    vector_set_sha256: str
    nearest_pair_count: int
    nearest_pairs_sha256: str
    max_nearest_similarity: float
    audit_manifest_sha256: str
    reviewed_pages: dict[str, list[dict[str, object]]]


@dataclass(frozen=True, slots=True)
class _AuthenticatedDinoSource:
    cohort_root: Path
    index: CohortPreviewIndex
    raw_context: dict[str, object]
    pixel_set_sha256: str


def default_dino_audit_root(cohort_root: Path) -> Path:
    root = Path(cohort_root)
    return root.with_name(root.name + DINO_AUDIT_ROOT_SUFFIX)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_private_directory(path: Path, *, label: str) -> None:
    candidate = Path(path)
    if candidate.is_symlink():
        raise PermissionError(f"{label} must be a private mode-0700 directory")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as error:
        raise PermissionError(f"{label} must be a private mode-0700 directory") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PermissionError(f"{label} must be a private mode-0700 directory")


def _encoder_record() -> dict[str, object]:
    encoder: dict[str, object] = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "encoder_id": f"{MODEL_ID}@{MODEL_REVISION}",
        "weights_sha256": EXPECTED_ONNX_SHA256,
        "preprocess_version": PREPROCESS_VERSION,
        "pooling_version": DINO_AUDIT_POOLING_VERSION,
    }
    encoder["encoder_sha256"] = _canonical_sha256(DINO_ENCODER_DOMAIN, encoder)
    return encoder


def pool_dino_audit_vectors(tokens: np.ndarray) -> np.ndarray:
    """Freeze six DINO pools into one crop-tolerant 384-D audit vector."""
    packs = pool_token_pack(tokens)
    pools = np.asarray(packs, dtype=np.float64).reshape(-1, 6, TOKEN_DIM)
    pool_norms = np.linalg.norm(pools, axis=2, keepdims=True)
    if np.any(~np.isfinite(pools)) or np.any(pool_norms <= 0.0):
        raise ValueError("DINO audit pools must be finite and non-zero")
    averaged = (pools / pool_norms).mean(axis=1)
    norms = np.linalg.norm(averaged, axis=1, keepdims=True)
    if np.any(~np.isfinite(averaged)) or np.any(norms <= 0.0):
        raise ValueError("DINO audit vectors must be finite and non-zero")
    return np.ascontiguousarray(averaged / norms, dtype=np.float32)


def _normalized_float64(vectors: np.ndarray) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] < 2 or array.shape[1] < 1:
        raise ValueError("DINO nearest audit requires at least two vectors")
    if not np.all(np.isfinite(array)):
        raise ValueError("DINO nearest vectors must be finite")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms <= 0.0):
        raise ValueError("DINO nearest vectors must be non-zero")
    return np.ascontiguousarray(array / norms, dtype=np.float64)


def exact_global_dino_pairs(
    vectors: np.ndarray,
    audit_ids: Sequence[str],
) -> tuple[DinoNearestPair, ...]:
    """Return exact global top-32 cosine edges with bounded matrix memory."""
    normalized = _normalized_float64(vectors)
    ids = tuple(str(value) for value in audit_ids)
    if len(ids) != len(normalized) or len(set(ids)) != len(ids) or any(not value for value in ids):
        raise ValueError("DINO audit ids must be non-empty and unique")
    if len(ids) * (len(ids) - 1) // 2 < DINO_AUDIT_PAIR_COUNT:
        raise ValueError("DINO nearest audit requires at least 32 distinct pairs")

    order = tuple(sorted(range(len(ids)), key=ids.__getitem__))
    ordered_ids = tuple(ids[index] for index in order)
    normalized = np.ascontiguousarray(normalized[np.asarray(order, dtype=np.intp)])
    candidates: list[tuple[float, str, str]] = []
    for left_start in range(0, len(ids), DINO_AUDIT_BLOCK_ROWS):
        left_stop = min(left_start + DINO_AUDIT_BLOCK_ROWS, len(ids))
        left_block = normalized[left_start:left_stop]
        for right_start in range(left_start, len(ids), DINO_AUDIT_BLOCK_ROWS):
            right_stop = min(right_start + DINO_AUDIT_BLOCK_ROWS, len(ids))
            similarities = left_block @ normalized[right_start:right_stop].T
            if left_start == right_start:
                local_left, local_right = np.triu_indices(left_stop - left_start, k=1)
                scores = similarities[local_left, local_right]
            else:
                scores = similarities.reshape(-1)
                flat = np.arange(len(scores), dtype=np.intp)
                width = right_stop - right_start
                local_left = flat // width
                local_right = flat % width
            if not len(scores):
                continue
            keep = min(DINO_AUDIT_PAIR_COUNT, len(scores))
            if len(scores) == keep:
                selected = np.arange(len(scores), dtype=np.intp)
            else:
                threshold = np.partition(scores, len(scores) - keep)[len(scores) - keep]
                better = np.flatnonzero(scores > threshold)
                tied = np.flatnonzero(scores == threshold)
                selected = np.concatenate((better, tied[: keep - len(better)]))
            for position in selected:
                left = left_start + int(local_left[position])
                right = right_start + int(local_right[position])
                candidates.append((float(scores[position]), ordered_ids[left], ordered_ids[right]))

    ranked = candidates
    ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
    return tuple(
        DinoNearestPair(
            rank=rank,
            left_audit_id=left_id,
            right_audit_id=right_id,
            similarity=similarity,
        )
        for rank, (similarity, left_id, right_id) in enumerate(
            ranked[:DINO_AUDIT_PAIR_COUNT], start=1
        )
    )


def _pixel_set_sha256(rows: Sequence[IndexedPreview]) -> str:
    return _canonical_sha256(
        DINO_PIXEL_SET_DOMAIN,
        {
            "schema": "triage-owner-training-dino-pixel-set-v1",
            "rows": [
                {
                    "audit_id": row.audit_id,
                    "preview_sha256": row.preview_sha256,
                }
                for row in sorted(rows, key=lambda item: item.audit_id)
            ],
        },
    )


def _vector_set_sha256(
    rows: Sequence[IndexedPreview],
    vectors: np.ndarray,
) -> str:
    ordered = tuple(sorted(rows, key=lambda item: item.audit_id))
    if len(ordered) != len(vectors):
        raise ValueError("DINO vector set does not cover the exact selected pixels")
    return _canonical_sha256(
        DINO_VECTOR_SET_DOMAIN,
        {
            "schema": "triage-owner-training-dino-vector-set-v1",
            "encoder_sha256": _encoder_record()["encoder_sha256"],
            "rows": [
                {
                    "audit_id": row.audit_id,
                    "preview_sha256": row.preview_sha256,
                    "vector_sha256": hashlib.sha256(
                        np.asarray(vector, dtype=np.float32).tobytes()
                    ).hexdigest(),
                }
                for row, vector in zip(ordered, vectors, strict=True)
            ],
        },
    )


def _raw_context_dict(context: Any) -> dict[str, object]:
    return {
        "cohort_run_sha256": context.cohort_run_sha256,
        "final_selection_sha256": context.final_selection_sha256,
        "final_preview_manifest_sha256": context.final_preview_manifest_sha256,
        "final_preview_count": context.final_preview_count,
    }


def _authenticate_source(cohort_root: Path) -> _AuthenticatedDinoSource:
    root = Path(cohort_root)
    raw = _load_visual_review_context(root)
    index_path = root / "training-previews-v2" / "private-index.json"
    # This replays the committed preview-store verifier, including every pixel.
    from .embed import load_active_v2_fresh_truth_binding

    binding = load_active_v2_fresh_truth_binding(index_path)
    index = load_cohort_preview_index(index_path)
    if (
        index.cohort_name != "location-training-v2"
        or len(index.rows) != DINO_AUDIT_OWNER_COUNT
        or index.selection_sha256 != raw.final_selection_sha256
        or index.manifest_sha256 != raw.final_preview_manifest_sha256
        or binding.get("owner_cohort_run_sha256") != raw.cohort_run_sha256
    ):
        raise RuntimeError("DINO audit source is not the exact owner v2 cohort")
    return _AuthenticatedDinoSource(
        cohort_root=root,
        index=index,
        raw_context=_raw_context_dict(raw),
        pixel_set_sha256=_pixel_set_sha256(index.rows),
    )


def _validate_pair_rows(
    payload: Mapping[str, object],
    *,
    vector_set_sha256: str,
) -> tuple[tuple[DinoNearestPair, ...], str]:
    expected_keys = {
        "schema",
        "algorithm",
        "vector_set_sha256",
        "rows",
        "nearest_pairs_sha256",
    }
    if set(payload) != expected_keys:
        raise RuntimeError("DINO nearest-pair evidence has the wrong schema")
    digest = _verify_self_digest(
        dict(payload),
        field="nearest_pairs_sha256",
        domain=DINO_NEAREST_PAIRS_DOMAIN,
        label="DINO nearest-pair evidence",
    )
    raw_rows = payload.get("rows")
    if (
        payload.get("schema") != "triage-private-owner-training-dino-nearest-pairs-v1"
        or payload.get("algorithm") != DINO_AUDIT_ALGORITHM
        or payload.get("vector_set_sha256") != vector_set_sha256
        or not isinstance(raw_rows, list)
        or len(raw_rows) != DINO_AUDIT_PAIR_COUNT
    ):
        raise RuntimeError("DINO nearest-pair evidence is malformed")
    pairs: list[DinoNearestPair] = []
    seen: set[tuple[str, str]] = set()
    for rank, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict) or set(row) != {
            "rank",
            "left_audit_id",
            "right_audit_id",
            "similarity",
        }:
            raise RuntimeError("DINO nearest-pair row is malformed")
        left = row.get("left_audit_id")
        right = row.get("right_audit_id")
        similarity = row.get("similarity")
        if (
            row.get("rank") != rank
            or not isinstance(left, str)
            or not isinstance(right, str)
            or not left.startswith("T")
            or not right.startswith("T")
            or left >= right
            or (left, right) in seen
            or not isinstance(similarity, (int, float))
            or isinstance(similarity, bool)
            or not math.isfinite(float(similarity))
            or not -1.0 <= float(similarity) <= 1.0 + 1e-12
        ):
            raise RuntimeError("DINO nearest-pair row is malformed")
        seen.add((left, right))
        pairs.append(DinoNearestPair(rank, left, right, float(similarity)))
    expected_order = sorted(
        pairs,
        key=lambda pair: (-pair.similarity, pair.left_audit_id, pair.right_audit_id),
    )
    if pairs != expected_order:
        raise RuntimeError("DINO nearest-pair rows are not globally ranked")
    return tuple(pairs), digest


def load_dino_diversity_audit(
    audit_root: Path,
    *,
    expected_raw_context: Mapping[str, object],
) -> DinoAuditContext:
    """Authenticate the sealed DINO audit and every human-review page."""
    root = Path(audit_root)
    _require_private_directory(root, label="DINO audit root")
    manifest, _manifest_bytes = _load_private_json(
        root / DINO_AUDIT_MANIFEST_FILENAME,
        label="DINO audit manifest",
    )
    expected_keys = {
        "schema",
        "status",
        "purpose",
        "cohort_run_sha256",
        "final_selection_sha256",
        "final_preview_manifest_sha256",
        "final_preview_count",
        "private_index_sha256",
        "pixel_set_sha256",
        "encoder",
        "vector_count",
        "vector_dim",
        "vector_set_sha256",
        "global_nearest",
        "groups",
        "memory_provenance",
        "audit_manifest_sha256",
    }
    if set(manifest) != expected_keys or manifest.get("schema") != DINO_AUDIT_SCHEMA:
        raise RuntimeError("DINO audit manifest has the wrong schema")
    audit_manifest_sha256 = _verify_self_digest(
        manifest,
        field="audit_manifest_sha256",
        domain=DINO_AUDIT_MANIFEST_DOMAIN,
        label="DINO audit manifest",
    )
    raw_fields = {
        "cohort_run_sha256",
        "final_selection_sha256",
        "final_preview_manifest_sha256",
        "final_preview_count",
    }
    if set(expected_raw_context) != raw_fields or any(
        manifest.get(field) != expected_raw_context.get(field) for field in raw_fields
    ):
        raise RuntimeError("DINO audit source lineage differs from the raw review")
    count = manifest.get("vector_count")
    if (
        manifest.get("status") != "awaiting_visual_review"
        or manifest.get("purpose") != DINO_AUDIT_PURPOSE
        or type(count) is not int
        or count < 2
        or count != manifest.get("final_preview_count")
        or manifest.get("vector_dim") != DINO_AUDIT_VECTOR_DIM
        or not all(
            _is_sha256(manifest.get(field))
            for field in (
                "private_index_sha256",
                "pixel_set_sha256",
                "vector_set_sha256",
            )
        )
    ):
        raise RuntimeError("DINO audit manifest is malformed")
    encoder = manifest.get("encoder")
    if not isinstance(encoder, dict) or encoder != _encoder_record():
        raise RuntimeError("DINO audit encoder identity differs from the pinned graph")

    pairs_payload, _pairs_bytes = _load_private_json(
        root / DINO_NEAREST_PAIRS_FILENAME,
        label="DINO nearest-pair evidence",
    )
    pairs, pairs_sha256 = _validate_pair_rows(
        pairs_payload,
        vector_set_sha256=str(manifest["vector_set_sha256"]),
    )
    nearest = manifest.get("global_nearest")
    if not isinstance(nearest, dict) or set(nearest) != {
        "algorithm",
        "pair_count",
        "max_nearest_similarity",
        "nearest_pairs_sha256",
    }:
        raise RuntimeError("DINO global-nearest evidence is malformed")
    maximum = nearest.get("max_nearest_similarity")
    if (
        nearest.get("algorithm") != DINO_AUDIT_ALGORITHM
        or nearest.get("pair_count") != DINO_AUDIT_PAIR_COUNT
        or len(pairs) != DINO_AUDIT_PAIR_COUNT
        or nearest.get("nearest_pairs_sha256") != pairs_sha256
        or not isinstance(maximum, (int, float))
        or isinstance(maximum, bool)
        or not math.isfinite(float(maximum))
        or abs(float(maximum) - pairs[0].similarity) > 1e-12
    ):
        raise RuntimeError("DINO global-nearest evidence does not reproduce")

    groups = manifest.get("groups")
    if not isinstance(groups, dict) or set(groups) != {"nearest"}:
        raise RuntimeError("DINO audit must contain the nearest review sheets")
    raw_pages = groups["nearest"]
    if not isinstance(raw_pages, list) or not raw_pages:
        raise RuntimeError("DINO nearest review group is empty")
    reviewed_pages: dict[str, list[dict[str, object]]] = {"nearest": []}
    expected_files = {DINO_AUDIT_MANIFEST_FILENAME, DINO_NEAREST_PAIRS_FILENAME}
    reviewed_tile_count = 0
    for page_number, page in enumerate(raw_pages, start=1):
        if (
            not isinstance(page, dict)
            or set(page) != {"page", "tile_count", "sha256"}
            or page.get("page") != page_number
            or type(page.get("tile_count")) is not int
            or int(page["tile_count"]) < 1
            or not _is_sha256(page.get("sha256"))
        ):
            raise RuntimeError("DINO nearest review page evidence is malformed")
        filename = f"nearest-{page_number:02d}.png"
        page_path = root / filename
        page_bytes = _read_private_bytes(
            page_path,
            label="DINO nearest review page",
        )
        if hashlib.sha256(page_bytes).hexdigest() != page["sha256"]:
            raise RuntimeError("DINO nearest review page digest does not reproduce")
        reviewed_tile_count += int(page["tile_count"])
        expected_files.add(filename)
        reviewed_pages["nearest"].append({"page": page_number, "sha256": page["sha256"]})
    if reviewed_tile_count != 2 * DINO_AUDIT_PAIR_COUNT:
        raise RuntimeError("DINO nearest review pages do not expose every sealed pair")
    if {path.name for path in root.iterdir()} != expected_files:
        raise RuntimeError("DINO audit root has unexpected or missing files")

    memory = manifest.get("memory_provenance")
    if not isinstance(memory, dict) or set(memory) != {
        "process_cap_bytes",
        "peak_process_rss_bytes",
        "global_ceiling_bytes",
    }:
        raise RuntimeError("DINO audit memory provenance is malformed")
    cap = memory.get("process_cap_bytes")
    peak = memory.get("peak_process_rss_bytes")
    if (
        type(cap) is not int
        or type(peak) is not int
        or cap < 1
        or cap > DINO_AUDIT_PROCESS_CAP_BYTES
        or peak < 1
        or peak > cap
        or memory.get("global_ceiling_bytes") != DINO_AUDIT_GLOBAL_CEILING_BYTES
    ):
        raise RuntimeError("DINO audit memory provenance violates its hard bounds")
    return DinoAuditContext(
        cohort_run_sha256=str(manifest["cohort_run_sha256"]),
        final_selection_sha256=str(manifest["final_selection_sha256"]),
        final_preview_manifest_sha256=str(manifest["final_preview_manifest_sha256"]),
        private_index_sha256=str(manifest["private_index_sha256"]),
        pixel_set_sha256=str(manifest["pixel_set_sha256"]),
        encoder_sha256=str(encoder["encoder_sha256"]),
        vector_count=int(count),
        vector_set_sha256=str(manifest["vector_set_sha256"]),
        nearest_pair_count=len(pairs),
        nearest_pairs_sha256=pairs_sha256,
        max_nearest_similarity=float(maximum),
        audit_manifest_sha256=audit_manifest_sha256,
        reviewed_pages=reviewed_pages,
    )


def _pair_payload(
    pairs: Sequence[DinoNearestPair],
    *,
    vector_set_sha256: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "triage-private-owner-training-dino-nearest-pairs-v1",
        "algorithm": DINO_AUDIT_ALGORITHM,
        "vector_set_sha256": vector_set_sha256,
        "rows": [
            {
                "rank": pair.rank,
                "left_audit_id": pair.left_audit_id,
                "right_audit_id": pair.right_audit_id,
                "similarity": pair.similarity,
            }
            for pair in pairs
        ],
    }
    payload["nearest_pairs_sha256"] = _canonical_sha256(
        DINO_NEAREST_PAIRS_DOMAIN,
        payload,
    )
    return payload


def _render_nearest_pages(
    *,
    output_dir: Path,
    image_paths: Mapping[str, Path],
    pairs: Sequence[DinoNearestPair],
    columns: int,
    review_rows: int,
) -> list[dict[str, object]]:
    entries: list[tuple[Path, str]] = []
    for pair in pairs:
        entries.extend(
            (
                (
                    image_paths[pair.left_audit_id],
                    f"DINO {pair.rank:02d}A {pair.similarity:.6f} | {pair.left_audit_id}",
                ),
                (
                    image_paths[pair.right_audit_id],
                    f"DINO {pair.rank:02d}B {pair.similarity:.6f} | {pair.right_audit_id}",
                ),
            )
        )
    return _v1._render_pages(  # noqa: SLF001 - shared blinded sheet renderer
        entries,
        output_dir=output_dir,
        stem="nearest",
        columns=columns,
        rows=review_rows,
    )


def _same_source(left: _AuthenticatedDinoSource, right: _AuthenticatedDinoSource) -> bool:
    return (
        left.raw_context == right.raw_context
        and left.pixel_set_sha256 == right.pixel_set_sha256
        and left.index.private_index_sha256 == right.index.private_index_sha256
        and left.index.manifest_sha256 == right.index.manifest_sha256
    )


def _publish_directory_noreplace(staging: Path, destination: Path) -> None:
    """Atomically publish one directory without ever replacing a destination."""
    source_bytes = os.fsencode(staging)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:  # pragma: no cover - fail closed on unsupported Darwin
            raise RuntimeError("exclusive DINO audit publish is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:  # pragma: no cover - fail closed on old libc/kernel
            raise RuntimeError("exclusive DINO audit publish is unavailable")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, destination_bytes, 1)
    else:  # pragma: no cover - production is pinned to macOS/Linux
        raise RuntimeError("exclusive DINO audit publish is unavailable")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise RuntimeError("DINO audit output appeared before atomic publish")
    raise OSError(
        error_number,
        f"exclusive DINO audit publish failed: {os.strerror(error_number)}",
        destination,
    )


def build_dino_diversity_audit(
    *,
    cohort_root: Path,
    output_dir: Path | None = None,
    onnx_path: Path,
    provider: str = "cpu",
    batch_size: int = 32,
    max_working_set_gib: float = 8.0,
    review_columns: int = 4,
    review_rows: int = 4,
    session_factory: Callable[[Path, str], Any] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> DinoAuditContext:
    """Run and atomically seal the DINO review lane for exact owner20k pixels."""
    if provider != "cpu":
        raise ValueError("owner DINO diversity audit is pinned to CPU inference")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("DINO audit batch size must be positive")
    if review_columns < 1 or review_rows < 1:
        raise ValueError("DINO review dimensions must be positive")
    limit_gib = validate_offline_working_set_gib(max_working_set_gib)
    limit_bytes = int(limit_gib * 1024**3)

    def memory_guard() -> int:
        return ensure_offline_process_memory(max_working_set_gib=limit_gib)

    memory_guard()
    source = _authenticate_source(Path(cohort_root))
    target = (
        Path(output_dir) if output_dir is not None else default_dino_audit_root(source.cohort_root)
    )
    if target.exists() or target.is_symlink():
        raise RuntimeError("DINO audit output already exists")
    if target.parent.resolve() != source.cohort_root.parent.resolve():
        raise RuntimeError("DINO audit must be a sibling of its owner v2 cohort")
    weights_sha256 = verify_onnx_artifact(Path(onnx_path))
    if weights_sha256 != EXPECTED_ONNX_SHA256:  # pragma: no cover - pinned verifier invariant
        raise RuntimeError("DINO audit ONNX identity did not reproduce")
    factory = session_factory or create_onnx_session
    session = factory(Path(onnx_path), provider)
    input_name = session.get_inputs()[0].name
    ordered = tuple(sorted(source.index.rows, key=lambda row: row.audit_id))
    vectors = np.empty((len(ordered), DINO_AUDIT_VECTOR_DIM), dtype=np.float32)
    peak_rss_bytes = memory_guard()
    for start in range(0, len(ordered), batch_size):
        chunk = ordered[start : start + batch_size]
        payloads: list[bytes] = []
        for row in chunk:
            payload = _read_private_bytes(
                row.image_path,
                label=f"DINO audit source pixel {row.audit_id}",
            )
            if hashlib.sha256(payload).hexdigest() != row.preview_sha256:
                raise RuntimeError("DINO audit source pixel changed during inference")
            payloads.append(payload)
        batch = np.stack([preprocess_image_bytes(payload) for payload in payloads])
        tokens = _token_output(session.run(None, {input_name: batch}))
        vectors[start : start + len(chunk)] = pool_dino_audit_vectors(tokens)
        peak_rss_bytes = max(peak_rss_bytes, memory_guard())
        if progress is not None:
            progress(start + len(chunk), len(ordered))
    vector_set_sha256 = _vector_set_sha256(ordered, vectors)
    pairs = exact_global_dino_pairs(vectors, [row.audit_id for row in ordered])
    if len(pairs) != DINO_AUDIT_PAIR_COUNT:
        raise RuntimeError("DINO audit did not produce the exact global top-32")
    peak_rss_bytes = max(peak_rss_bytes, memory_guard())
    pair_payload = _pair_payload(pairs, vector_set_sha256=vector_set_sha256)

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.staging-",
        dir=target.parent,
    ) as temporary_name:
        staging = Path(temporary_name)
        os.chmod(staging, 0o700)
        _write_create_only(
            staging / DINO_NEAREST_PAIRS_FILENAME,
            _canonical_bytes(pair_payload),
        )
        rows_by_id = {row.audit_id: row for row in ordered}
        review_ids = sorted(
            {audit_id for pair in pairs for audit_id in (pair.left_audit_id, pair.right_audit_id)}
        )
        with tempfile.TemporaryDirectory(
            prefix=f".{target.name}.render-input-",
            dir=target.parent,
        ) as render_input_name:
            render_input = Path(render_input_name)
            os.chmod(render_input, 0o700)
            render_paths: dict[str, Path] = {}
            for audit_id in review_ids:
                row = rows_by_id[audit_id]
                payload = _read_private_bytes(
                    row.image_path,
                    label=f"DINO review source pixel {audit_id}",
                )
                if hashlib.sha256(payload).hexdigest() != row.preview_sha256:
                    raise RuntimeError("DINO review source pixel changed before rendering")
                snapshot_path = render_input / f"{audit_id}.image"
                _write_create_only(snapshot_path, payload)
                render_paths[audit_id] = snapshot_path
            pages = _render_nearest_pages(
                output_dir=staging,
                image_paths=render_paths,
                pairs=pairs,
                columns=review_columns,
                review_rows=review_rows,
            )
        peak_rss_bytes = max(peak_rss_bytes, memory_guard())
        manifest: dict[str, object] = {
            "schema": DINO_AUDIT_SCHEMA,
            "status": "awaiting_visual_review",
            "purpose": DINO_AUDIT_PURPOSE,
            **source.raw_context,
            "private_index_sha256": source.index.private_index_sha256,
            "pixel_set_sha256": source.pixel_set_sha256,
            "encoder": _encoder_record(),
            "vector_count": len(ordered),
            "vector_dim": DINO_AUDIT_VECTOR_DIM,
            "vector_set_sha256": vector_set_sha256,
            "global_nearest": {
                "algorithm": DINO_AUDIT_ALGORITHM,
                "pair_count": len(pairs),
                "max_nearest_similarity": pairs[0].similarity,
                "nearest_pairs_sha256": pair_payload["nearest_pairs_sha256"],
            },
            "groups": {"nearest": pages},
            "memory_provenance": {
                "process_cap_bytes": limit_bytes,
                "peak_process_rss_bytes": peak_rss_bytes,
                "global_ceiling_bytes": DINO_AUDIT_GLOBAL_CEILING_BYTES,
            },
        }
        manifest["audit_manifest_sha256"] = _canonical_sha256(
            DINO_AUDIT_MANIFEST_DOMAIN,
            manifest,
        )
        _write_create_only(
            staging / DINO_AUDIT_MANIFEST_FILENAME,
            _canonical_bytes(manifest),
        )
        staged = load_dino_diversity_audit(
            staging,
            expected_raw_context=source.raw_context,
        )
        current = _authenticate_source(source.cohort_root)
        if not _same_source(source, current):
            raise RuntimeError("owner v2 source changed before DINO audit publish")
        if target.exists() or target.is_symlink():
            raise RuntimeError("DINO audit output appeared before atomic publish")
        _publish_directory_noreplace(staging, target)
        _v1._fsync_directory(target.parent)  # noqa: SLF001
        return staged
