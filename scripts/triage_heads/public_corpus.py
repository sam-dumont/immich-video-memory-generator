#!/usr/bin/env python3
"""Verify and partition the immutable Open Images triage corpus.

The source corpus is read-only.  Certification is reserved before training by
using only raw pixels and non-semantic file metadata; labels, titles, authors,
and model output never participate in selection.  The emitted public files
retain the complete CC BY attribution chain while absolute local paths stay in
the private manifest.

    uv run python -m scripts.triage_heads.public_corpus \
      --output /path/to/versioned/triage-public-v1

An optional, separately downloaded 400-row Open Images manifest may be added
later with ``--top-up-manifest``, ``--top-up-images``, and its exact manifest
SHA-256.  Those rows extend training only; they cannot change certification.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import resource
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError
from scripts.triage_heads.image_diversity import (
    DESCRIPTOR_VERSION,
    PixelDescriptor,
    cluster_descriptors,
    describe_preview_bytes,
    selected_cluster_metrics,
)
from scripts.triage_heads.memory import acquire_recovery_pipeline_lock

SOURCE_SCHEMA = "open-images-pull-corpus-manifest-v1"
PARTITION_SCHEMA = "triage-open-images-partition-v1"
EXPECTED_SOURCE_MANIFEST_SHA256 = "20df04dcba5286128751d55fb8a34e609d6da674eab65e3db3886a3fd40e4143"
DEFAULT_SOURCE_ROOT = Path.home() / ".immich-memories-distill" / "validation"
CC_BY_2_0 = "https://creativecommons.org/licenses/by/2.0/"
LICENSE_NAME = "CC BY 2.0"
CVDF_IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{split}/{image_id}.jpg"
MAX_MEMORY_BYTES = 8 * 1024**3
MAX_IMAGE_BYTES = 64 * 1024**2
MAX_IMAGE_PIXELS = 100_000_000
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_MANIFEST_DECODED_BYTES = 256 * 1024**2
MAX_SOURCE_FIELD_BYTES = 16 * 1024
DEFAULT_SEED = "triage-public-certification-v1"

SOURCE_COLUMNS = (
    "image_id",
    "split",
    "s3_url",
    "local_path",
    "license_name",
    "license_url",
    "author",
    "author_profile_url",
    "original_landing_url",
    "title",
    "retrieved_at",
    "content_sha256",
    "bytes",
)

PUBLIC_ATTRIBUTION_COLUMNS = (
    "image_id",
    "role",
    "source_corpus",
    "split",
    "s3_url",
    "license_name",
    "license_url",
    "author",
    "author_profile_url",
    "original_landing_url",
    "title",
    "retrieved_at",
    "content_sha256",
    "bytes",
)


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    """Fixed corpus arithmetic and label-blind selection parameters."""

    source_count: int = 3_000
    certification_count: int = 400
    architecture_training_count: int = 3_000
    top_up_count: int = 400
    visual_clusters: int = 64

    def __post_init__(self) -> None:
        if self.source_count < 2:
            raise ValueError("source_count must be at least two")
        if not 1 <= self.certification_count < self.source_count:
            raise ValueError("certification_count must fit inside source_count")
        if self.architecture_training_count < 1 or self.top_up_count < 1:
            raise ValueError("training and top-up counts must be positive")
        if not 2 <= self.visual_clusters <= self.certification_count:
            raise ValueError("visual_clusters must be between two and certification_count")


@dataclass(frozen=True, slots=True)
class VerifiedRow:
    """One verified manifest row plus its label-blind image measurements."""

    source: Mapping[str, Any]
    source_corpus: str
    local_path: Path
    width: int
    height: int
    descriptor: PixelDescriptor

    @property
    def image_id(self) -> str:
        return str(self.source["image_id"])

    @property
    def content_sha256(self) -> str:
        return str(self.source["content_sha256"])

    def selection_vector(self) -> np.ndarray:
        """Pixel descriptor plus two raw, non-semantic scale measurements."""
        metadata = np.asarray(
            [
                math.log1p(int(self.source["bytes"])),
                math.log1p(self.width * self.height),
            ],
            dtype=np.float32,
        )
        return np.concatenate((self.descriptor.vector, metadata)).astype(np.float32, copy=False)


@dataclass(frozen=True, slots=True)
class VerifiedCorpus:
    rows: tuple[VerifiedRow, ...]
    manifest_path: Path
    images_root: Path
    manifest_sha256: str
    logical_sha256: str
    source_corpus: str


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


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if os.uname().sysname == "Darwin" else value * 1024


def _assert_memory_limit() -> None:
    observed = _peak_rss_bytes()
    if observed >= MAX_MEMORY_BYTES:
        raise MemoryError(
            f"public-corpus process reached {observed / 1024**3:.2f} GiB; the hard limit is 8 GiB"
        )


def _read_parquet(payload: bytes, *, expected_count: int) -> tuple[dict[str, Any], ...]:
    import pyarrow.parquet  # deferred so --help and pure helpers stay cheap

    source = pyarrow.BufferReader(payload)
    parquet = pyarrow.parquet.ParquetFile(source)
    metadata = parquet.metadata
    if metadata.num_rows != expected_count:
        raise ValueError(f"source manifest has {metadata.num_rows} rows; expected {expected_count}")
    decoded_bytes = sum(
        metadata.row_group(group).column(column).total_uncompressed_size
        for group in range(metadata.num_row_groups)
        for column in range(metadata.row_group(group).num_columns)
    )
    if decoded_bytes > MAX_MANIFEST_DECODED_BYTES:
        raise ValueError("source manifest exceeds the bounded decoded-size limit")
    table = parquet.read(columns=list(SOURCE_COLUMNS))
    if table.nbytes > MAX_MANIFEST_DECODED_BYTES:
        raise ValueError("source manifest decoded table exceeds its memory limit")
    rows = tuple(table.to_pylist())
    for row in rows:
        for column in SOURCE_COLUMNS:
            value = row.get(column)
            if isinstance(value, str) and len(value.encode("utf-8")) > MAX_SOURCE_FIELD_BYTES:
                raise ValueError(f"source manifest field is unbounded: {column}")
    return rows


def _normalizes_to_cc_by_two(value: object) -> bool:
    return str(value or "").strip().rstrip("/") == CC_BY_2_0.rstrip("/")


def _require_nonempty(row: Mapping[str, Any], fields: Sequence[str], *, image_id: str) -> None:
    missing = [field for field in fields if not str(row.get(field) or "").strip()]
    if missing:
        raise ValueError(f"{image_id}: blank required attribution fields: {', '.join(missing)}")


def _verified_image(
    row: Mapping[str, Any],
    *,
    images_root: Path,
    source_corpus: str,
) -> VerifiedRow:
    image_id = str(row.get("image_id") or "").strip()
    if len(image_id) != 16 or any(character not in "0123456789abcdef" for character in image_id):
        raise ValueError(f"invalid Open Images image_id: {image_id!r}")
    _require_nonempty(
        row,
        (
            "author",
            "author_profile_url",
            "original_landing_url",
            "retrieved_at",
            "content_sha256",
        ),
        image_id=image_id,
    )
    if str(row.get("license_name") or "").strip() != LICENSE_NAME:
        raise ValueError(f"{image_id}: licence name is not {LICENSE_NAME}")
    if not _normalizes_to_cc_by_two(row.get("license_url")):
        raise ValueError(f"{image_id}: licence URL is not plain CC BY 2.0")
    for field in ("author_profile_url", "original_landing_url"):
        if not str(row[field]).strip().startswith(("https://", "http://")):
            raise ValueError(f"{image_id}: {field} is not an HTTP(S) URL")
    split = str(row.get("split") or "").strip()
    if split not in {"validation", "test", "train"}:
        raise ValueError(f"{image_id}: unsupported Open Images split {split!r}")
    expected_s3_url = CVDF_IMAGE_URL.format(split=split, image_id=image_id)
    if str(row.get("s3_url") or "").strip() != expected_s3_url:
        raise ValueError(f"{image_id}: unexpected CVDF source URL")

    content_sha256 = str(row["content_sha256"]).strip().lower()
    if len(content_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in content_sha256
    ):
        raise ValueError(f"{image_id}: invalid content SHA-256")
    expected_path = images_root / f"{image_id}.jpg"
    manifest_path = Path(str(row.get("local_path") or ""))
    if manifest_path.is_symlink() or expected_path.is_symlink():
        raise ValueError(f"{image_id}: image path must not be a symlink")
    try:
        resolved_expected = expected_path.resolve(strict=True)
        resolved_manifest = manifest_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"{image_id}: manifest image is missing") from error
    if resolved_expected != resolved_manifest:
        raise ValueError(f"{image_id}: local_path escapes or disagrees with images root")
    if not resolved_expected.is_file():
        raise ValueError(f"{image_id}: local_path is not a regular file")
    expected_bytes = int(row.get("bytes") or 0)
    actual_bytes = resolved_expected.stat().st_size
    if expected_bytes < 1 or actual_bytes != expected_bytes:
        raise ValueError(f"{image_id}: manifest byte count does not match image")
    if actual_bytes > MAX_IMAGE_BYTES:
        raise ValueError(f"{image_id}: image exceeds the bounded 64 MiB decode limit")
    payload = resolved_expected.read_bytes()
    if len(payload) != expected_bytes:
        raise ValueError(f"{image_id}: image changed while it was being read")
    if hashlib.sha256(payload).hexdigest() != content_sha256:
        raise ValueError(f"{image_id}: image content SHA-256 does not match manifest")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
        if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
            raise ValueError(f"{image_id}: invalid or unbounded image dimensions")
        descriptor = describe_preview_bytes(payload)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"{image_id}: image cannot be decoded") from error
    if descriptor.preview_sha256 != content_sha256:
        raise AssertionError("descriptor digest and verified content digest disagree")
    return VerifiedRow(
        source=dict(row),
        source_corpus=source_corpus,
        local_path=resolved_expected,
        width=width,
        height=height,
        descriptor=descriptor,
    )


def _logical_corpus_sha256(rows: Sequence[VerifiedRow]) -> str:
    records = [
        {
            **{
                column: row.source.get(column)
                for column in SOURCE_COLUMNS
                if column != "local_path"
            },
            "width": row.width,
            "height": row.height,
            "average_hash": row.descriptor.average_hash,
            "difference_hash": row.descriptor.difference_hash,
        }
        for row in sorted(rows, key=lambda item: item.image_id)
    ]
    return _canonical_sha256({"schema": SOURCE_SCHEMA, "rows": records})


def verify_corpus(
    manifest_path: Path,
    images_root: Path,
    *,
    expected_manifest_sha256: str,
    expected_count: int,
    source_corpus: str,
) -> VerifiedCorpus:
    """Verify every manifest row, attribution field, path, byte count, and image hash."""
    if expected_count < 1:
        raise ValueError("expected_count must be positive")
    if len(expected_manifest_sha256) != 64:
        raise ValueError("expected manifest digest must be a SHA-256 hex string")
    _assert_memory_limit()
    if manifest_path.is_symlink() or images_root.is_symlink():
        raise ValueError("source manifest and images root must not be symlinks")
    manifest_path = manifest_path.resolve(strict=True)
    images_root = images_root.resolve(strict=True)
    if not manifest_path.is_file() or not images_root.is_dir():
        raise ValueError("source manifest or images root has an invalid type")
    manifest_size = manifest_path.stat().st_size
    if manifest_size < 1 or manifest_size > MAX_MANIFEST_BYTES:
        raise ValueError("source manifest exceeds the bounded 16 MiB input limit")
    manifest_payload = manifest_path.read_bytes()
    if len(manifest_payload) != manifest_size:
        raise ValueError("source manifest changed while it was being read")
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    if manifest_sha256 != expected_manifest_sha256.lower():
        raise ValueError(
            "source manifest is not the pinned immutable manifest: "
            f"expected {expected_manifest_sha256.lower()}, got {manifest_sha256}"
        )
    rows = _read_parquet(manifest_payload, expected_count=expected_count)
    verified: list[VerifiedRow] = []
    for index, row in enumerate(sorted(rows, key=lambda item: str(item["image_id"]))):
        verified.append(_verified_image(row, images_root=images_root, source_corpus=source_corpus))
        if index % 100 == 0:
            _assert_memory_limit()
    ids = [row.image_id for row in verified]
    digests = [row.content_sha256 for row in verified]
    paths = [row.local_path for row in verified]
    for name, values in (("image ids", ids), ("content hashes", digests), ("paths", paths)):
        if len(set(values)) != len(values):
            raise ValueError(f"source corpus contains duplicate {name}")
    return VerifiedCorpus(
        rows=tuple(verified),
        manifest_path=manifest_path,
        images_root=images_root,
        manifest_sha256=manifest_sha256,
        logical_sha256=_logical_corpus_sha256(verified),
        source_corpus=source_corpus,
    )


def _stable_rank(seed: str, scope: str, image_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{image_id}".encode()).hexdigest()


def _standardized_vectors(rows: Sequence[VerifiedRow]) -> np.ndarray:
    vectors = np.stack([row.selection_vector() for row in rows]).astype(np.float32)
    means = vectors.mean(axis=0, dtype=np.float64)
    scales = vectors.std(axis=0, dtype=np.float64)
    scales[scales < 1e-7] = 1.0
    return ((vectors - means) / scales).astype(np.float32)


def _cluster_quotas(labels: np.ndarray, target: int) -> dict[int, int]:
    counts = Counter(int(label) for label in labels)
    if target < len(counts) or target > len(labels):
        raise ValueError("target must cover every visual cluster and fit the corpus")
    quotas = dict.fromkeys(counts, 1)
    weights = {label: math.sqrt(count) for label, count in counts.items()}
    weight_total = sum(weights.values())
    ideals = {label: target * weights[label] / weight_total for label in counts}
    remaining = target - len(quotas)
    while remaining:
        candidates = [label for label in counts if quotas[label] < counts[label]]
        if not candidates:
            raise AssertionError("visual quota allocation exhausted the corpus early")
        chosen = max(candidates, key=lambda label: (ideals[label] - quotas[label], -label))
        quotas[chosen] += 1
        remaining -= 1
    return quotas


def _farthest_first(
    members: Sequence[int],
    *,
    count: int,
    medoid: int,
    vectors: np.ndarray,
    rows: Sequence[VerifiedRow],
    seed: str,
    cluster_label: int,
) -> list[int]:
    if count < 1 or count > len(members) or medoid not in members:
        raise ValueError("invalid within-cluster selection request")
    selected = [medoid]
    candidates = [index for index in members if index != medoid]
    if count == 1:
        return selected
    minimum_distances = np.sum((vectors[candidates] - vectors[medoid]) ** 2, axis=1)
    while len(selected) < count:
        maximum = float(minimum_distances.max())
        tied_offsets = np.flatnonzero(np.isclose(minimum_distances, maximum, rtol=0, atol=1e-7))
        offset = min(
            (int(value) for value in tied_offsets),
            key=lambda value: _stable_rank(
                seed, f"cluster-{cluster_label}", rows[candidates[value]].image_id
            ),
        )
        chosen = candidates.pop(offset)
        selected.append(chosen)
        minimum_distances = np.delete(minimum_distances, offset)
        if candidates:
            distances = np.sum((vectors[candidates] - vectors[chosen]) ** 2, axis=1)
            minimum_distances = np.minimum(minimum_distances, distances)
    return selected


def select_certification(
    corpus: VerifiedCorpus,
    *,
    count: int,
    visual_clusters: int,
    seed: str,
) -> tuple[tuple[VerifiedRow, ...], dict[str, Any]]:
    """Reserve a cluster-balanced, spatially spread, label-blind certification set."""
    rows = tuple(sorted(corpus.rows, key=lambda row: row.image_id))
    if count < visual_clusters or count >= len(rows):
        raise ValueError("certification count must cover clusters and leave training rows")
    clustering = cluster_descriptors(
        [row.descriptor for row in rows], cluster_count=visual_clusters, seed=42
    )
    quotas = _cluster_quotas(clustering.labels, count)
    vectors = _standardized_vectors(rows)
    medoids_by_label = {int(clustering.labels[index]): index for index in clustering.medoid_indices}
    selected_indices: list[int] = []
    for label in sorted(quotas):
        members = [
            index for index, assigned in enumerate(clustering.labels) if int(assigned) == label
        ]
        selected_indices.extend(
            _farthest_first(
                members,
                count=quotas[label],
                medoid=medoids_by_label[label],
                vectors=vectors,
                rows=rows,
                seed=seed,
                cluster_label=label,
            )
        )
    if len(selected_indices) != count or len(set(selected_indices)) != count:
        raise AssertionError("certification selection did not produce the requested unique rows")
    certification = tuple(
        sorted((rows[index] for index in selected_indices), key=lambda row: row.image_id)
    )
    selected_labels = [int(clustering.labels[index]) for index in selected_indices]
    metrics = {
        "policy": "raw-pixel-cluster-sqrt-quota-farthest-first-v1",
        "seed": seed,
        "descriptor_version": DESCRIPTOR_VERSION,
        "visual_cluster_count": visual_clusters,
        "universe_normalized_cluster_entropy": clustering.normalized_entropy,
        "universe_largest_cluster_share": clustering.largest_cluster_share,
        "certification": selected_cluster_metrics(
            selected_labels, universe_cluster_count=visual_clusters
        ),
        "cluster_quotas": {str(label): quotas[label] for label in sorted(quotas)},
    }
    return certification, metrics


def _row_digest(rows: Sequence[VerifiedRow]) -> str:
    return _canonical_sha256(
        [
            [row.image_id, row.content_sha256, row.source_corpus]
            for row in sorted(rows, key=lambda x: x.image_id)
        ]
    )


def _public_attribution(row: VerifiedRow, *, role: str) -> dict[str, Any]:
    result = {
        column: row.source.get(column)
        for column in PUBLIC_ATTRIBUTION_COLUMNS
        if column not in {"role", "source_corpus"}
    }
    result["role"] = role
    result["source_corpus"] = row.source_corpus
    return {column: result[column] for column in PUBLIC_ATTRIBUTION_COLUMNS}


def _private_partition_row(row: VerifiedRow, *, role: str) -> dict[str, Any]:
    return {
        **_public_attribution(row, role=role),
        "local_path": str(row.local_path),
        "width": row.width,
        "height": row.height,
        "average_hash": row.descriptor.average_hash,
        "difference_hash": row.descriptor.difference_hash,
        "descriptor_version": DESCRIPTOR_VERSION,
    }


def _write_json(path: Path, payload: object, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(payload))
    os.chmod(path, mode)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for row in rows:
            handle.write(_canonical_bytes(row))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def _attribution_markdown(*, total: int) -> str:
    return f"""# Open Images attribution

