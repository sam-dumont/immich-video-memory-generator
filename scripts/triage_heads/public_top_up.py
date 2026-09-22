#!/usr/bin/env python3
"""Build a separate immutable Open Images training top-up.

The sealed ``public-corpus-v1`` partition remains read-only.  This command
authenticates that partition, verifies an independently downloaded Open Images
manifest, rejects every exact or perceptual overlap, and emits exactly 400
training-only rows in a second artifact.  It performs no network access.

Future offline invocation, after the trusted acquisition and review commands
have produced and pinned the canonical private source manifest::

    uv run python -m scripts.triage_heads.public_top_up \
      --source-manifest /path/to/top-up/manifest.json \
      --source-images /path/to/top-up/images \
      --output ~/.immich-memories-matrix/triage-heads/public-top-up-v1

Production intentionally fails closed until the trusted acquisition step has
produced the 580-row source manifest and its reviewed SHA-256 has been pinned in
``EXPECTED_TOP_UP_SOURCE_MANIFEST_SHA256`` below.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import stat
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps
from scripts.triage_heads.image_diversity import (
    DESCRIPTOR_VERSION,
    confirmed_near_duplicate,
)
from scripts.triage_heads.memory import acquire_recovery_pipeline_lock
from scripts.triage_heads.public_corpus import (
    CC_BY_2_0,
    LICENSE_NAME,
    MAX_IMAGE_BYTES,
    MAX_MEMORY_BYTES,
    PARTITION_SCHEMA,
    PUBLIC_ATTRIBUTION_COLUMNS,
    SOURCE_SCHEMA,
    VerifiedCorpus,
    VerifiedRow,
    _assert_memory_limit,
    _canonical_bytes,
    _canonical_sha256,
    _file_sha256,
    _private_partition_row,
    _public_attribution,
    _row_digest,
    select_certification,
    verify_corpus,
)

TOP_UP_SCHEMA = "triage-open-images-training-top-up-v1"
TOP_UP_SOURCE_LOCK_SCHEMA = "triage-open-images-top-up-source-lock-v1"
EXPECTED_PUBLIC_CORPUS_V1_MANIFEST_SHA256 = (
    "fbcaff06a8c07e91ea246b8332d996085dd198ca9b89319ba0d37ec520a81474"
)
EXPECTED_TOP_UP_SOURCE_MANIFEST_SHA256: str | None = None
DEFAULT_BASE_PARTITION = (
    Path.home() / ".immich-memories-matrix" / "triage-heads" / "public-corpus-v1"
)
DEFAULT_OUTPUT = Path.home() / ".immich-memories-matrix" / "triage-heads" / "public-top-up-v1"
DEFAULT_SELECTION_SEED = "triage-public-training-top-up-v1"
DEFAULT_OFFICIAL_METADATA = (
    Path.home() / ".immich-memories-distill" / "metadata" / "validation-images-with-rotation.csv"
)
OFFICIAL_METADATA_URL = (
    "https://storage.googleapis.com/openimages/2018_04/validation/"
    "validation-images-with-rotation.csv"
)
EXPECTED_OFFICIAL_METADATA_SHA256 = (
    "ed93a0e121fe345effdfc7359b848dbc64a1ff6778c8c73563157cb500b33a17"
)
OFFICIAL_METADATA_COLUMNS = (
    "ImageID",
    "Subset",
    "OriginalURL",
    "OriginalLandingURL",
    "License",
    "AuthorProfileURL",
    "Author",
    "Title",
    "OriginalSize",
    "OriginalMD5",
    "Thumbnail300KURL",
    "Rotation",
)

_BASE_REQUIRED_FILES = frozenset(
    {
        "private/partition.jsonl",
        "private/source-lock.json",
        "public/certification.jsonl",
        "public/training.jsonl",
        "public/attribution.jsonl",
        "public/ATTRIBUTION.md",
        "public/top-up-plan.json",
    }
)
_STATIC_ARTIFACT_FILES = (
    "private/training.jsonl",
    "private/source-lock.json",
    "public/training.jsonl",
    "public/attribution.jsonl",
    "public/ATTRIBUTION.md",
)
_MAX_IMMUTABLE_ARTIFACT_FILE_BYTES = 16 * 1024**2
_MAX_OFFICIAL_METADATA_BYTES = 64 * 1024**2


@dataclass(frozen=True, slots=True)
class BasePartitionSpec:
    """Arithmetic that identifies the sealed public-corpus-v1 partition."""

    source_count: int = 3_000
    certification_count: int = 400
    training_count: int = 2_600
    architecture_training_target: int = 3_000

    def __post_init__(self) -> None:
        if min(self.source_count, self.certification_count, self.training_count) < 1:
            raise ValueError("base partition counts must be positive")
        if self.certification_count + self.training_count != self.source_count:
            raise ValueError("base certification and training counts must exhaust the source")
        if self.architecture_training_target <= self.training_count:
            raise ValueError("base partition must retain a positive public-training shortfall")

    @property
    def shortfall(self) -> int:
        return self.architecture_training_target - self.training_count


@dataclass(frozen=True, slots=True)
class TopUpSpec:
    """Fixed count and label-blind diversity gates for the supplement."""

    candidate_count: int = 580
    count: int = 400
    visual_clusters: int = 64

    def __post_init__(self) -> None:
        if self.count < 2:
            raise ValueError("top-up count must be at least two")
        if self.candidate_count <= self.count:
            raise ValueError("candidate count must exceed the selected top-up count")
        if not 2 <= self.visual_clusters <= self.count:
            raise ValueError("visual_clusters must fit inside the top-up")


@dataclass(frozen=True, slots=True)
class SealedBasePartition:
    source: VerifiedCorpus
    root: Path
    manifest_sha256: str
    partition_sha256: str
    certification_sha256: str
    certification_file_sha256: str
    immutable_file_sha256: tuple[tuple[str, str], ...]

    @property
    def rows(self) -> tuple[VerifiedRow, ...]:
        return self.source.rows


@dataclass(frozen=True, slots=True)
class OfficialMetadataSeal:
    path: Path
    sha256: str
    url: str = OFFICIAL_METADATA_URL


@dataclass(frozen=True, slots=True)
class RobustPixelFingerprint:
    """Brightness- and dihedral-invariant hashes over full and cropped pixels."""

    median_hashes: tuple[int, ...]
    difference_hashes: tuple[int, ...]
    keypoints: np.ndarray
    orb_descriptors: np.ndarray | None


def _require_sha256(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _read_bounded_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required immutable file is missing or a symlink: {path}")
    size = path.stat().st_size
    if size < 1 or size > maximum_bytes:
        raise ValueError(f"immutable file exceeds its bounded size: {path.name}")
    payload = path.read_bytes()
    if len(payload) != size:
        raise ValueError(f"immutable file changed while it was being read: {path.name}")
    return payload


def _decode_json_object(payload: bytes, *, name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"immutable JSON file cannot be decoded: {name}") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"immutable JSON payload must be an object: {name}")
    return decoded


def _load_json_object(path: Path, *, maximum_bytes: int = 2 * 1024**2) -> dict[str, Any]:
    return _decode_json_object(
        _read_bounded_bytes(path, maximum_bytes=maximum_bytes), name=path.name
    )


def _decode_jsonl(payload: bytes, *, name: str, expected_count: int) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        text = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"immutable JSONL cannot be decoded: {name}") from error
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"{name}:{line_number}: blank rows are forbidden")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"immutable JSONL cannot be decoded: {name}") from error
        if not isinstance(row, dict):
            raise ValueError(f"{name}:{line_number}: row must be an object")
        rows.append(row)
        if len(rows) > expected_count:
            raise ValueError(f"{name} has more than {expected_count} rows")
    if len(rows) != expected_count:
        raise ValueError(f"{name} has {len(rows)} rows; expected {expected_count}")
    return tuple(rows)


def _load_jsonl(path: Path, *, expected_count: int) -> tuple[dict[str, Any], ...]:
    return _decode_jsonl(
        _read_bounded_bytes(path, maximum_bytes=_MAX_IMMUTABLE_ARTIFACT_FILE_BYTES),
        name=path.name,
        expected_count=expected_count,
    )


def _validate_base_manifest(payload: Mapping[str, Any], spec: BasePartitionSpec) -> None:
    expected = {
        "schema": PARTITION_SCHEMA,
        "source_count": spec.source_count,
        "certification_count": spec.certification_count,
        "base_training_count": spec.training_count,
        "top_up_training_count": 0,
        "training_count": spec.training_count,
        "architecture_training_target": spec.architecture_training_target,
        "training_shortfall": spec.shortfall,
        "certification_selected_before_training": True,
        "selection_uses_labels": False,
        "top_up_status": "awaiting-local-manifest",
        "memory_limit_bytes": MAX_MEMORY_BYTES,
    }
    disagreements = [key for key, value in expected.items() if payload.get(key) != value]
    if disagreements:
        raise ValueError(
            "base partition is not the sealed public-corpus-v1 shape: " + ", ".join(disagreements)
        )
    digest = _require_sha256(str(payload.get("partition_sha256") or ""), field="partition_sha256")
    unsigned = dict(payload)
    del unsigned["partition_sha256"]
    if _canonical_sha256(unsigned) != digest:
        raise ValueError("base partition manifest self-digest does not verify")
    files = payload.get("files")
    if not isinstance(files, Mapping) or set(files) != _BASE_REQUIRED_FILES:
        raise ValueError("base partition manifest has an unexpected immutable file set")


def _verify_base_partition_rows(
    *,
    manifest: Mapping[str, Any],
    corpus: VerifiedCorpus,
    spec: BasePartitionSpec,
    immutable_files: Mapping[str, bytes],
) -> None:
    private_rows = _decode_jsonl(
        immutable_files["private/partition.jsonl"],
        name="private/partition.jsonl",
        expected_count=spec.source_count,
    )
    verified_by_id = {row.image_id: row for row in corpus.rows}
    if len(verified_by_id) != spec.source_count:
        raise ValueError("verified base source contains duplicate image ids")
    certification_ids: set[str] = set()
    training_ids: set[str] = set()
    for raw in private_rows:
        image_id = str(raw.get("image_id") or "")
        verified = verified_by_id.get(image_id)
        if verified is None:
            raise ValueError("base private partition references an image outside its source lock")
        role = str(raw.get("role") or "")
        if role == "certification":
            certification_ids.add(image_id)
        elif role == "training":
            training_ids.add(image_id)
        else:
            raise ValueError(f"base private partition has unsupported role {role!r}")
        expected = _private_partition_row(verified, role=role)
        if raw != expected:
            raise ValueError(f"base private partition row disagrees with source bytes: {image_id}")
    if len(certification_ids) != spec.certification_count:
        raise ValueError("base private partition certification count is inconsistent")
    if len(training_ids) != spec.training_count or certification_ids & training_ids:
        raise ValueError("base private partition training split is inconsistent")
    certification_rows = tuple(verified_by_id[image_id] for image_id in certification_ids)
    training_rows = tuple(verified_by_id[image_id] for image_id in training_ids)
    if _row_digest(certification_rows) != manifest.get("certification_sha256"):
        raise ValueError("base certification semantic digest does not verify")
    if _row_digest(training_rows) != manifest.get("training_sha256"):
        raise ValueError("base training semantic digest does not verify")

    for name, expected_role, expected_ids in (
        ("certification.jsonl", "certification", certification_ids),
        ("training.jsonl", "training", training_ids),
    ):
        public_rows = _decode_jsonl(
            immutable_files[f"public/{name}"],
            name=f"public/{name}",
            expected_count=len(expected_ids),
        )
        ids = {str(row.get("image_id") or "") for row in public_rows}
        if ids != expected_ids or any(row.get("role") != expected_role for row in public_rows):
            raise ValueError(f"base public {expected_role} rows disagree with the sealed split")


def verify_base_partition(
    root: Path,
    *,
    expected_manifest_sha256: str = EXPECTED_PUBLIC_CORPUS_V1_MANIFEST_SHA256,
    spec: BasePartitionSpec = BasePartitionSpec(),
) -> SealedBasePartition:
    """Authenticate the sealed v1 partition and reconstruct its public pixels."""
    root = root.resolve(strict=True)
    manifest_path = root / "public" / "manifest.json"
    expected_manifest_sha256 = _require_sha256(
        expected_manifest_sha256, field="base manifest SHA-256"
    )
    manifest_bytes = _read_bounded_bytes(manifest_path, maximum_bytes=2 * 1024**2)
    observed_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if observed_manifest_sha256 != expected_manifest_sha256:
        raise ValueError(
            "base partition manifest is not the pinned public-corpus-v1 manifest: "
            f"expected {expected_manifest_sha256}, got {observed_manifest_sha256}"
        )
    manifest = _decode_json_object(manifest_bytes, name="public/manifest.json")
    _validate_base_manifest(manifest, spec)
    files = manifest["files"]
    assert isinstance(files, Mapping)
    immutable_files: dict[str, bytes] = {}
    for relative, expected in sorted(files.items()):
        expected_digest = _require_sha256(str(expected), field=f"base {relative} SHA-256")
        path = root / str(relative)
        payload = _read_bounded_bytes(path, maximum_bytes=_MAX_IMMUTABLE_ARTIFACT_FILE_BYTES)
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError(f"base partition immutable file failed verification: {relative}")
        immutable_files[str(relative)] = payload

    source_lock = _decode_json_object(
        immutable_files["private/source-lock.json"], name="private/source-lock.json"
    )
    sources = source_lock.get("sources")
    if source_lock.get("schema") != "triage-open-images-source-lock-v1" or not isinstance(
        sources, list
    ):
        raise ValueError("base source lock has an unsupported schema")
    if len(sources) != 1 or not isinstance(sources[0], Mapping):
        raise ValueError("base source lock must contain exactly one immutable source")
    source = sources[0]
    if source.get("source_corpus") != "base" or source.get("row_count") != spec.source_count:
        raise ValueError("base source lock identifies an unexpected source")
    if source.get("manifest_sha256") != manifest.get("source_manifest_sha256"):
        raise ValueError("base source manifest digest disagrees across seals")
    if source.get("logical_sha256") != manifest.get("source_logical_sha256"):
        raise ValueError("base logical digest disagrees across seals")
    corpus = verify_corpus(
        Path(str(source.get("manifest_path") or "")),
        Path(str(source.get("images_root") or "")),
        expected_manifest_sha256=str(source["manifest_sha256"]),
        expected_count=spec.source_count,
        source_corpus="base",
    )
    _verify_base_partition_rows(
        manifest=manifest,
        corpus=corpus,
        spec=spec,
        immutable_files=immutable_files,
    )
    _assert_memory_limit()
    return SealedBasePartition(
        source=corpus,
        root=root,
        manifest_sha256=observed_manifest_sha256,
        partition_sha256=str(manifest["partition_sha256"]),
        certification_sha256=str(manifest["certification_sha256"]),
        certification_file_sha256=str(files["public/certification.jsonl"]),
        immutable_file_sha256=tuple(
            sorted((str(relative), str(digest)) for relative, digest in files.items())
        ),
    )


def _bits_as_integer(bits: np.ndarray) -> int:
    result = 0
    for index, value in enumerate(np.asarray(bits, dtype=bool).reshape(-1)):
        if value:
            result |= 1 << index
    return result


def _dihedral_arrays(array: np.ndarray) -> tuple[np.ndarray, ...]:
    rotations = tuple(np.rot90(array, turns) for turns in range(4))
    return (*rotations, *(np.fliplr(value) for value in rotations))


def _robust_fingerprint(row: VerifiedRow) -> RobustPixelFingerprint:
    payload = row.local_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != row.content_sha256:
        raise ValueError(f"{row.image_id}: image changed before duplicate fingerprinting")
    try:
        with Image.open(io.BytesIO(payload)) as source:
            gray = ImageOps.autocontrast(ImageOps.exif_transpose(source).convert("L"))
    except OSError as error:
        raise ValueError(f"{row.image_id}: image cannot be fingerprinted") from error
    width, height = gray.size
    crop_width = max(1, round(width * 0.90))
    crop_height = max(1, round(height * 0.90))
    offsets = (
        (0, 0),
        (width - crop_width, 0),
        (0, height - crop_height),
        (width - crop_width, height - crop_height),
        ((width - crop_width) // 2, (height - crop_height) // 2),
    )
    boxes = [(0, 0, width, height), *((x, y, x + crop_width, y + crop_height) for x, y in offsets)]
    views = [gray.crop(box) for box in boxes]
    median_hashes: list[int] = []
    difference_hashes: list[int] = []
    for view in views:
        median_pixels = np.asarray(view.resize((16, 16), Image.Resampling.LANCZOS), dtype=np.uint8)
        difference_pixels = np.asarray(
            view.resize((17, 17), Image.Resampling.LANCZOS), dtype=np.int16
        )
        median_hashes.append(
            min(
                _bits_as_integer(transformed >= np.median(transformed))
                for transformed in _dihedral_arrays(median_pixels)
            )
        )
        difference_hashes.append(
            min(
                _bits_as_integer(transformed[:16, 1:] >= transformed[:16, :-1])
                for transformed in _dihedral_arrays(difference_pixels)
            )
        )
    orb_image = np.asarray(gray, dtype=np.uint8)
    maximum_edge = max(orb_image.shape)
    if maximum_edge > 512:
        scale = 512 / maximum_edge
        orb_image = cv2.resize(
            orb_image,
            (max(1, round(orb_image.shape[1] * scale)), max(1, round(orb_image.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    detector = cv2.ORB_create(nfeatures=512, fastThreshold=5)
    keypoints, descriptors = detector.detectAndCompute(orb_image, None)
    points = np.asarray([keypoint.pt for keypoint in keypoints], dtype=np.float32).reshape(-1, 2)
    return RobustPixelFingerprint(
        tuple(median_hashes),
        tuple(difference_hashes),
        points,
        descriptors,
    )


def _robust_near_duplicate(left: RobustPixelFingerprint, right: RobustPixelFingerprint) -> bool:
    for left_median, left_difference in zip(
        left.median_hashes, left.difference_hashes, strict=True
    ):
        for right_median, right_difference in zip(
            right.median_hashes, right.difference_hashes, strict=True
        ):
            median_distance = (left_median ^ right_median).bit_count()
            difference_distance = (left_difference ^ right_difference).bit_count()
            if median_distance <= 64 and difference_distance <= 64:
                return True
    return False


def _orb_near_duplicate(left: RobustPixelFingerprint, right: RobustPixelFingerprint) -> bool:
    if left.orb_descriptors is None or right.orb_descriptors is None:
        return False
    if len(left.orb_descriptors) < 8 or len(right.orb_descriptors) < 8:
        return False
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        left.orb_descriptors, right.orb_descriptors, k=2
    )
    good = [
        first
        for pair in matches
        if len(pair) == 2
        for first, second in [pair]
        if first.distance <= 72 and first.distance < 0.75 * second.distance
    ]
    if len(good) < 8:
        return False
    source = np.asarray([left.keypoints[match.queryIdx] for match in good], dtype=np.float32)
    target = np.asarray([right.keypoints[match.trainIdx] for match in good], dtype=np.float32)
    _matrix, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
    if mask is None:
        return False
    inliers = int(mask.sum())
    return inliers >= 8 and inliers / len(good) >= 0.35


def _orb_overlap_candidates(
    queries: Sequence[RobustPixelFingerprint],
    references: Sequence[RobustPixelFingerprint],
    *,
    exclude_same_index: bool = False,
) -> set[tuple[int, int]]:
    """Use an LSH index to avoid an all-pairs local-feature comparison."""
    usable = [
        (index, fingerprint)
        for index, fingerprint in enumerate(references)
        if fingerprint.orb_descriptors is not None and len(fingerprint.orb_descriptors) >= 8
    ]
    if not usable:
        return set()
    if len(usable) <= 32:
        return {
            (query_index, reference_index)
            for query_index, query in enumerate(queries)
            for reference_index, reference in usable
            if not (exclude_same_index and query_index == reference_index)
            if _orb_near_duplicate(query, reference)
        }
    matcher = cv2.FlannBasedMatcher(
        {
            "algorithm": 6,
            "table_number": 12,
            "key_size": 20,
            "multi_probe_level": 2,
        },
        {"checks": 64},
    )
    matcher.add([fingerprint.orb_descriptors for _index, fingerprint in usable])
    matcher.train()
    candidates: set[tuple[int, int]] = set()
    for query_index, query in enumerate(queries):
        if query.orb_descriptors is None or len(query.orb_descriptors) < 8:
            continue
        nearest = matcher.knnMatch(query.orb_descriptors, k=4)
        votes: list[int] = []
        for matches in nearest:
            seen_references: set[int] = set()
            for match in matches:
                reference_index, _reference = usable[match.imgIdx]
                if exclude_same_index and query_index == reference_index:
                    continue
                if match.distance <= 72 and match.imgIdx not in seen_references:
                    votes.append(match.imgIdx)
                    seen_references.add(match.imgIdx)
        counts = Counter(votes)
        for matcher_index, count in counts.items():
            reference_index, reference = usable[matcher_index]
            if count >= 8 and _orb_near_duplicate(query, reference):
                candidates.add((query_index, reference_index))
    return candidates


def _reject_overlaps(base: Sequence[VerifiedRow], top_up: Sequence[VerifiedRow]) -> None:
    base_ids = {row.image_id for row in base}
    top_up_ids = {row.image_id for row in top_up}
    if overlap := base_ids & top_up_ids:
        raise ValueError(f"top-up image ids overlap public-corpus-v1 ({len(overlap)} rows)")
    base_hashes = {row.content_sha256 for row in base}
    top_up_hashes = {row.content_sha256 for row in top_up}
    if overlap := base_hashes & top_up_hashes:
        raise ValueError(f"top-up image bytes overlap public-corpus-v1 ({len(overlap)} rows)")

    ordered_top_up = tuple(sorted(top_up, key=lambda row: row.image_id))
    ordered_base = tuple(sorted(base, key=lambda row: row.image_id))
    base_fingerprints = tuple(_robust_fingerprint(row) for row in ordered_base)
    top_up_fingerprints = tuple(_robust_fingerprint(row) for row in ordered_top_up)
    for index, candidate in enumerate(ordered_top_up):
        for existing, existing_fingerprint in zip(ordered_base, base_fingerprints, strict=True):
            if confirmed_near_duplicate(
                candidate.descriptor, existing.descriptor
            ) or _robust_near_duplicate(top_up_fingerprints[index], existing_fingerprint):
                raise ValueError(
                    "top-up contains a perceptual duplicate of public-corpus-v1: "
                    f"{candidate.image_id}"
                )
        for earlier_index, earlier in enumerate(ordered_top_up[:index]):
            if confirmed_near_duplicate(
                candidate.descriptor, earlier.descriptor
            ) or _robust_near_duplicate(
                top_up_fingerprints[index], top_up_fingerprints[earlier_index]
            ):
                raise ValueError(
                    f"top-up contains an internal perceptual duplicate: {candidate.image_id}"
                )
        if index % 25 == 0:
            _assert_memory_limit()
    if matches := _orb_overlap_candidates(top_up_fingerprints, base_fingerprints):
        candidate_index, _base_index = min(matches)
        raise ValueError(
            "top-up contains a crop-resistant perceptual duplicate of public-corpus-v1: "
            f"{ordered_top_up[candidate_index].image_id}"
        )
    internal_matches = {
        (left, right)
        for left, right in _orb_overlap_candidates(
            top_up_fingerprints,
            top_up_fingerprints,
            exclude_same_index=True,
        )
        if left < right
    }
    if internal_matches:
        _left, right = min(internal_matches)
        raise ValueError(
            "top-up contains an internal crop-resistant perceptual duplicate: "
            f"{ordered_top_up[right].image_id}"
        )


def _require_canonical_top_up_source(corpus: VerifiedCorpus) -> None:
    """Reject values that validate only after stripping or URL normalization."""
    canonical_fields = (
        "image_id",
        "split",
        "s3_url",
        "license_name",
        "author_profile_url",
        "original_landing_url",
        "retrieved_at",
        "content_sha256",
    )
    for row in corpus.rows:
        for field in canonical_fields:
            raw = str(row.source.get(field) or "")
            if raw != raw.strip():
                raise ValueError(f"{row.image_id}: {field} is not in canonical form")
        if row.source.get("license_name") != LICENSE_NAME:
            raise ValueError(f"{row.image_id}: licence name is not canonical {LICENSE_NAME}")
        if row.source.get("license_url") != CC_BY_2_0:
            raise ValueError(f"{row.image_id}: licence URL is not canonical CC BY 2.0")
        if not isinstance(row.source.get("bytes"), int) or isinstance(
            row.source.get("bytes"), bool
        ):
            raise ValueError(f"{row.image_id}: byte count must be a canonical integer")


def _verify_official_metadata(
    corpus: VerifiedCorpus,
    metadata_path: Path,
    *,
    expected_sha256: str,
) -> OfficialMetadataSeal:
    """Bind every attribution claim to the pinned official Open Images CSV."""
    expected_sha256 = _require_sha256(
        expected_sha256,
        field="official Open Images metadata SHA-256",
    )
    if metadata_path.is_symlink():
        raise ValueError("official Open Images metadata must not be a symlink")
    metadata_path = metadata_path.resolve(strict=True)
    payload = _read_bounded_bytes(
        metadata_path,
        maximum_bytes=_MAX_OFFICIAL_METADATA_BYTES,
    )
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if observed_sha256 != expected_sha256:
        raise ValueError(
            "official Open Images metadata is not the pinned snapshot: "
            f"expected {expected_sha256}, got {observed_sha256}"
        )
    try:
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if tuple(reader.fieldnames or ()) != OFFICIAL_METADATA_COLUMNS:
            raise ValueError("official Open Images metadata columns changed")
        wanted = {row.image_id for row in corpus.rows}
        official_by_id: dict[str, dict[str, str]] = {}
        for raw in reader:
            image_id = str(raw.get("ImageID") or "")
            if image_id not in wanted:
                continue
            if image_id in official_by_id:
                raise ValueError(f"official Open Images metadata duplicates {image_id}")
            if None in raw:
                raise ValueError("official Open Images metadata row has unexpected columns")
            official_by_id[image_id] = raw
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("official Open Images metadata cannot be decoded") from error

    missing = wanted - set(official_by_id)
    if missing:
        raise ValueError(
            f"top-up candidate is absent from official Open Images metadata: {min(missing)}"
        )
    bindings = {
        "split": "Subset",
        "license_url": "License",
        "author": "Author",
        "author_profile_url": "AuthorProfileURL",
        "original_landing_url": "OriginalLandingURL",
        "title": "Title",
    }
    for row in corpus.rows:
        official = official_by_id[row.image_id]
        if official["Subset"] != "validation":
            raise ValueError(f"{row.image_id}: top-up must use the official validation split")
        if official["License"] != CC_BY_2_0:
            raise ValueError(f"{row.image_id}: official licence is not canonical CC BY 2.0")
        if not official["OriginalURL"].startswith(("https://", "http://")):
            raise ValueError(f"{row.image_id}: official original source URL is not HTTP(S)")
        disagreements = [
            source_field
            for source_field, official_field in bindings.items()
            if str(row.source.get(source_field) or "") != official[official_field]
        ]
        if disagreements:
            raise ValueError(
                f"{row.image_id}: attribution disagrees with official Open Images metadata: "
                + ", ".join(disagreements)
            )
    _assert_memory_limit()
    return OfficialMetadataSeal(path=metadata_path, sha256=observed_sha256)


def _recheck_verified_corpus(corpus: VerifiedCorpus) -> None:
    """Close the validation-to-commit race without retaining any image bytes."""
    if (
        corpus.manifest_path.is_symlink()
        or _file_sha256(corpus.manifest_path) != corpus.manifest_sha256
    ):
        raise ValueError(f"{corpus.source_corpus} source manifest changed during the build")
    for index, row in enumerate(corpus.rows):
        if row.local_path.is_symlink() or not row.local_path.is_file():
            raise ValueError(f"{corpus.source_corpus} source image disappeared during the build")
        if _file_sha256(row.local_path) != row.content_sha256:
            raise ValueError(f"{corpus.source_corpus} source image changed during the build")
        if index % 100 == 0:
            _assert_memory_limit()


def _recheck_sealed_base_files(base: SealedBasePartition) -> None:
    for relative, expected_digest in base.immutable_file_sha256:
        payload = _read_bounded_bytes(
            base.root / relative,
            maximum_bytes=_MAX_IMMUTABLE_ARTIFACT_FILE_BYTES,
        )
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError(f"base partition changed during the build: {relative}")


def _select_top_up(
    corpus: VerifiedCorpus, *, spec: TopUpSpec
) -> tuple[tuple[VerifiedRow, ...], dict[str, Any]]:
    selected, raw_metrics = select_certification(
        corpus,
        count=spec.count,
        visual_clusters=spec.visual_clusters,
        seed=DEFAULT_SELECTION_SEED,
    )
    selected_metrics = raw_metrics.get("certification")
    if not isinstance(selected_metrics, Mapping):
        raise AssertionError("label-blind selector omitted selected-cohort metrics")
    metrics = {
        "policy": "raw-pixel-cluster-sqrt-quota-farthest-first-training-v1",
        "seed": DEFAULT_SELECTION_SEED,
        "selection_uses_labels": False,
        "descriptor_version": DESCRIPTOR_VERSION,
        "candidate_pool_count": len(corpus.rows),
        "selected_count": len(selected),
        "visual_cluster_count": spec.visual_clusters,
        "universe_normalized_cluster_entropy": raw_metrics["universe_normalized_cluster_entropy"],
        "universe_largest_cluster_share": raw_metrics["universe_largest_cluster_share"],
        "selected": dict(selected_metrics),
        "cluster_quotas": raw_metrics["cluster_quotas"],
    }
    if len(selected) != spec.count:
        raise AssertionError("label-blind selection did not close the exact top-up shortfall")
    if selected_metrics.get("represented_clusters") != spec.visual_clusters:
        raise ValueError("selected top-up does not represent every visual cluster")
    return selected, metrics


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _attribution_markdown(*, count: int) -> str:
    return f"""# Open Images training top-up attribution

