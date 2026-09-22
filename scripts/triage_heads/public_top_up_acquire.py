#!/usr/bin/env python3
"""Acquire the private, reviewable 580-row Open Images top-up source.

The command has two deliberately separate modes. ``resolve`` authenticates the
sealed public-corpus-v1 inputs and prints the deterministic acquisition-plan
digest without touching the network. ``download`` performs the same resolution
and then fetches the exact rows into private staging. Redirects are never
followed and only the fixed CVDF Open Images HTTPS origin is accepted.

Publishing this source does *not* make it usable by the production top-up
builder. A separate review command must verify the completed tree and print the
manifest SHA-256 for a human to pin in ``public_top_up.py``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import errno
import fcntl
import hashlib
import hmac
import io
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError
from scripts.triage_heads.memory import acquire_recovery_pipeline_lock
from scripts.triage_heads.public_corpus import (
    CC_BY_2_0,
    CVDF_IMAGE_URL,
    LICENSE_NAME,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    SOURCE_COLUMNS,
    VerifiedCorpus,
    _assert_memory_limit,
    _logical_corpus_sha256,
    _verified_image,
)
from scripts.triage_heads.public_top_up import (
    DEFAULT_BASE_PARTITION,
    DEFAULT_OFFICIAL_METADATA,
    EXPECTED_OFFICIAL_METADATA_SHA256,
    EXPECTED_PUBLIC_CORPUS_V1_MANIFEST_SHA256,
    OFFICIAL_METADATA_COLUMNS,
    OFFICIAL_METADATA_URL,
    BasePartitionSpec,
    TopUpSpec,
    verify_base_partition,
)

ACQUISITION_PLAN_SCHEMA = "triage-open-images-top-up-acquisition-plan-v1"
ACQUISITION_WAL_SCHEMA = "triage-open-images-top-up-download-wal-v1"
ACQUISITION_MANIFEST_SCHEMA = "triage-open-images-top-up-source-manifest-v1"
ACQUISITION_SEAL_SCHEMA = "triage-open-images-top-up-source-seal-v1"
ALLOWED_IMAGE_HOST = "open-images-dataset.s3.amazonaws.com"
DEFAULT_CANDIDATE_INVENTORY = (
    Path.home() / ".immich-memories-distill" / "validation" / "candidates.json"
)
DEFAULT_ACQUISITION_WORKSPACE = (
    Path.home()
    / ".immich-memories-matrix"
    / "triage-heads"
    / "private-public-top-up-acquisition-v1"
)
DEFAULT_ACQUIRED_SOURCE = (
    Path.home() / ".immich-memories-matrix" / "triage-heads" / "public-top-up-source-v1"
)
MAX_CANDIDATE_INVENTORY_BYTES = 32 * 1024**2
MAX_ACQUISITION_MANIFEST_BYTES = 32 * 1024**2
MAX_WAL_BYTES = 64 * 1024**2

_CANDIDATE_INPUT_FIELDS = frozenset(
    {
        "author",
        "author_profile_url",
        "original_landing_url",
        "title",
        "license_url",
    }
)
_PLAN_ROW_FIELDS = frozenset(
    {
        "image_id",
        "split",
        "source_url",
        "license_name",
        "license_url",
        "author",
        "author_profile_url",
        "original_landing_url",
        "title",
        "original_url",
        "original_size",
        "original_md5",
    }
)
_PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "base_public_manifest_sha256",
        "base_source_manifest_sha256",
        "base_logical_sha256",
        "base_top_up_plan_sha256",
        "frozen_certification_sha256",
        "candidate_inventory_sha256",
        "official_metadata_sha256",
        "official_metadata_url",
        "candidate_count",
        "rows",
        "plan_sha256",
    }
)
_WAL_FIELDS = frozenset(
    {
        "schema",
        "plan_sha256",
        "image_id",
        "source_url",
        "retrieved_at",
        "content_sha256",
        "bytes",
        "content_md5",
        "official_original_size",
        "official_original_md5",
    }
)
_SOURCE_ROW_EXTRA_FIELDS = frozenset(
    {
        "official_original_url",
        "official_original_size",
        "official_original_md5",
        "response_content_md5",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _md5_digest(payload: bytes) -> bytes:
    """Return protocol-mandated MD5; SHA-256 remains the security identity."""
    return hashlib.md5(payload, usedforsecurity=False).digest()


def _require_sha256(value: object, *, field: str) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _require_image_id(value: object) -> str:
    image_id = str(value or "")
    if len(image_id) != 16 or any(character not in "0123456789abcdef" for character in image_id):
        raise ValueError(f"invalid canonical Open Images image id: {image_id!r}")
    return image_id


def _regular_snapshot(path: Path, *, mode: int | None = None) -> os.stat_result:
    if path.is_symlink():
        raise ValueError(f"file must not be a symlink: {path}")
    try:
        metadata = path.stat()
    except FileNotFoundError as error:
        raise ValueError(f"required file is missing: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"path must be a regular file: {path}")
    if metadata.st_nlink != 1:
        raise ValueError(f"private source files must be single-link files: {path}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise ValueError(f"private source file mode must be {mode:o}: {path}")
    return metadata


def _read_snapshot(
    path: Path,
    *,
    maximum_bytes: int,
    mode: int | None = None,
) -> bytes:
    before = _regular_snapshot(path, mode=mode)
    if before.st_size < 1 or before.st_size > maximum_bytes:
        raise ValueError(f"file exceeds its bounded size: {path}")
    payload = path.read_bytes()
    after = _regular_snapshot(path, mode=mode)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(payload) != before.st_size:
        raise ValueError(f"file changed while it was being read: {path}")
    return payload


class _DuplicateJsonKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"JSON object duplicates key {key!r}")
        result[key] = value
    return result


def _decode_json(payload: bytes, *, name: str) -> Any:
    try:
        return json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as error:
        raise ValueError(f"{name} is not unambiguous UTF-8 JSON") from error


def _http_url(value: object, *, field: str) -> str:
    raw = str(value or "")
    if raw != raw.strip() or len(raw.encode("utf-8")) > 16 * 1024:
        raise ValueError(f"{field} is not canonical")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} must not contain user information")
    return raw


def _validate_source_url(url: object, *, image_id: str) -> str:
    value = str(url or "")
    expected = CVDF_IMAGE_URL.format(split="validation", image_id=image_id)
    parsed = urlsplit(value)
    if (
        value != expected
        or parsed.scheme != "https"
        or parsed.hostname != ALLOWED_IMAGE_HOST
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{image_id}: source URL is outside the fixed HTTPS allowlist")
    return value


def _validate_fetch_url(url: object) -> str:
    value = str(url or "")
    parsed = urlsplit(value)
    prefix = "/validation/"
    suffix = ".jpg"
    if not parsed.path.startswith(prefix) or not parsed.path.endswith(suffix):
        raise ValueError("download URL is outside the fixed HTTPS allowlist")
    image_id = parsed.path[len(prefix) : -len(suffix)]
    try:
        image_id = _require_image_id(image_id)
    except ValueError as error:
        raise ValueError("download URL is outside the fixed HTTPS allowlist") from error
    return _validate_source_url(value, image_id=image_id)


def _parse_original_size(value: object, *, image_id: str) -> int | None:
    raw = str(value or "")
    if not raw:
        return None
    if not raw.isdecimal() or raw.startswith("0"):
        raise ValueError(f"{image_id}: official OriginalSize is not canonical")
    size = int(raw)
    if size < 1:
        raise ValueError(f"{image_id}: official OriginalSize must be positive")
    return size


def _parse_original_md5(value: object, *, image_id: str) -> str | None:
    raw = str(value or "")
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{image_id}: official OriginalMD5 is invalid base64") from error
    if len(decoded) != 16 or base64.b64encode(decoded).decode() != raw:
        raise ValueError(f"{image_id}: official OriginalMD5 is not canonical")
    return raw


@dataclass(frozen=True, slots=True)
class AcquisitionCandidate:
    image_id: str
    split: str
    source_url: str
    license_name: str
    license_url: str
    author: str
    author_profile_url: str
    original_landing_url: str
    title: str
    original_url: str
    original_size: int | None
    original_md5: str | None

    def as_plan_row(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "split": self.split,
            "source_url": self.source_url,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "author": self.author,
            "author_profile_url": self.author_profile_url,
            "original_landing_url": self.original_landing_url,
            "title": self.title,
            "original_url": self.original_url,
            "original_size": self.original_size,
            "original_md5": self.original_md5,
        }

    @classmethod
    def from_plan_row(cls, raw: Mapping[str, Any]) -> AcquisitionCandidate:
        if set(raw) != _PLAN_ROW_FIELDS:
            raise ValueError("acquisition plan row has an unexpected field set")
        image_id = _require_image_id(raw.get("image_id"))
        if raw.get("split") != "validation":
            raise ValueError(f"{image_id}: acquisition row is not validation")
        if raw.get("license_name") != LICENSE_NAME or raw.get("license_url") != CC_BY_2_0:
            raise ValueError(f"{image_id}: acquisition row licence is not canonical CC BY 2.0")
        for field in ("author", "title"):
            value = raw.get(field)
            if not isinstance(value, str) or value != value.strip() or not value:
                raise ValueError(f"{image_id}: {field} is not a canonical nonempty string")
        original_size = raw.get("original_size")
        if original_size is not None and (
            not isinstance(original_size, int)
            or isinstance(original_size, bool)
            or original_size < 1
        ):
            raise ValueError(f"{image_id}: original_size is not a canonical positive integer")
        original_md5 = raw.get("original_md5")
        if original_md5 is not None:
            _parse_original_md5(original_md5, image_id=image_id)
        return cls(
            image_id=image_id,
            split="validation",
            source_url=_validate_source_url(raw.get("source_url"), image_id=image_id),
            license_name=LICENSE_NAME,
            license_url=CC_BY_2_0,
            author=str(raw["author"]),
            author_profile_url=_http_url(
                raw.get("author_profile_url"), field=f"{image_id}.author_profile_url"
            ),
            original_landing_url=_http_url(
                raw.get("original_landing_url"), field=f"{image_id}.original_landing_url"
            ),
            title=str(raw["title"]),
            original_url=_http_url(raw.get("original_url"), field=f"{image_id}.original_url"),
            original_size=original_size,
            original_md5=str(original_md5) if original_md5 is not None else None,
        )


@dataclass(frozen=True, slots=True)
class CandidateResolution:
    rows: tuple[AcquisitionCandidate, ...]
    candidate_inventory_sha256: str
    official_metadata_sha256: str


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    payload: Mapping[str, Any]
    rows: tuple[AcquisitionCandidate, ...]
    plan_sha256: str

    def as_payload(self) -> dict[str, Any]:
        return dict(self.payload)

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> AcquisitionPlan:
        if set(raw) != _PLAN_FIELDS:
            raise ValueError("acquisition plan has an unexpected field set")
        if raw.get("schema") != ACQUISITION_PLAN_SCHEMA:
            raise ValueError("acquisition plan schema is unsupported")
        if raw.get("status") != "ready_for_private_download":
            raise ValueError("acquisition plan is not ready for private download")
        for field in (
            "base_public_manifest_sha256",
            "base_source_manifest_sha256",
            "base_logical_sha256",
            "base_top_up_plan_sha256",
            "frozen_certification_sha256",
            "candidate_inventory_sha256",
            "official_metadata_sha256",
        ):
            _require_sha256(raw.get(field), field=field)
        if raw.get("official_metadata_url") != OFFICIAL_METADATA_URL:
            raise ValueError("acquisition plan official metadata URL is unexpected")
        row_payloads = raw.get("rows")
        if not isinstance(row_payloads, list):
            raise ValueError("acquisition plan rows must be a list")
        rows = tuple(
            AcquisitionCandidate.from_plan_row(row)
            for row in row_payloads
            if isinstance(row, Mapping)
        )
        if len(rows) != len(row_payloads):
            raise ValueError("acquisition plan row must be an object")
        count = raw.get("candidate_count")
        if not isinstance(count, int) or isinstance(count, bool) or count != len(rows):
            raise ValueError("acquisition plan candidate_count disagrees with rows")
        ids = [row.image_id for row in rows]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("acquisition plan rows must have unique, sorted image ids")
        expected_digest = _require_sha256(raw.get("plan_sha256"), field="plan_sha256")
        unsigned = dict(raw)
        del unsigned["plan_sha256"]
        if _canonical_sha256(unsigned) != expected_digest:
            raise ValueError("acquisition plan self-digest does not verify")
        return cls(payload=dict(raw), rows=rows, plan_sha256=expected_digest)


@dataclass(frozen=True, slots=True)
class FetchResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    chunks: Iterable[bytes]


class Fetcher(Protocol):
    def __call__(self, url: str) -> FetchResponse: ...


def _read_candidate_inventory(path: Path, *, expected_count: int) -> tuple[dict[str, Any], str]:
    payload = _read_snapshot(path, maximum_bytes=MAX_CANDIDATE_INVENTORY_BYTES)
    decoded = _decode_json(payload, name="candidate inventory")
    if not isinstance(decoded, dict) or len(decoded) != expected_count:
        observed = len(decoded) if isinstance(decoded, dict) else "non-object"
        raise ValueError(
            f"candidate inventory must contain exactly {expected_count} rows; got {observed}"
        )
    result: dict[str, Any] = {}
    for raw_id, raw in decoded.items():
        image_id = _require_image_id(raw_id)
        if not isinstance(raw, Mapping) or set(raw) != _CANDIDATE_INPUT_FIELDS:
            raise ValueError(f"{image_id}: candidate inventory field set changed")
        result[image_id] = dict(raw)
    return result, hashlib.sha256(payload).hexdigest()


def _read_official_metadata(
    path: Path,
    *,
    expected_sha256: str,
    wanted: set[str],
) -> tuple[dict[str, dict[str, str]], str]:
    expected_sha256 = _require_sha256(expected_sha256, field="official metadata SHA-256")
    payload = _read_snapshot(path, maximum_bytes=64 * 1024**2)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise ValueError(
            f"official Open Images metadata digest changed: expected {expected_sha256}, got {digest}"
        )
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
        if tuple(reader.fieldnames or ()) != OFFICIAL_METADATA_COLUMNS:
            raise ValueError("official Open Images metadata columns changed")
        rows: dict[str, dict[str, str]] = {}
        for raw in reader:
            image_id = str(raw.get("ImageID") or "")
            if image_id not in wanted:
                continue
            if image_id in rows:
                raise ValueError(f"official Open Images metadata duplicates {image_id}")
            if None in raw:
                raise ValueError("official Open Images metadata row has unexpected columns")
            rows[image_id] = dict(raw)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("official Open Images metadata cannot be decoded") from error
    missing = wanted - set(rows)
    if missing:
        raise ValueError(f"candidate is missing from official metadata: {min(missing)}")
    return rows, digest


def resolve_candidate_rows(
    *,
    candidate_inventory: Path,
    official_metadata: Path,
    expected_official_metadata_sha256: str,
    base_image_ids: set[str] | frozenset[str],
    expected_inventory_count: int,
    expected_candidate_count: int,
) -> CandidateResolution:
    """Resolve the exact candidate-set difference and bind it to official metadata."""
    inventory, inventory_digest = _read_candidate_inventory(
        candidate_inventory, expected_count=expected_inventory_count
    )
    canonical_base_ids = {_require_image_id(value) for value in base_image_ids}
    missing_base = canonical_base_ids - set(inventory)
    if missing_base:
        raise ValueError(
            "candidate inventory does not contain every authenticated base image id: "
            f"{min(missing_base)}"
        )
    candidate_ids = set(inventory) - canonical_base_ids
    if len(candidate_ids) != expected_candidate_count:
        raise ValueError(
            f"remaining candidate set has {len(candidate_ids)} rows; "
            f"expected exactly {expected_candidate_count}"
        )
    official, metadata_digest = _read_official_metadata(
        official_metadata,
        expected_sha256=expected_official_metadata_sha256,
        wanted=candidate_ids,
    )
    resolved: list[AcquisitionCandidate] = []
    bindings = {
        "license_url": "License",
        "author": "Author",
        "author_profile_url": "AuthorProfileURL",
        "original_landing_url": "OriginalLandingURL",
        "title": "Title",
    }
    for image_id in sorted(candidate_ids):
        candidate = inventory[image_id]
        metadata = official[image_id]
        if metadata["Subset"] != "validation":
            raise ValueError(f"{image_id}: official metadata split is not validation")
        if metadata["License"] != CC_BY_2_0 or candidate.get("license_url") != CC_BY_2_0:
            raise ValueError(f"{image_id}: candidate licence is not canonical CC BY 2.0")
        disagreements = [
            local for local, remote in bindings.items() if candidate.get(local) != metadata[remote]
        ]
        if disagreements:
            raise ValueError(
                f"{image_id}: candidate attribution disagrees with official metadata: "
                + ", ".join(disagreements)
            )
        author = str(candidate["author"])
        title = str(candidate["title"])
        if not author or author != author.strip() or title != title.strip():
            raise ValueError(f"{image_id}: attribution strings are not canonical")
        resolved.append(
            AcquisitionCandidate(
                image_id=image_id,
                split="validation",
                source_url=_validate_source_url(
                    CVDF_IMAGE_URL.format(split="validation", image_id=image_id),
                    image_id=image_id,
                ),
                license_name=LICENSE_NAME,
                license_url=CC_BY_2_0,
                author=author,
                author_profile_url=_http_url(
                    candidate["author_profile_url"], field=f"{image_id}.author_profile_url"
                ),
                original_landing_url=_http_url(
                    candidate["original_landing_url"],
                    field=f"{image_id}.original_landing_url",
                ),
                title=title,
                original_url=_http_url(metadata["OriginalURL"], field=f"{image_id}.OriginalURL"),
                original_size=_parse_original_size(metadata["OriginalSize"], image_id=image_id),
                original_md5=_parse_original_md5(metadata["OriginalMD5"], image_id=image_id),
            )
        )
    return CandidateResolution(
        rows=tuple(resolved),
        candidate_inventory_sha256=inventory_digest,
        official_metadata_sha256=metadata_digest,
    )


def resolve_acquisition_plan(
    *,
    base_partition: Path,
    candidate_inventory: Path,
    official_metadata: Path = DEFAULT_OFFICIAL_METADATA,
    expected_official_metadata_sha256: str = EXPECTED_OFFICIAL_METADATA_SHA256,
    expected_base_manifest_sha256: str = EXPECTED_PUBLIC_CORPUS_V1_MANIFEST_SHA256,
    base_spec: BasePartitionSpec = BasePartitionSpec(),
    top_up_spec: TopUpSpec = TopUpSpec(),
) -> tuple[AcquisitionPlan, frozenset[str]]:
    """Authenticate public-corpus-v1 and resolve its exact unacquired 580 rows."""
    base = verify_base_partition(
        base_partition,
        expected_manifest_sha256=expected_base_manifest_sha256,
        spec=base_spec,
    )
    plan_path = base.root / "public" / "top-up-plan.json"
    plan_bytes = _read_snapshot(plan_path, maximum_bytes=2 * 1024**2)
    sealed_plan = _decode_json(plan_bytes, name="sealed public top-up plan")
    if not isinstance(sealed_plan, Mapping):
        raise ValueError("sealed public top-up plan must be an object")
    expected_plan = {
        "schema": "triage-open-images-top-up-plan-v1",
        "status": "awaiting-local-manifest",
        "required_rows": top_up_spec.count,
        "destination_role": "training",
        "base_manifest_sha256": base.source.manifest_sha256,
        "base_logical_sha256": base.source.logical_sha256,
        "frozen_certification_sha256": base.certification_sha256,
    }
    disagreements = [key for key, value in expected_plan.items() if sealed_plan.get(key) != value]
    if disagreements:
        raise ValueError(
            "sealed public top-up plan disagrees with authenticated base: "
            + ", ".join(disagreements)
        )
    resolution = resolve_candidate_rows(
        candidate_inventory=candidate_inventory,
        official_metadata=official_metadata,
        expected_official_metadata_sha256=expected_official_metadata_sha256,
        base_image_ids={row.image_id for row in base.rows},
        expected_inventory_count=base_spec.source_count + top_up_spec.candidate_count,
        expected_candidate_count=top_up_spec.candidate_count,
    )
    unsigned: dict[str, Any] = {
        "schema": ACQUISITION_PLAN_SCHEMA,
        "status": "ready_for_private_download",
        "base_public_manifest_sha256": base.manifest_sha256,
        "base_source_manifest_sha256": base.source.manifest_sha256,
        "base_logical_sha256": base.source.logical_sha256,
        "base_top_up_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "frozen_certification_sha256": base.certification_sha256,
        "candidate_inventory_sha256": resolution.candidate_inventory_sha256,
        "official_metadata_sha256": resolution.official_metadata_sha256,
        "official_metadata_url": OFFICIAL_METADATA_URL,
        "candidate_count": len(resolution.rows),
        "rows": [row.as_plan_row() for row in resolution.rows],
    }
    plan = AcquisitionPlan.from_payload({**unsigned, "plan_sha256": _canonical_sha256(unsigned)})
    return plan, frozenset(row.content_sha256 for row in base.rows)


def _headers(raw: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip().lower()
        if name in normalized:
            raise ValueError(f"response duplicates header {name}")
        normalized[name] = str(value).strip()
    return normalized


def _decode_jpeg(payload: bytes, *, image_id: str) -> None:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "JPEG":
                raise ValueError(f"{image_id}: response is not a JPEG")
            width, height = image.size
            image.verify()
        if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
            raise ValueError(f"{image_id}: JPEG dimensions are invalid or unbounded")
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"{image_id}: response cannot be decoded as JPEG") from error


def _validated_response(
    candidate: AcquisitionCandidate, response: FetchResponse
) -> tuple[bytes, str]:
    _validate_source_url(candidate.source_url, image_id=candidate.image_id)
    if 300 <= response.status_code < 400:
        raise ValueError(f"{candidate.image_id}: redirects are forbidden")
    if response.status_code != 200:
        raise ValueError(f"{candidate.image_id}: HTTP status {response.status_code} is not 200")
    if response.url != candidate.source_url:
        raise ValueError(f"{candidate.image_id}: response URL is outside the allowlisted source")
    _validate_source_url(response.url, image_id=candidate.image_id)
    headers = _headers(response.headers)
    content_encoding = headers.get("content-encoding")
    if content_encoding not in {None, "identity"}:
        raise ValueError(f"{candidate.image_id}: encoded HTTP bodies are forbidden")
    raw_length = headers.get("content-length")
    if raw_length is None or not raw_length.isdecimal() or raw_length.startswith("0"):
        raise ValueError(f"{candidate.image_id}: canonical Content-Length is required")
    expected_length = int(raw_length)
    if expected_length < 1 or expected_length > MAX_IMAGE_BYTES:
        raise ValueError(f"{candidate.image_id}: Content-Length exceeds the 64 MiB limit")
    payload = bytearray()
    for chunk in response.chunks:
        if not isinstance(chunk, bytes) or not chunk:
            raise ValueError(f"{candidate.image_id}: response yielded an invalid byte chunk")
        payload.extend(chunk)
        if len(payload) > expected_length or len(payload) > MAX_IMAGE_BYTES:
            raise ValueError(f"{candidate.image_id}: response exceeds Content-Length")
    if len(payload) != expected_length:
        raise ValueError(f"{candidate.image_id}: response is truncated against Content-Length")
    body = bytes(payload)
    body_md5 = _md5_digest(body)
    content_md5 = base64.b64encode(body_md5).decode()
    supplied_md5 = headers.get("content-md5")
    if supplied_md5 is not None:
        try:
            decoded = base64.b64decode(supplied_md5, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError(f"{candidate.image_id}: response Content-MD5 is invalid") from error
        if len(decoded) != 16 or not hmac.compare_digest(decoded, body_md5):
            raise ValueError(f"{candidate.image_id}: response Content-MD5 does not match bytes")
        if base64.b64encode(decoded).decode() != supplied_md5:
            raise ValueError(f"{candidate.image_id}: response Content-MD5 is not canonical")
    _decode_jpeg(body, image_id=candidate.image_id)
    return body, content_md5


def _private_directory(path: Path, *, create: bool) -> Path:
    absolute = path.absolute()
    if path.is_symlink():
        raise ValueError(f"private directory must not be a symlink: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    path = path.resolve(strict=True)
    if path != absolute:
        raise ValueError(f"private directory must not traverse symlink ancestors: {absolute}")
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError(f"private directory mode must be 700: {path}")
    return path


def _write_create_only(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    _private_directory(path.parent, create=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite private acquisition file: {path}") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, mode)
    _regular_snapshot(path, mode=mode)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _HeldLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def __enter__(self) -> _HeldLock:
        _private_directory(self.path.parent, create=True)
        if self.path.is_symlink():
            raise ValueError("acquisition lock must not be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError("acquisition lock must not be a symlink") from error
            raise
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            os.close(descriptor)
            raise ValueError("acquisition lock must be a private single-link file")
        os.fchmod(descriptor, 0o600)
        self.handle = os.fdopen(descriptor, "a+b", buffering=0)
        metadata = os.fstat(self.handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            self.handle.close()
            raise ValueError("acquisition lock must be a private single-link file")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args: object) -> None:
        assert self.handle is not None
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _wal_path(workspace: Path) -> Path:
    return workspace / "download.wal.jsonl"


def _ensure_wal(workspace: Path) -> Path:
    path = _wal_path(workspace)
    if not path.exists():
        _write_create_only(path, b"", mode=0o600)
    _regular_snapshot(path, mode=0o600)
    return path


def _ensure_plan_checkpoint(workspace: Path, plan: AcquisitionPlan) -> None:
    path = workspace / "acquisition-plan.json"
    expected = _canonical_bytes(plan.as_payload())
    if not path.exists():
        _write_create_only(path, expected, mode=0o600)
        _fsync_directory(workspace)
        return
    observed = _read_snapshot(
        path,
        maximum_bytes=MAX_ACQUISITION_MANIFEST_BYTES,
        mode=0o600,
    )
    if observed != expected:
        raise ValueError("private acquisition workspace belongs to another plan")


def _repair_truncated_wal_tail(
    path: Path, payload: bytes, *, expected_snapshot: os.stat_result
) -> bytes:
    if not payload or payload.endswith(b"\n"):
        return payload
    durable_end = payload.rfind(b"\n") + 1
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("download WAL changed before tail repair") from error
        raise
    try:
        opened = os.fstat(descriptor)
        expected_identity = (
            expected_snapshot.st_dev,
            expected_snapshot.st_ino,
            expected_snapshot.st_size,
            expected_snapshot.st_mtime_ns,
            expected_snapshot.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if (
            opened_identity != expected_identity
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise ValueError("download WAL changed before tail repair")
        os.ftruncate(descriptor, durable_end)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    repaired = _regular_snapshot(path, mode=0o600)
    if (repaired.st_dev, repaired.st_ino, repaired.st_size) != (
        expected_snapshot.st_dev,
        expected_snapshot.st_ino,
        durable_end,
    ):
        raise ValueError("download WAL changed during tail repair")
    return payload[:durable_end]


def _validate_wal_record(
    raw: Mapping[str, Any], *, candidate: AcquisitionCandidate, plan: AcquisitionPlan
) -> dict[str, Any]:
    if set(raw) != _WAL_FIELDS:
        raise ValueError("download WAL row has an unexpected field set")
    if raw.get("schema") != ACQUISITION_WAL_SCHEMA or raw.get("plan_sha256") != plan.plan_sha256:
        raise ValueError("download WAL row belongs to another acquisition plan")
    if raw.get("image_id") != candidate.image_id or raw.get("source_url") != candidate.source_url:
        raise ValueError("download WAL row disagrees with its planned candidate")
    if (
        raw.get("official_original_size") != candidate.original_size
        or raw.get("official_original_md5") != candidate.original_md5
    ):
        raise ValueError("download WAL row disagrees with official metadata")
    _require_sha256(raw.get("content_sha256"), field="WAL content_sha256")
    byte_count = raw.get("bytes")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or not 1 <= byte_count <= MAX_IMAGE_BYTES
    ):
        raise ValueError("download WAL row has an invalid byte count")
    retrieved_at = raw.get("retrieved_at")
    if (
        not isinstance(retrieved_at, str)
        or not retrieved_at
        or retrieved_at != retrieved_at.strip()
    ):
        raise ValueError("download WAL row has a noncanonical retrieval timestamp")
    content_md5 = raw.get("content_md5")
    _parse_original_md5(content_md5, image_id=candidate.image_id)
    return dict(raw)


def _read_wal(
    *,
    workspace: Path,
    plan: AcquisitionPlan,
    base_content_sha256: frozenset[str],
) -> dict[str, dict[str, Any]]:
    wal = _ensure_wal(workspace)
    before = _regular_snapshot(wal, mode=0o600)
    size = before.st_size
    if size > MAX_WAL_BYTES:
        raise ValueError("download WAL exceeds its bounded size")
    payload = wal.read_bytes()
    after = _regular_snapshot(wal, mode=0o600)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if len(payload) != size or before_identity != after_identity:
        raise ValueError("download WAL changed while it was read")
    payload = _repair_truncated_wal_tail(wal, payload, expected_snapshot=after)
    by_candidate = {candidate.image_id: candidate for candidate in plan.rows}
    records: dict[str, dict[str, Any]] = {}
    hashes: set[str] = set()
    for line_number, line in enumerate(payload.splitlines(keepends=True), start=1):
        if not line.strip():
            raise ValueError(f"download WAL line {line_number} is blank")
        raw = _decode_json(line, name=f"download WAL line {line_number}")
        if not isinstance(raw, Mapping) or _canonical_bytes(raw) != line:
            raise ValueError(f"download WAL line {line_number} is not canonical")
        candidate = by_candidate.get(str(raw.get("image_id") or ""))
        if candidate is None:
            raise ValueError("download WAL contains a candidate outside its plan")
        record = _validate_wal_record(raw, candidate=candidate, plan=plan)
        if candidate.image_id in records:
            raise ValueError(f"download WAL duplicates candidate {candidate.image_id}")
        digest = str(record["content_sha256"])
        if digest in hashes:
            raise ValueError("download WAL contains duplicate content bytes")
        if digest in base_content_sha256:
            raise ValueError("download WAL content bytes overlap the authenticated base")
        image_path = workspace / "images" / f"{candidate.image_id}.jpg"
        image_payload = _read_snapshot(image_path, maximum_bytes=MAX_IMAGE_BYTES, mode=0o600)
        if (
            len(image_payload) != record["bytes"]
            or hashlib.sha256(image_payload).hexdigest() != digest
        ):
            raise ValueError(f"{candidate.image_id}: staged image changed against the WAL")
        if base64.b64encode(_md5_digest(image_payload)).decode() != record["content_md5"]:
            raise ValueError(f"{candidate.image_id}: staged image MD5 changed against the WAL")
        _decode_jpeg(image_payload, image_id=candidate.image_id)
        records[candidate.image_id] = record
        hashes.add(digest)
    expected_files = {f"{image_id}.jpg" for image_id in records}
    planned_files = {f"{candidate.image_id}.jpg" for candidate in plan.rows}
    image_root = workspace / "images"
    children = tuple(image_root.iterdir())
    invalid_children = [path.name for path in children if path.is_symlink() or not path.is_file()]
    if invalid_children:
        raise ValueError(f"private staging contains a symlink or non-file: {min(invalid_children)}")
    actual_files = {path.name for path in children}
    orphaned = actual_files - expected_files
    for name in sorted(orphaned):
        path = image_root / name
        if name not in planned_files:
            raise ValueError(f"private staging contains an unexpected file: {name}")
        # A crash can leave a planned pixel after its fsync but before the WAL fsync.
        # It is not trusted, so discard only this process-owned staging orphan.
        _regular_snapshot(path, mode=0o600)
        path.unlink()
    return records


def _append_wal(path: Path, record: Mapping[str, Any]) -> None:
    payload = _canonical_bytes(record)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        _regular_snapshot(path, mode=0o600)


def _source_row(
    candidate: AcquisitionCandidate,
    record: Mapping[str, Any],
    *,
    output: Path,
) -> dict[str, Any]:
    return {
        "image_id": candidate.image_id,
        "split": "validation",
        "s3_url": candidate.source_url,
        "local_path": str((output / "images" / f"{candidate.image_id}.jpg").resolve()),
        "license_name": LICENSE_NAME,
        "license_url": CC_BY_2_0,
        "author": candidate.author,
        "author_profile_url": candidate.author_profile_url,
        "original_landing_url": candidate.original_landing_url,
        "title": candidate.title,
        "retrieved_at": record["retrieved_at"],
        "content_sha256": record["content_sha256"],
        "bytes": record["bytes"],
        "official_original_url": candidate.original_url,
        "official_original_size": candidate.original_size,
        "official_original_md5": candidate.original_md5,
        "response_content_md5": record["content_md5"],
    }


def _manifest(
    plan: AcquisitionPlan, records: Mapping[str, Mapping[str, Any]], output: Path
) -> dict[str, Any]:
    by_id = {candidate.image_id: candidate for candidate in plan.rows}
    rows = [
        _source_row(by_id[image_id], records[image_id], output=output)
        for image_id in sorted(records)
    ]
    payload: dict[str, Any] = {
        "schema": ACQUISITION_MANIFEST_SCHEMA,
        "status": "complete_private_source",
        "plan_sha256": plan.plan_sha256,
        "base_public_manifest_sha256": plan.payload["base_public_manifest_sha256"],
        "base_source_manifest_sha256": plan.payload["base_source_manifest_sha256"],
        "base_logical_sha256": plan.payload["base_logical_sha256"],
        "base_top_up_plan_sha256": plan.payload["base_top_up_plan_sha256"],
        "frozen_certification_sha256": plan.payload["frozen_certification_sha256"],
        "candidate_inventory_sha256": plan.payload["candidate_inventory_sha256"],
        "official_metadata_sha256": plan.payload["official_metadata_sha256"],
        "official_metadata_url": plan.payload["official_metadata_url"],
        "row_count": len(rows),
        "rows": rows,
    }
    return payload


def _seal(manifest: Mapping[str, Any], *, manifest_sha256: str) -> dict[str, Any]:
    unsigned = {
        "schema": ACQUISITION_SEAL_SCHEMA,
        "manifest_sha256": manifest_sha256,
        "plan_sha256": manifest["plan_sha256"],
        "row_count": manifest["row_count"],
        "files": {
            "manifest.json": manifest_sha256,
            **{f"images/{row['image_id']}.jpg": row["content_sha256"] for row in manifest["rows"]},
        },
    }
    return {**unsigned, "seal_sha256": _canonical_sha256(unsigned)}


def _copy_private_snapshot(source: Path, destination: Path, *, expected_sha256: str) -> None:
    payload = _read_snapshot(source, maximum_bytes=MAX_IMAGE_BYTES, mode=0o600)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("staged image changed before immutable publish")
    _write_create_only(destination, payload, mode=0o600)


def _publish(
    *,
    workspace: Path,
    output: Path,
    plan: AcquisitionPlan,
    records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    output = output.absolute()
    if output.is_symlink():
        raise ValueError("acquired source output must not be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved_parent = output.parent.resolve(strict=True)
    if resolved_parent != output.parent:
        raise ValueError("acquired source output must not traverse symlink ancestors")
    expected_manifest = _manifest(plan, records, output)
    expected_payload = _canonical_bytes(expected_manifest)
    expected_digest = hashlib.sha256(expected_payload).hexdigest()
    lock_name = f".{output.name}.publish.lock"
    with _HeldLock(output.parent / lock_name):
        if output.exists():
            verified = verify_acquired_source(
                output / "manifest.json",
                output / "images",
                expected_manifest_sha256=expected_digest,
                expected_count=len(plan.rows),
            )
            if verified.manifest_sha256 != expected_digest:
                raise AssertionError("idempotent acquired source verification disagrees")
            return expected_manifest
        publishing = Path(tempfile.mkdtemp(prefix=f".{output.name}.publishing-", dir=output.parent))
        os.chmod(publishing, 0o700)
        try:
            images = _private_directory(publishing / "images", create=True)
            for row in expected_manifest["rows"]:
                image_id = str(row["image_id"])
                _copy_private_snapshot(
                    workspace / "images" / f"{image_id}.jpg",
                    images / f"{image_id}.jpg",
                    expected_sha256=str(row["content_sha256"]),
                )
            _write_create_only(publishing / "manifest.json", expected_payload, mode=0o600)
            _write_create_only(
                publishing / "source-seal.json",
                _canonical_bytes(_seal(expected_manifest, manifest_sha256=expected_digest)),
                mode=0o600,
            )
            _fsync_directory(images)
            _fsync_directory(publishing)
            if output.exists():
                raise ValueError("acquired source output appeared during create-only publish")
            os.rename(publishing, output)
            _fsync_directory(output.parent)
        except BaseException:
            if publishing.exists():
                shutil.rmtree(publishing)
            raise
    verify_acquired_source(
        output / "manifest.json",
        output / "images",
        expected_manifest_sha256=expected_digest,
        expected_count=len(plan.rows),
        expected_plan=plan,
    )
    return expected_manifest


def acquire_from_plan(
    *,
    plan: AcquisitionPlan,
    base_content_sha256: frozenset[str],
    workspace: Path,
    output: Path,
    fetcher: Fetcher,
    retrieved_at: Callable[[], str],
) -> dict[str, Any]:
    """Resume an authenticated plan and atomically publish only after all rows verify."""
    workspace = _private_directory(workspace, create=True)
    _private_directory(workspace / "images", create=True)
    with _HeldLock(workspace / ".download.lock"):
        _ensure_plan_checkpoint(workspace, plan)
        records = _read_wal(
            workspace=workspace,
            plan=plan,
            base_content_sha256=base_content_sha256,
        )
        seen_hashes = {str(record["content_sha256"]) for record in records.values()}
        for index, candidate in enumerate(plan.rows):
            if candidate.image_id in records:
                continue
            response = fetcher(candidate.source_url)
            payload, content_md5 = _validated_response(candidate, response)
            digest = hashlib.sha256(payload).hexdigest()
            if digest in base_content_sha256:
                raise ValueError(f"{candidate.image_id}: downloaded bytes overlap the base corpus")
            if digest in seen_hashes:
                raise ValueError(f"{candidate.image_id}: downloaded duplicate content bytes")
            timestamp = retrieved_at()
            if not isinstance(timestamp, str) or not timestamp or timestamp != timestamp.strip():
                raise ValueError("retrieval clock returned a noncanonical timestamp")
            image_path = workspace / "images" / f"{candidate.image_id}.jpg"
            _write_create_only(image_path, payload, mode=0o600)
            _fsync_directory(image_path.parent)
            record = {
                "schema": ACQUISITION_WAL_SCHEMA,
                "plan_sha256": plan.plan_sha256,
                "image_id": candidate.image_id,
                "source_url": candidate.source_url,
                "retrieved_at": timestamp,
                "content_sha256": digest,
                "bytes": len(payload),
                "content_md5": content_md5,
                "official_original_size": candidate.original_size,
                "official_original_md5": candidate.original_md5,
            }
            _append_wal(_wal_path(workspace), record)
            records[candidate.image_id] = record
            seen_hashes.add(digest)
            if index % 25 == 0:
                _assert_memory_limit()
        if len(records) != len(plan.rows):
            raise AssertionError("download loop did not exhaust its exact plan")
        records = _read_wal(
            workspace=workspace,
            plan=plan,
            base_content_sha256=base_content_sha256,
        )
        return _publish(workspace=workspace, output=output, plan=plan, records=records)


def acquire_top_up_source(
    *,
    base_partition: Path,
    candidate_inventory: Path,
    official_metadata: Path,
    workspace: Path,
    output: Path,
    fetcher: Fetcher,
    retrieved_at: Callable[[], str],
) -> dict[str, Any]:
    plan, base_hashes = resolve_acquisition_plan(
        base_partition=base_partition,
        candidate_inventory=candidate_inventory,
        official_metadata=official_metadata,
    )
    return acquire_from_plan(
        plan=plan,
        base_content_sha256=base_hashes,
        workspace=workspace,
        output=output,
        fetcher=fetcher,
        retrieved_at=retrieved_at,
    )


def _manifest_source_row(raw: Mapping[str, Any], *, images_root: Path) -> dict[str, Any]:
    allowed = frozenset(SOURCE_COLUMNS) | _SOURCE_ROW_EXTRA_FIELDS
    if set(raw) != allowed:
        raise ValueError("acquired source row has an unexpected field set")
    image_id = _require_image_id(raw.get("image_id"))
    if raw.get("official_original_size") is not None and (
        not isinstance(raw.get("official_original_size"), int)
        or isinstance(raw.get("official_original_size"), bool)
        or int(raw["official_original_size"]) < 1
    ):
        raise ValueError(f"{image_id}: official original size is invalid")
    if raw.get("official_original_md5") is not None:
        _parse_original_md5(raw["official_original_md5"], image_id=image_id)
    _parse_original_md5(raw.get("response_content_md5"), image_id=image_id)
    _http_url(raw.get("official_original_url"), field=f"{image_id}.official_original_url")
    expected_path = str((images_root / f"{image_id}.jpg").resolve())
    if raw.get("local_path") != expected_path:
        raise ValueError(f"{image_id}: acquired source local_path is not canonical")
    return {column: raw[column] for column in SOURCE_COLUMNS}


def verify_acquired_source(
    manifest_path: Path,
    images_root: Path,
    *,
    expected_manifest_sha256: str,
    expected_count: int,
    expected_plan: AcquisitionPlan | None = None,
) -> VerifiedCorpus:
    """Authenticate the canonical private source tree and every downloaded pixel."""
    expected_manifest_sha256 = _require_sha256(
        expected_manifest_sha256, field="acquired source manifest SHA-256"
    )
    if manifest_path.name != "manifest.json" or images_root.name != "images":
        raise ValueError("acquired source must use exact manifest.json and images paths")
    root = manifest_path.parent
    if images_root.parent != root:
        raise ValueError("acquired source manifest and image directory must share one root")
    root = _private_directory(root, create=False)
    images_root = _private_directory(images_root, create=False)
    manifest_path = root / "manifest.json"
    manifest_payload = _read_snapshot(
        manifest_path, maximum_bytes=MAX_ACQUISITION_MANIFEST_BYTES, mode=0o600
    )
    observed_digest = hashlib.sha256(manifest_payload).hexdigest()
    if observed_digest != expected_manifest_sha256:
        raise ValueError(
            "acquired source manifest digest changed: "
            f"expected {expected_manifest_sha256}, got {observed_digest}"
        )
    decoded = _decode_json(manifest_payload, name="acquired source manifest")
    if not isinstance(decoded, dict) or _canonical_bytes(decoded) != manifest_payload:
        raise ValueError("acquired source manifest is not canonical")
    expected_manifest_fields = {
        "schema",
        "status",
        "plan_sha256",
        "base_public_manifest_sha256",
        "base_source_manifest_sha256",
        "base_logical_sha256",
        "base_top_up_plan_sha256",
        "frozen_certification_sha256",
        "candidate_inventory_sha256",
        "official_metadata_sha256",
        "official_metadata_url",
        "row_count",
        "rows",
    }
    if set(decoded) != expected_manifest_fields:
        raise ValueError("acquired source manifest has an unexpected field set")
    if (
        decoded.get("schema") != ACQUISITION_MANIFEST_SCHEMA
        or decoded.get("status") != "complete_private_source"
    ):
        raise ValueError("acquired source manifest is not complete")
    for field in (
        "plan_sha256",
        "base_public_manifest_sha256",
        "base_source_manifest_sha256",
        "base_logical_sha256",
        "base_top_up_plan_sha256",
        "frozen_certification_sha256",
        "candidate_inventory_sha256",
        "official_metadata_sha256",
    ):
        _require_sha256(decoded.get(field), field=field)
    if decoded.get("official_metadata_url") != OFFICIAL_METADATA_URL:
        raise ValueError("acquired source official metadata URL is unexpected")
    if expected_plan is not None:
        expected_lineage = {
            "plan_sha256": expected_plan.plan_sha256,
            "base_public_manifest_sha256": expected_plan.payload["base_public_manifest_sha256"],
            "base_source_manifest_sha256": expected_plan.payload["base_source_manifest_sha256"],
            "base_logical_sha256": expected_plan.payload["base_logical_sha256"],
            "base_top_up_plan_sha256": expected_plan.payload["base_top_up_plan_sha256"],
            "frozen_certification_sha256": expected_plan.payload["frozen_certification_sha256"],
            "candidate_inventory_sha256": expected_plan.payload["candidate_inventory_sha256"],
            "official_metadata_sha256": expected_plan.payload["official_metadata_sha256"],
            "official_metadata_url": expected_plan.payload["official_metadata_url"],
            "row_count": len(expected_plan.rows),
        }
        disagreements = [
            field for field, expected in expected_lineage.items() if decoded.get(field) != expected
        ]
        if disagreements:
            raise ValueError(
                "acquired source disagrees with the freshly authenticated acquisition plan: "
                + ", ".join(disagreements)
            )
    rows = decoded.get("rows")
    if (
        not isinstance(rows, list)
        or decoded.get("row_count") != expected_count
        or len(rows) != expected_count
    ):
        raise ValueError(f"acquired source must contain exactly {expected_count} rows")
    image_names = {
        f"{str(row.get('image_id') or '')}.jpg" for row in rows if isinstance(row, Mapping)
    }
    actual_names = {path.name for path in images_root.iterdir()}
    if len(image_names) != expected_count or actual_names != image_names:
        raise ValueError("acquired source contains unexpected or missing image entries")
    verified_rows = []
    ids: list[str] = []
    hashes: list[str] = []
    planned_by_id = (
        {row.image_id: row for row in expected_plan.rows} if expected_plan is not None else {}
    )
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError("acquired source row must be an object")
        source = _manifest_source_row(raw, images_root=images_root)
        image_id = str(source["image_id"])
        if expected_plan is not None:
            planned = planned_by_id.get(image_id)
            if planned is None:
                raise ValueError(f"{image_id}: acquired row is outside the authenticated plan")
            planned_fields = {
                "split": planned.split,
                "s3_url": planned.source_url,
                "license_name": planned.license_name,
                "license_url": planned.license_url,
                "author": planned.author,
                "author_profile_url": planned.author_profile_url,
                "original_landing_url": planned.original_landing_url,
                "title": planned.title,
                "official_original_url": planned.original_url,
                "official_original_size": planned.original_size,
                "official_original_md5": planned.original_md5,
            }
            disagreements = [
                field for field, expected in planned_fields.items() if raw.get(field) != expected
            ]
            if disagreements:
                raise ValueError(
                    f"{image_id}: acquired row disagrees with authenticated plan: "
                    + ", ".join(disagreements)
                )
        image_path = images_root / f"{image_id}.jpg"
        _regular_snapshot(image_path, mode=0o600)
        row = _verified_image(source, images_root=images_root, source_corpus="top-up")
        if base64.b64encode(_md5_digest(image_path.read_bytes())).decode() != raw.get(
            "response_content_md5"
        ):
            raise ValueError(f"{image_id}: response MD5 changed against manifest")
        verified_rows.append(row)
        ids.append(row.image_id)
        hashes.append(row.content_sha256)
        if index % 100 == 0:
            _assert_memory_limit()
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("acquired source ids are not unique and sorted")
    if len(hashes) != len(set(hashes)):
        raise ValueError("acquired source contains duplicate content hashes")
    seal_payload = _read_snapshot(
        root / "source-seal.json", maximum_bytes=MAX_ACQUISITION_MANIFEST_BYTES, mode=0o600
    )
    seal = _decode_json(seal_payload, name="acquired source seal")
    if not isinstance(seal, dict) or _canonical_bytes(seal) != seal_payload:
        raise ValueError("acquired source seal is not canonical")
    if set(seal) != {
        "schema",
        "manifest_sha256",
        "plan_sha256",
        "row_count",
        "files",
        "seal_sha256",
    }:
        raise ValueError("acquired source seal has an unexpected field set")
    if seal.get("schema") != ACQUISITION_SEAL_SCHEMA:
        raise ValueError("acquired source seal schema is unsupported")
    unsigned_seal = dict(seal)
    seal_digest = _require_sha256(unsigned_seal.pop("seal_sha256"), field="seal_sha256")
    if _canonical_sha256(unsigned_seal) != seal_digest:
        raise ValueError("acquired source seal self-digest does not verify")
    expected_files = {
        "manifest.json": observed_digest,
        **{f"images/{row.image_id}.jpg": row.content_sha256 for row in verified_rows},
    }
    if (
        seal.get("manifest_sha256") != observed_digest
        or seal.get("plan_sha256") != decoded["plan_sha256"]
        or seal.get("row_count") != expected_count
        or seal.get("files") != expected_files
    ):
        raise ValueError("acquired source seal disagrees with manifest or pixels")
    if {path.name for path in root.iterdir()} != {"images", "manifest.json", "source-seal.json"}:
        raise ValueError("acquired source root contains unexpected files")
    for row in verified_rows:
        payload = _read_snapshot(row.local_path, maximum_bytes=MAX_IMAGE_BYTES, mode=0o600)
        if len(payload) != row.source["bytes"]:
            raise ValueError("acquired source pixel changed during final verification")
        if hashlib.sha256(payload).hexdigest() != row.content_sha256:
            raise ValueError("acquired source pixel changed during final verification")
    if (
        _read_snapshot(
            manifest_path,
            maximum_bytes=MAX_ACQUISITION_MANIFEST_BYTES,
            mode=0o600,
        )
        != manifest_payload
    ):
        raise ValueError("acquired source manifest changed during verification")
    if (
        _read_snapshot(
            root / "source-seal.json",
            maximum_bytes=MAX_ACQUISITION_MANIFEST_BYTES,
            mode=0o600,
        )
        != seal_payload
    ):
        raise ValueError("acquired source seal changed during verification")
    if {path.name for path in root.iterdir()} != {"images", "manifest.json", "source-seal.json"}:
        raise ValueError("acquired source root changed during verification")
    if {path.name for path in images_root.iterdir()} != image_names:
        raise ValueError("acquired source image set changed during verification")
    return VerifiedCorpus(
        rows=tuple(verified_rows),
        manifest_path=manifest_path,
        images_root=images_root,
        manifest_sha256=observed_digest,
        logical_sha256=_logical_corpus_sha256(verified_rows),
        source_corpus="top-up",
    )


def review_acquired_source(
    manifest_path: Path,
    images_root: Path,
    *,
    expected_count: int = TopUpSpec().candidate_count,
    expected_plan: AcquisitionPlan | None = None,
) -> str:
    """Verify a completed source without trusting a supplied digest, then return its SHA."""
    payload = _read_snapshot(
        manifest_path, maximum_bytes=MAX_ACQUISITION_MANIFEST_BYTES, mode=0o600
    )
    digest = hashlib.sha256(payload).hexdigest()
    verified = verify_acquired_source(
        manifest_path,
        images_root,
        expected_manifest_sha256=digest,
        expected_count=expected_count,
        expected_plan=expected_plan,
    )
    if verified.manifest_sha256 != digest:
        raise AssertionError("reviewed source digest changed during verification")
    return digest


class HttpxFetcher:
    """Production HTTP boundary: no redirects, proxies, or ambient environment."""

    def __call__(self, url: str) -> FetchResponse:
        url = _validate_fetch_url(url)
        import httpx

        with (
            httpx.Client(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(60.0, connect=15.0),
            ) as client,
            client.stream(
                "GET",
                url,
                headers={"Accept": "image/jpeg", "Accept-Encoding": "identity"},
            ) as response,
        ):
            raw_length = response.headers.get("content-length", "")
            should_read = (
                response.status_code == 200
                and raw_length.isdecimal()
                and not raw_length.startswith("0")
                and int(raw_length) <= MAX_IMAGE_BYTES
            )
            chunks: list[bytes] = []
            observed = 0
            if should_read:
                for chunk in response.iter_bytes():
                    observed += len(chunk)
                    if observed > MAX_IMAGE_BYTES or observed > int(raw_length):
                        raise ValueError("HTTP response exceeded its bounded Content-Length")
                    chunks.append(chunk)
            return FetchResponse(
                url=str(response.url),
                status_code=response.status_code,
                headers=dict(response.headers),
                chunks=tuple(chunks),
            )


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "download"):
        command = subparsers.add_parser(name)
        command.add_argument("--base-partition", type=Path, default=DEFAULT_BASE_PARTITION)
        command.add_argument(
            "--candidate-inventory", type=Path, default=DEFAULT_CANDIDATE_INVENTORY
        )
        command.add_argument("--official-metadata", type=Path, default=DEFAULT_OFFICIAL_METADATA)
        if name == "download":
            command.add_argument("--workspace", type=Path, default=DEFAULT_ACQUISITION_WORKSPACE)
            command.add_argument("--output", type=Path, default=DEFAULT_ACQUIRED_SOURCE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "resolve":
        plan, _base_hashes = resolve_acquisition_plan(
            base_partition=args.base_partition,
            candidate_inventory=args.candidate_inventory,
            official_metadata=args.official_metadata,
        )
        print(f"exact candidates={len(plan.rows)}; acquisition-plan={plan.plan_sha256}")
        return 0
    _pipeline_lock = acquire_recovery_pipeline_lock()
    manifest = acquire_top_up_source(
        base_partition=args.base_partition,
        candidate_inventory=args.candidate_inventory,
        official_metadata=args.official_metadata,
        workspace=args.workspace,
        output=args.output,
        fetcher=HttpxFetcher(),
        retrieved_at=_utc_now,
    )
    print(
        f"private acquired rows={manifest['row_count']}; "
        f"review next: {args.output / 'manifest.json'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