This partition contains {total:,} Open Images photographs obtained from the
CVDF Open Images mirror. Each photograph is licensed **CC BY 2.0** by its named
creator. The row-level creator, profile, original landing page, licence URL,
retrieval timestamp, source URL, and content SHA-256 are preserved in
`attribution.jsonl`.

The partitioning code did not fetch or alter source pixels. Local filesystem
paths exist only in the private manifest.
"""


def _top_up_plan(
    *,
    spec: PartitionSpec,
    base: VerifiedCorpus,
    certification_sha256: str,
    added: bool,
) -> dict[str, Any]:
    return {
        "schema": "triage-open-images-top-up-plan-v1",
        "status": "applied" if added else "awaiting-local-manifest",
        "required_rows": spec.top_up_count,
        "destination_role": "training",
        "accepted_input": {
            "format": "pull_corpus.py manifest.parquet schema",
            "required_columns": list(SOURCE_COLUMNS),
            "required_license": LICENSE_NAME,
            "required_license_url": CC_BY_2_0,
            "manifest_sha256_must_be_supplied": True,
            "images_root_must_be_supplied": True,
        },
        "constraints": [
            f"exactly {spec.top_up_count} rows",
            "every image byte hash and local path must verify",
            "every row retains named creator and complete CC BY 2.0 attribution",
            "image ids and content hashes must be disjoint from the immutable base corpus",
            "top-up rows are training-only and cannot alter certification selection",
            "no network access occurs in this partition stage",
        ],
        "base_manifest_sha256": base.manifest_sha256,
        "base_logical_sha256": base.logical_sha256,
        "frozen_certification_sha256": certification_sha256,
        "interface": {
            "arguments": [
                "--top-up-manifest PATH",
                "--top-up-images PATH",
                "--top-up-manifest-sha256 SHA256",
            ]
        },
    }


def _source_lock(corpus: VerifiedCorpus) -> dict[str, Any]:
    return {
        "source_corpus": corpus.source_corpus,
        "manifest_path": str(corpus.manifest_path),
        "images_root": str(corpus.images_root),
        "manifest_sha256": corpus.manifest_sha256,
        "logical_sha256": corpus.logical_sha256,
        "row_count": len(corpus.rows),
    }


def _write_partition_tree(
    staging: Path,
    *,
    base: VerifiedCorpus,
    certification: Sequence[VerifiedRow],
    training: Sequence[VerifiedRow],
    top_up: VerifiedCorpus | None,
    selection_metrics: Mapping[str, Any],
    spec: PartitionSpec,
) -> dict[str, Any]:
    certification_ids = {row.image_id for row in certification}
    training_ids = {row.image_id for row in training}
    if certification_ids & training_ids:
        raise AssertionError("a source row appears in both certification and training")
    all_rows = [*certification, *training]
    if len({(row.source_corpus, row.image_id) for row in all_rows}) != len(all_rows):
        raise AssertionError("partition contains a repeated source row")

    private_rows = [
        *(_private_partition_row(row, role="certification") for row in certification),
        *(_private_partition_row(row, role="training") for row in training),
    ]
    public_certification = [_public_attribution(row, role="certification") for row in certification]
    public_training = [_public_attribution(row, role="training") for row in training]
    public_attribution = sorted(
        [*public_certification, *public_training],
        key=lambda row: (str(row["source_corpus"]), str(row["image_id"])),
    )
    _write_jsonl(staging / "private" / "partition.jsonl", private_rows, mode=0o600)
    _write_json(
        staging / "private" / "source-lock.json",
        {
            "schema": "triage-open-images-source-lock-v1",
            "sources": [_source_lock(base), *([_source_lock(top_up)] if top_up else [])],
        },
        mode=0o600,
    )
    _write_jsonl(staging / "public" / "certification.jsonl", public_certification, mode=0o644)
    _write_jsonl(staging / "public" / "training.jsonl", public_training, mode=0o644)
    _write_jsonl(staging / "public" / "attribution.jsonl", public_attribution, mode=0o644)
    attribution_path = staging / "public" / "ATTRIBUTION.md"
    attribution_path.write_text(_attribution_markdown(total=len(all_rows)), encoding="utf-8")
    os.chmod(attribution_path, 0o644)

    certification_sha256 = _row_digest(certification)
    top_up_plan = _top_up_plan(
        spec=spec,
        base=base,
        certification_sha256=certification_sha256,
        added=top_up is not None,
    )
    _write_json(staging / "public" / "top-up-plan.json", top_up_plan, mode=0o644)
    file_names = (
        "private/partition.jsonl",
        "private/source-lock.json",
        "public/certification.jsonl",
        "public/training.jsonl",
        "public/attribution.jsonl",
        "public/ATTRIBUTION.md",
        "public/top-up-plan.json",
    )
    file_digests = {name: _file_sha256(staging / name) for name in file_names}
    base_training_count = len(base.rows) - len(certification)
    base_shortfall = max(0, spec.architecture_training_count - base_training_count)
    shortfall = max(0, spec.architecture_training_count - len(training))
    shortfall_explanation = (
        f"Reserving {len(certification)} of the immutable {len(base.rows)} rows for "
        f"certification leaves {base_training_count} base training rows, "
        f"{base_shortfall} short of the architecture's "
        f"{spec.architecture_training_count:,}-row public-training target."
    )
    if top_up is not None:
        shortfall_explanation += (
            f" The verified top-up adds {len(top_up.rows)} training-only rows; "
            f"the final shortfall is {shortfall}."
        )
    manifest: dict[str, Any] = {
        "schema": PARTITION_SCHEMA,
        "source_manifest_sha256": base.manifest_sha256,
        "source_logical_sha256": base.logical_sha256,
        "source_count": len(base.rows),
        "certification_count": len(certification),
        "base_training_count": base_training_count,
        "top_up_training_count": len(top_up.rows) if top_up else 0,
        "training_count": len(training),
        "architecture_training_target": spec.architecture_training_count,
        "training_shortfall": shortfall,
        "training_shortfall_explanation": shortfall_explanation,
        "certification_selected_before_training": True,
        "selection_uses_labels": False,
        "certification_sha256": certification_sha256,
        "training_sha256": _row_digest(training),
        "selection": dict(selection_metrics),
        "top_up_status": top_up_plan["status"],
        "files": file_digests,
        "memory_limit_bytes": MAX_MEMORY_BYTES,
    }
    manifest["partition_sha256"] = _canonical_sha256(manifest)
    _write_json(staging / "public" / "manifest.json", manifest, mode=0o644)
    return manifest


def build_partition(
    *,
    source_manifest: Path,
    source_images: Path,
    output: Path,
    expected_source_manifest_sha256: str = EXPECTED_SOURCE_MANIFEST_SHA256,
    top_up_manifest: Path | None = None,
    top_up_images: Path | None = None,
    top_up_manifest_sha256: str | None = None,
    spec: PartitionSpec = PartitionSpec(),
    seed: str = DEFAULT_SEED,
) -> dict[str, Any]:
    """Verify sources, reserve certification, and atomically emit a partition."""
    if output.exists():
        raise FileExistsError(f"output already exists; use a new versioned path: {output}")
    top_up_arguments = (top_up_manifest, top_up_images, top_up_manifest_sha256)
    if any(value is not None for value in top_up_arguments) and not all(
        value is not None for value in top_up_arguments
    ):
        raise ValueError("top-up manifest, images root, and manifest SHA-256 are all required")
    base = verify_corpus(
        source_manifest,
        source_images,
        expected_manifest_sha256=expected_source_manifest_sha256,
        expected_count=spec.source_count,
        source_corpus="base",
    )
    certification, selection_metrics = select_certification(
        base,
        count=spec.certification_count,
        visual_clusters=spec.visual_clusters,
        seed=seed,
    )
    certification_ids = {row.image_id for row in certification}
    base_training = tuple(row for row in base.rows if row.image_id not in certification_ids)
    if len(base_training) != spec.source_count - spec.certification_count:
        raise AssertionError("base partition arithmetic is inconsistent")

    top_up: VerifiedCorpus | None = None
    if top_up_manifest is not None:
        assert top_up_images is not None and top_up_manifest_sha256 is not None
        top_up = verify_corpus(
            top_up_manifest,
            top_up_images,
            expected_manifest_sha256=top_up_manifest_sha256,
            expected_count=spec.top_up_count,
            source_corpus="top-up",
        )
        base_ids = {row.image_id for row in base.rows}
        base_hashes = {row.content_sha256 for row in base.rows}
        if base_ids & {row.image_id for row in top_up.rows}:
            raise ValueError("top-up image ids overlap the immutable base corpus")
        if base_hashes & {row.content_sha256 for row in top_up.rows}:
            raise ValueError("top-up image bytes overlap the immutable base corpus")
    training = tuple(
        sorted(
            [*base_training, *(top_up.rows if top_up else ())],
            key=lambda row: (row.source_corpus, row.image_id),
        )
    )
    _assert_memory_limit()

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    os.chmod(staging, 0o700)
    try:
        manifest = _write_partition_tree(
            staging,
            base=base,
            certification=certification,
            training=training,
            top_up=top_up,
            selection_metrics=selection_metrics,
            spec=spec,
        )
        os.rename(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-manifest", type=Path, default=DEFAULT_SOURCE_ROOT / "manifest.parquet"
    )
    parser.add_argument("--source-images", type=Path, default=DEFAULT_SOURCE_ROOT / "images")
    parser.add_argument("--source-manifest-sha256", default=EXPECTED_SOURCE_MANIFEST_SHA256)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-up-manifest", type=Path)
    parser.add_argument("--top-up-images", type=Path)
    parser.add_argument("--top-up-manifest-sha256")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _pipeline_lock = acquire_recovery_pipeline_lock()
    manifest = build_partition(
        source_manifest=args.source_manifest,
        source_images=args.source_images,
        output=args.output,
        expected_source_manifest_sha256=args.source_manifest_sha256,
        top_up_manifest=args.top_up_manifest,
        top_up_images=args.top_up_images,
        top_up_manifest_sha256=args.top_up_manifest_sha256,
        seed=args.seed,
    )
    print(
        f"verified {manifest['source_count']} immutable Open Images rows; "
        f"reserved {manifest['certification_count']} certification rows first; "
        f"training={manifest['training_count']}; "
        f"architecture shortfall={manifest['training_shortfall']}"
    )
    print(f"partition: {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