This separate training-only supplement contains {count:,} Open Images photographs
obtained from the CVDF Open Images mirror. Each photograph is licensed **CC BY
2.0** by its named creator. Row-level creator, profile, original landing page,
licence URL, retrieval timestamp, source URL, and content SHA-256 are preserved
in `attribution.jsonl`.

This artifact contains no certification rows and does not modify
`public-corpus-v1`. Its selection and duplicate checks use raw pixels only.
The verified image bytes are copied into `public/images/` before the immutable
manifest is committed. Local source paths exist only in the private source lock.
"""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_create_only(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if mode == 0o600 else 0o755)
    if path.exists():
        existing = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) != mode
            or path.read_bytes() != payload
        ):
            raise RuntimeError(f"existing immutable file differs: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = path.stat()
            if (
                path.is_symlink()
                or not path.is_file()
                or existing.st_nlink != 1
                or stat.S_IMODE(existing.st_mode) != mode
                or path.read_bytes() != payload
            ):
                raise RuntimeError(f"existing immutable file differs: {path}")
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_verified_image_create_only(row: VerifiedRow, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    expected_bytes = int(row.source["bytes"])
    if destination.exists():
        existing = destination.stat()
        if (
            destination.is_symlink()
            or not destination.is_file()
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) != 0o644
            or existing.st_size != expected_bytes
            or _file_sha256(destination) != row.content_sha256
        ):
            raise RuntimeError(f"existing immutable image differs: {destination.name}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            with row.local_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_IMAGE_BYTES:
                        raise ValueError(
                            f"{row.image_id}: source image exceeds 64 MiB while copying"
                        )
                    digest.update(chunk)
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if written != expected_bytes or digest.hexdigest() != row.content_sha256:
            raise ValueError(f"{row.image_id}: source image changed during immutable copy")
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing = destination.stat()
            if (
                destination.is_symlink()
                or not destination.is_file()
                or existing.st_nlink != 1
                or stat.S_IMODE(existing.st_mode) != 0o644
                or existing.st_size != expected_bytes
                or _file_sha256(destination) != row.content_sha256
            ):
                raise RuntimeError(f"existing immutable image differs: {destination.name}")
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _artifact_payloads(
    *,
    base: SealedBasePartition,
    candidate_pool: VerifiedCorpus,
    official_metadata: OfficialMetadataSeal,
    selected: Sequence[VerifiedRow],
    diversity: Mapping[str, Any],
    spec: TopUpSpec,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    rows = tuple(sorted(selected, key=lambda row: row.image_id))
    public_rows = [_public_attribution(row, role="training") for row in rows]
    _verify_public_rows(public_rows, expected_count=spec.count)
    private_rows = []
    for row in rows:
        private_row = _private_partition_row(row, role="training")
        private_row["local_path"] = f"public/images/{row.image_id}.jpg"
        private_rows.append(private_row)
    source_lock = {
        "schema": TOP_UP_SOURCE_LOCK_SCHEMA,
        "base_partition": {
            "root": str(base.root),
            "manifest_sha256": base.manifest_sha256,
            "partition_sha256": base.partition_sha256,
            "certification_sha256": base.certification_sha256,
            "certification_file_sha256": base.certification_file_sha256,
        },
        "top_up_source": {
            "schema": SOURCE_SCHEMA,
            "manifest_path": str(candidate_pool.manifest_path),
            "images_root": str(candidate_pool.images_root),
            "manifest_sha256": candidate_pool.manifest_sha256,
            "logical_sha256": candidate_pool.logical_sha256,
            "row_count": len(candidate_pool.rows),
            "official_metadata_path": str(official_metadata.path),
            "official_metadata_url": official_metadata.url,
            "official_metadata_sha256": official_metadata.sha256,
        },
    }
    payloads = {
        "private/training.jsonl": _jsonl_bytes(private_rows),
        "private/source-lock.json": _canonical_bytes(source_lock),
        "public/training.jsonl": _jsonl_bytes(public_rows),
        "public/attribution.jsonl": _jsonl_bytes(public_rows),
        "public/ATTRIBUTION.md": _attribution_markdown(count=len(rows)).encode("utf-8"),
    }
    file_digests = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
    file_digests.update({f"public/images/{row.image_id}.jpg": row.content_sha256 for row in rows})
    manifest: dict[str, Any] = {
        "schema": TOP_UP_SCHEMA,
        "role": "training",
        "training_count": len(rows),
        "required_training_count": spec.count,
        "contains_certification_rows": False,
        "selection_uses_labels": False,
        "source_count": len(candidate_pool.rows),
        "source_manifest_sha256": candidate_pool.manifest_sha256,
        "source_logical_sha256": candidate_pool.logical_sha256,
        "official_metadata_url": official_metadata.url,
        "official_metadata_sha256": official_metadata.sha256,
        "candidate_pool_sha256": _row_digest(candidate_pool.rows),
        "training_sha256": _row_digest(rows),
        "base_partition_manifest_sha256": base.manifest_sha256,
        "base_partition_sha256": base.partition_sha256,
        "frozen_certification_sha256": base.certification_sha256,
        "frozen_certification_file_sha256": base.certification_file_sha256,
        "overlap": {
            "image_ids": 0,
            "content_sha256": 0,
            "perceptual_duplicates": 0,
        },
        "diversity": dict(diversity),
        "license_name": LICENSE_NAME,
        "license_url": CC_BY_2_0,
        "memory_limit_bytes": MAX_MEMORY_BYTES,
        "files": file_digests,
    }
    manifest["artifact_sha256"] = _canonical_sha256(manifest)
    return payloads, manifest


def _verify_public_rows(rows: Sequence[Mapping[str, Any]], *, expected_count: int) -> None:
    if len(rows) != expected_count:
        raise ValueError("top-up public training count is inconsistent")
    for row in rows:
        if set(row) != set(PUBLIC_ATTRIBUTION_COLUMNS):
            raise ValueError("top-up public attribution columns changed")
        if row.get("role") != "training" or row.get("source_corpus") != "top-up":
            raise ValueError("top-up artifact contains a non-training row")
        if row.get("license_name") != LICENSE_NAME or row.get("license_url") != CC_BY_2_0:
            raise ValueError("top-up artifact contains a non-CC-BY-2.0 row")
        if "local_path" in row:
            raise ValueError("top-up public artifact leaks a local path")


def verify_top_up_artifact(
    output: Path,
    *,
    expected_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify an existing output as an exact idempotent replay."""
    if output.is_symlink():
        raise ValueError("top-up output must not be a symlink")
    output = output.resolve(strict=True)
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise ValueError("top-up output directory must be mode 700")
    private_directory = output / "private"
    if (
        private_directory.is_symlink()
        or not private_directory.is_dir()
        or stat.S_IMODE(private_directory.stat().st_mode) != 0o700
    ):
        raise ValueError("top-up private directory must be mode 700 and not a symlink")
    manifest_path = output / "public" / "manifest.json"
    manifest_stat = manifest_path.stat()
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_stat.st_nlink != 1
        or stat.S_IMODE(manifest_stat.st_mode) != 0o644
    ):
        raise ValueError("top-up manifest has unsafe linkage or permissions")
    manifest = _load_json_object(manifest_path)
    if manifest != dict(expected_manifest):
        raise RuntimeError("existing top-up manifest differs from the requested immutable build")
    digest = _require_sha256(str(manifest.get("artifact_sha256") or ""), field="artifact SHA-256")
    unsigned = dict(manifest)
    del unsigned["artifact_sha256"]
    if _canonical_sha256(unsigned) != digest:
        raise ValueError("top-up artifact manifest self-digest does not verify")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not set(_STATIC_ARTIFACT_FILES) <= set(files):
        raise ValueError("top-up artifact has an unexpected immutable file set")
    count = int(manifest["training_count"])
    actual_directories = {
        str(path.relative_to(output))
        for path in output.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }
    if actual_directories != {"private", "public", "public/images"}:
        raise ValueError("top-up artifact contains an unexpected directory")
    actual_files = {
        str(path.relative_to(output))
        for path in output.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != {*files, "public/manifest.json"}:
        raise ValueError("top-up artifact contains missing or unexpected files")
    for relative, expected in files.items():
        path = output / str(relative)
        expected_mode = 0o600 if str(relative).startswith("private/") else 0o644
        file_stat = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or file_stat.st_nlink != 1
            or stat.S_IMODE(file_stat.st_mode) != expected_mode
            or (str(relative).startswith("public/images/") and file_stat.st_size > MAX_IMAGE_BYTES)
            or _file_sha256(path) != expected
        ):
            raise ValueError(f"top-up immutable file failed verification: {relative}")
    public_training = _load_jsonl(output / "public" / "training.jsonl", expected_count=count)
    public_attribution = _load_jsonl(output / "public" / "attribution.jsonl", expected_count=count)
    _verify_public_rows(public_training, expected_count=count)
    if public_training != public_attribution:
        raise ValueError("top-up training and attribution rows disagree")
    expected_images = {
        f"public/images/{row['image_id']}.jpg": str(row["content_sha256"])
        for row in public_training
    }
    if len(expected_images) != count:
        raise ValueError("top-up artifact contains duplicate public image ids")
    if set(files) != {*_STATIC_ARTIFACT_FILES, *expected_images}:
        raise ValueError("top-up artifact image file set disagrees with its training rows")
    private_training = _load_jsonl(output / "private" / "training.jsonl", expected_count=count)
    for row in private_training:
        image_id = str(row.get("image_id") or "")
        if row.get("role") != "training" or row.get("local_path") != (
            f"public/images/{image_id}.jpg"
        ):
            raise ValueError("top-up private training row does not bind an artifact image")
        if expected_images.get(str(row["local_path"])) != row.get("content_sha256"):
            raise ValueError("top-up private row image digest disagrees with public attribution")
    if (output / "public" / "certification.jsonl").exists():
        raise ValueError("top-up artifact must not contain certification rows")
    return manifest


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _safe_output_path(output: Path, *, protected: Sequence[Path]) -> Path:
    if output.is_symlink():
        raise ValueError("top-up output must not be a symlink")
    resolved = output.resolve()
    for path in protected:
        protected_path = path.resolve(strict=True)
        if _paths_overlap(resolved, protected_path):
            raise ValueError("top-up output must be disjoint from every immutable input")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("top-up output must be a directory")
    if resolved.exists():
        for path in resolved.rglob("*"):
            if path.is_symlink():
                raise ValueError("top-up output tree must not contain symlinks")
    return resolved


def _build_verified_top_up(
    *,
    base: SealedBasePartition,
    top_up: VerifiedCorpus,
    output: Path,
    official_metadata: Path,
    official_metadata_sha256: str,
    spec: TopUpSpec,
) -> dict[str, Any]:
    """Build the immutable supplement from already authenticated source seals."""
    metadata_seal = _verify_official_metadata(
        top_up,
        official_metadata,
        expected_sha256=official_metadata_sha256,
    )
    _reject_overlaps(base.rows, top_up.rows)
    selected, diversity = _select_top_up(top_up, spec=spec)
    _assert_memory_limit()
    payloads, manifest = _artifact_payloads(
        base=base,
        candidate_pool=top_up,
        official_metadata=metadata_seal,
        selected=selected,
        diversity=diversity,
        spec=spec,
    )

    # Recheck every pixel and the files that freeze certification immediately before commit.
    _recheck_verified_corpus(base.source)
    _recheck_verified_corpus(top_up)
    if _file_sha256(metadata_seal.path) != metadata_seal.sha256:
        raise ValueError("official Open Images metadata changed during the build")
    _recheck_sealed_base_files(base)
    if _file_sha256(base.root / "public" / "manifest.json") != base.manifest_sha256:
        raise ValueError("base partition changed while the top-up was being built")
    if _file_sha256(base.root / "public" / "certification.jsonl") != base.certification_file_sha256:
        raise ValueError("base certification changed while the top-up was being built")

    output = _safe_output_path(
        output,
        protected=(
            base.root,
            base.source.manifest_path,
            base.source.images_root,
            top_up.manifest_path,
            top_up.images_root,
            metadata_seal.path,
        ),
    )
    if (output / "public" / "manifest.json").exists():
        return verify_top_up_artifact(output, expected_manifest=manifest)
    if output.exists():
        existing_directories = {
            str(path.relative_to(output))
            for path in output.rglob("*")
            if path.is_dir() and not path.is_symlink()
        }
        if not existing_directories <= {"private", "public", "public/images"}:
            raise ValueError("uncommitted top-up output contains an unexpected directory")
        existing_files = {
            str(path.relative_to(output))
            for path in output.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        allowed_files = {*manifest["files"], "public/manifest.json"}
        if not existing_files <= allowed_files:
            raise ValueError("uncommitted top-up output contains unexpected files")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    for relative, payload in payloads.items():
        mode = 0o600 if relative.startswith("private/") else 0o644
        _write_create_only(output / relative, payload, mode=mode)
    for row in selected:
        _write_verified_image_create_only(row, output / "public" / "images" / f"{row.image_id}.jpg")
    _write_create_only(output / "public" / "manifest.json", _canonical_bytes(manifest), mode=0o644)
    _fsync_directory(output)
    return verify_top_up_artifact(output, expected_manifest=manifest)


def _build_top_up(
    *,
    base_partition: Path,
    source_manifest: Path,
    source_images: Path,
    source_manifest_sha256: str,
    output: Path,
    official_metadata: Path = DEFAULT_OFFICIAL_METADATA,
    official_metadata_sha256: str = EXPECTED_OFFICIAL_METADATA_SHA256,
    expected_base_manifest_sha256: str = EXPECTED_PUBLIC_CORPUS_V1_MANIFEST_SHA256,
    base_spec: BasePartitionSpec = BasePartitionSpec(),
    spec: TopUpSpec = TopUpSpec(),
) -> dict[str, Any]:
    """Build or exactly replay the immutable 400-row training-only supplement."""
    if spec.count != base_spec.shortfall:
        raise ValueError(
            f"top-up count {spec.count} does not exactly close base shortfall {base_spec.shortfall}"
        )
    base = verify_base_partition(
        base_partition,
        expected_manifest_sha256=expected_base_manifest_sha256,
        spec=base_spec,
    )
    top_up = verify_corpus(
        source_manifest,
        source_images,
        expected_manifest_sha256=_require_sha256(
            source_manifest_sha256, field="top-up source manifest SHA-256"
        ),
        expected_count=spec.candidate_count,
        source_corpus="top-up",
    )
    _require_canonical_top_up_source(top_up)
    return _build_verified_top_up(
        base=base,
        top_up=top_up,
        output=output,
        official_metadata=official_metadata,
        official_metadata_sha256=official_metadata_sha256,
        spec=spec,
    )


def build_top_up(
    *,
    base_partition: Path,
    source_manifest: Path,
    source_images: Path,
    output: Path,
) -> dict[str, Any]:
    """Build the production top-up against the one pinned public-corpus-v1 seal."""
    if EXPECTED_TOP_UP_SOURCE_MANIFEST_SHA256 is None:
        raise RuntimeError(
            "production top-up source manifest is not pinned; complete trusted acquisition "
            "and review its SHA-256 before building"
        )
    # Deferred to avoid a module-import cycle: the acquisition resolver itself
    # authenticates public-corpus-v1 before it is ever allowed to fetch.
    from scripts.triage_heads.public_top_up_acquire import verify_acquired_source

    base_spec = BasePartitionSpec()
    spec = TopUpSpec()
    base = verify_base_partition(
        base_partition,
        expected_manifest_sha256=EXPECTED_PUBLIC_CORPUS_V1_MANIFEST_SHA256,
        spec=base_spec,
    )
    top_up = verify_acquired_source(
        source_manifest,
        source_images,
        expected_manifest_sha256=EXPECTED_TOP_UP_SOURCE_MANIFEST_SHA256,
        expected_count=spec.candidate_count,
    )
    _require_canonical_top_up_source(top_up)
    acquired_manifest = _load_json_object(
        source_manifest,
        maximum_bytes=_MAX_IMMUTABLE_ARTIFACT_FILE_BYTES,
    )
    lineage = {
        "base_public_manifest_sha256": base.manifest_sha256,
        "base_source_manifest_sha256": base.source.manifest_sha256,
        "base_logical_sha256": base.source.logical_sha256,
        "base_top_up_plan_sha256": _file_sha256(base.root / "public" / "top-up-plan.json"),
        "frozen_certification_sha256": base.certification_sha256,
        "official_metadata_sha256": EXPECTED_OFFICIAL_METADATA_SHA256,
        "official_metadata_url": OFFICIAL_METADATA_URL,
        "row_count": spec.candidate_count,
    }
    disagreements = [
        field for field, expected in lineage.items() if acquired_manifest.get(field) != expected
    ]
    if disagreements:
        raise ValueError(
            "reviewed acquired source disagrees with the current sealed inputs: "
            + ", ".join(disagreements)
        )
    return _build_verified_top_up(
        base=base,
        top_up=top_up,
        output=output,
        official_metadata=DEFAULT_OFFICIAL_METADATA,
        official_metadata_sha256=EXPECTED_OFFICIAL_METADATA_SHA256,
        spec=spec,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-partition", type=Path, default=DEFAULT_BASE_PARTITION)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-images", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _pipeline_lock = acquire_recovery_pipeline_lock()
    manifest = build_top_up(
        base_partition=args.base_partition,
        source_manifest=args.source_manifest,
        source_images=args.source_images,
        output=args.output,
    )
    print(
        f"verified immutable public-corpus-v1; training-only top-up="
        f"{manifest['training_count']}; artifact={manifest['artifact_sha256']}"
    )
    print(f"top-up: {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
