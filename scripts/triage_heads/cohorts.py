"""Private, deterministic cohort construction for local triage-head training.

The certification cohort is evidence, not a random train/test split.  This
module therefore reserves truth before training, blocks whole photographic
moments across the boundary, and exposes only aggregate manifests publicly.
It deliberately does not fetch pixels or consult labels/model output.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.moment_grouping import (
    MOMENT_RADIUS_METRES,
    MOMENT_WINDOW_MINUTES,
    metres_between,
)
from immich_memories.api.models import AssetType

if TYPE_CHECKING:
    from immich_memories.api.sync_client import SyncImmichClient


_INVENTORY_DOMAIN = b"triage-full-image-inventory-v1\0"
_SELECTION_DOMAIN = b"triage-cohort-selection-v1\0"
_MIXED_TRUTH_DOMAIN = b"triage-mixed-truth-reservation-v2\0"
_LEDGER_DOMAIN = b"triage-truth-ledger-v1\0"
_MOMENT_DOMAIN = b"triage-capture-moment-v1\0"
_COMPONENT_DOMAIN = b"triage-duplicate-component-v1\0"
_LEDGER_SCHEMA_VERSION = 2
_SAFE_COHORT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")


def _sha256_json(domain: bytes, value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(domain + encoded).hexdigest()


def _stable_rank(seed: str, scope: str, value: str) -> str:
    payload = f"{seed}\0{scope}\0{value}".encode()
    return hashlib.sha256(payload).hexdigest()


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _epoch_seconds(value: datetime) -> float:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).timestamp()


def _group_digest(domain: bytes, asset_ids: Iterable[str]) -> str:
    ids = sorted(str(asset_id) for asset_id in asset_ids)
    return hashlib.sha256(domain + "\0".join(ids).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class InventoryAsset:
    """A compact private inventory row; no paths, captions, labels, or scores."""

    asset_id: str
    owner_id: str
    captured_at: str
    event_at: str
    updated_at: str
    capture_day: str
    year: int
    quarter: int
    latitude: float | None
    longitude: float | None
    width: int
    height: int
    device_id: str
    checksum: str | None
    thumbhash: str | None
    duplicate_id: str | None
    live_photo_video_id: str | None
    moment_key: str
    component_key: str

    @property
    def stratum(self) -> str:
        return f"{self.year:04d}-Q{self.quarter}"

    def canonical_record(self) -> tuple[object, ...]:
        """Return the private record bound into the inventory digest."""
        return (
            self.asset_id,
            self.owner_id,
            self.captured_at,
            self.event_at,
            self.updated_at,
            self.capture_day,
            self.year,
            self.quarter,
            self.latitude,
            self.longitude,
            self.width,
            self.height,
            self.device_id,
            self.checksum,
            self.thumbhash,
            self.duplicate_id,
            self.live_photo_video_id,
            self.moment_key,
            self.component_key,
        )


@dataclass(frozen=True, slots=True)
class InventoryScanStats:
    pages: int
    seen: int
    eligible: int
    repeated: int
    wrong_type: int
    foreign_owner: int
    trashed: int
    archived: int
    off_timeline: int

    def public_dict(self) -> dict[str, int]:
        return {
            "pages": self.pages,
            "seen": self.seen,
            "eligible": self.eligible,
            "repeated": self.repeated,
            "excluded_wrong_type": self.wrong_type,
            "excluded_foreign_owner": self.foreign_owner,
            "excluded_trashed": self.trashed,
            "excluded_archived": self.archived,
            "excluded_off_timeline": self.off_timeline,
        }


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    rows: tuple[InventoryAsset, ...]
    inventory_sha256: str
    stats: InventoryScanStats

    def public_manifest(self) -> dict[str, object]:
        """Return aggregates safe for a report; asset and group keys stay private."""
        days = {row.capture_day for row in self.rows}
        strata = {row.stratum for row in self.rows}
        years = {row.year for row in self.rows}
        return {
            "schema": "triage-image-inventory-public-v1",
            "inventory_sha256": self.inventory_sha256,
            "asset_count": len(self.rows),
            "distinct_capture_days": len(days),
            "distinct_years": len(years),
            "distinct_year_quarters": len(strata),
            "scan": self.stats.public_dict(),
        }


@dataclass(frozen=True, slots=True)
class _Draft:
    asset_id: str
    owner_id: str
    captured_at: str
    event_at: str
    updated_at: str
    event_epoch: float
    capture_day: str
    year: int
    quarter: int
    latitude: float | None
    longitude: float | None
    width: int
    height: int
    device_id: str
    checksum: str | None
    thumbhash: str | None
    duplicate_id: str | None
    live_photo_video_id: str | None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        if self.latitude is None or self.longitude is None:
            return None
        return self.latitude, self.longitude

    def identity_record(self) -> tuple[object, ...]:
        return (
            self.asset_id,
            self.owner_id,
            self.captured_at,
            self.event_at,
            self.updated_at,
            self.capture_day,
            self.year,
            self.quarter,
            self.latitude,
            self.longitude,
            self.width,
            self.height,
            self.device_id,
            self.checksum,
            self.thumbhash,
            self.duplicate_id,
            self.live_photo_video_id,
        )


def _draft_from_asset(asset: Any) -> _Draft:
    event_time = getattr(asset, "file_created_at", None)
    if not isinstance(event_time, datetime):
        raise ValueError("eligible Immich image has no file_created_at timestamp")
    local_time = getattr(asset, "local_date_time", None) or event_time
    if not isinstance(local_time, datetime):
        raise ValueError("eligible Immich image has no local capture timestamp")
    updated_at = getattr(asset, "updated_at", None)
    if not isinstance(updated_at, datetime):
        raise ValueError("eligible Immich image has no updated_at timestamp")
    exif = getattr(asset, "exif_info", None)
    latitude = getattr(exif, "latitude", None) if exif is not None else None
    longitude = getattr(exif, "longitude", None) if exif is not None else None
    return _Draft(
        asset_id=str(asset.id),
        owner_id=str(getattr(asset, "owner_id", "")),
        captured_at=_canonical_datetime(local_time),
        event_at=_canonical_datetime(event_time),
        updated_at=_canonical_datetime(updated_at),
        event_epoch=_epoch_seconds(event_time),
        capture_day=local_time.date().isoformat(),
        year=local_time.year,
        quarter=(local_time.month - 1) // 3 + 1,
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
        width=int(getattr(asset, "width", 0) or 0),
        height=int(getattr(asset, "height", 0) or 0),
        device_id=str(getattr(asset, "device_id", "") or ""),
        checksum=str(getattr(asset, "checksum", "") or "") or None,
        thumbhash=str(getattr(asset, "thumbhash", "") or "") or None,
        duplicate_id=str(getattr(asset, "duplicate_id", "") or "") or None,
        live_photo_video_id=str(getattr(asset, "live_photo_video_id", "") or "") or None,
    )


def _eligible_reason(asset: Any, owner_id: str) -> str | None:
    asset_type = getattr(asset, "type", None)
    if asset_type != AssetType.IMAGE and str(asset_type).upper() != AssetType.IMAGE.value:
        return "wrong_type"
    if str(getattr(asset, "owner_id", "")) != owner_id:
        return "foreign_owner"
    if bool(getattr(asset, "is_trashed", False)):
        return "trashed"
    if bool(getattr(asset, "is_archived", False)):
        return "archived"
    if str(getattr(asset, "visibility", "timeline")) != "timeline":
        return "off_timeline"
    return None


def _moment_groups(drafts: Sequence[_Draft]) -> list[list[_Draft]]:
    ordered = sorted(drafts, key=lambda row: (row.event_epoch, row.asset_id))
    moments: list[list[_Draft]] = []
    active: list[int] = []
    window_seconds = MOMENT_WINDOW_MINUTES * 60.0
    for row in ordered:
        active = [
            index
            for index in active
            if row.event_epoch - moments[index][-1].event_epoch <= window_seconds
        ]
        for index in reversed(active):
            moment = moments[index]
            if row.event_epoch - moment[-1].event_epoch > window_seconds:
                continue
            here = row.coordinates
            if here is not None:
                known_places = [member.coordinates for member in reversed(moment)]
                known_places = [place for place in known_places if place is not None]
                if known_places and not any(
                    metres_between(here, place) <= MOMENT_RADIUS_METRES for place in known_places
                ):
                    continue
            moment.append(row)
            break
        else:
            moments.append([row])
            active.append(len(moments) - 1)
    return moments


class _DisjointSets:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _component_keys(drafts: Sequence[_Draft]) -> dict[str, str]:
    """Join exact/metadata-near duplicates without looking at image pixels."""
    sets = _DisjointSets(len(drafts))
    signatures: dict[tuple[object, ...], int] = {}
    for index, row in enumerate(drafts):
        row_signatures: list[tuple[object, ...]] = []
        if row.checksum:
            row_signatures.append(("checksum", row.checksum))
        if row.duplicate_id:
            row_signatures.append(("immich-duplicate", row.duplicate_id))
        if row.live_photo_video_id:
            row_signatures.append(("live-photo", row.live_photo_video_id))
        if row.thumbhash:
            row_signatures.append(("thumbhash", row.thumbhash, row.width, row.height))
        # Same-device frames stamped in the same second are overwhelmingly a
        # burst/re-encode family.  Treating them as one component is safer than
        # allowing siblings to straddle truth and training.
        if row.device_id:
            row_signatures.append(
                (
                    "capture-second",
                    row.device_id,
                    math.floor(row.event_epoch),
                    row.width,
                    row.height,
                )
            )
        for signature in row_signatures:
            prior = signatures.setdefault(signature, index)
            sets.union(index, prior)
    members: dict[int, list[str]] = defaultdict(list)
    for index, row in enumerate(drafts):
        members[sets.find(index)].append(row.asset_id)
    digests = {
        root: _group_digest(_COMPONENT_DOMAIN, asset_ids) for root, asset_ids in members.items()
    }
    return {row.asset_id: digests[sets.find(index)] for index, row in enumerate(drafts)}


def build_inventory_snapshot(
    drafts: Sequence[_Draft],
    *,
    stats: InventoryScanStats,
) -> InventorySnapshot:
    """Attach deterministic moment/component keys and seal the inventory."""
    if not drafts:
        raise ValueError("live Immich scan contains no eligible images")
    by_id = {row.asset_id: row for row in drafts}
    if len(by_id) != len(drafts):
        raise ValueError("inventory asset ids must be unique")
    moment_keys: dict[str, str] = {}
    for moment in _moment_groups(drafts):
        key = _group_digest(_MOMENT_DOMAIN, (row.asset_id for row in moment))
        moment_keys.update({row.asset_id: key for row in moment})
    component_keys = _component_keys(drafts)
    rows = tuple(
        InventoryAsset(
            asset_id=row.asset_id,
            owner_id=row.owner_id,
            captured_at=row.captured_at,
            event_at=row.event_at,
            updated_at=row.updated_at,
            capture_day=row.capture_day,
            year=row.year,
            quarter=row.quarter,
            latitude=row.latitude,
            longitude=row.longitude,
            width=row.width,
            height=row.height,
            device_id=row.device_id,
            checksum=row.checksum,
            thumbhash=row.thumbhash,
            duplicate_id=row.duplicate_id,
            live_photo_video_id=row.live_photo_video_id,
            moment_key=moment_keys[row.asset_id],
            component_key=component_keys[row.asset_id],
        )
        for row in sorted(drafts, key=lambda item: item.asset_id)
    )
    return InventorySnapshot(rows, inventory_sha256(rows), stats)


def scan_live_image_inventory(
    client: SyncImmichClient,
    *,
    page_size: int = 1000,
    progress: Callable[[int, int, int], None] | None = None,
) -> InventorySnapshot:
    """Read every eligible owner image through metadata pagination.

    This is intentionally read-only and metadata-only. ``result.total`` is not
    trusted because deployed Immich versions disagree about its pagination
    semantics; ``next_page`` is the authoritative continuation signal.
    """
    if page_size < 1 or page_size > 1000:
        raise ValueError("page_size must be between 1 and 1000")
    owner_id = str(client.get_current_user().id)
    if not owner_id:
        raise ValueError("current Immich user has no id")

    drafts: dict[str, _Draft] = {}
    excluded: Counter[str] = Counter()
    seen = 0
    pages = 0
    repeated = 0
    continuation_tokens: set[str] = set()
    page = 1
    while True:
        result = client.search_metadata(asset_type=AssetType.IMAGE, page=page, size=page_size)
        pages += 1
        assets = list(result.all_assets)
        seen += len(assets)
        for asset in assets:
            reason = _eligible_reason(asset, owner_id)
            if reason is not None:
                excluded[reason] += 1
                continue
            draft = _draft_from_asset(asset)
            prior = drafts.get(draft.asset_id)
            if prior is not None:
                repeated += 1
                if prior.identity_record() != draft.identity_record():
                    raise ValueError("conflicting metadata for a repeated inventory asset")
                continue
            drafts[draft.asset_id] = draft
        if progress is not None:
            progress(pages, seen, len(drafts))
        next_page = result.next_page
        if not next_page:
            break
        token = str(next_page)
        if token in continuation_tokens:
            raise RuntimeError("Immich metadata pagination repeated a continuation token")
        continuation_tokens.add(token)
        if not assets:
            raise RuntimeError("Immich metadata pagination returned an empty continuing page")
        page += 1

    stats = InventoryScanStats(
        pages=pages,
        seen=seen,
        eligible=len(drafts),
        repeated=repeated,
        wrong_type=excluded["wrong_type"],
        foreign_owner=excluded["foreign_owner"],
        trashed=excluded["trashed"],
        archived=excluded["archived"],
        off_timeline=excluded["off_timeline"],
    )
    return build_inventory_snapshot(tuple(drafts.values()), stats=stats)


def inventory_sha256(rows: Iterable[InventoryAsset]) -> str:
    ordered = sorted(rows, key=lambda row: row.asset_id)
    if len({row.asset_id for row in ordered}) != len(ordered):
        raise ValueError("inventory asset ids must be unique")
    return _sha256_json(_INVENTORY_DOMAIN, [row.canonical_record() for row in ordered])


def _canonical_json_bytes(payload: object) -> bytes:
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


def _inventory_row_payload(row: InventoryAsset) -> dict[str, object]:
    return {
        "asset_id": row.asset_id,
        "owner_id": row.owner_id,
        "captured_at": row.captured_at,
        "event_at": row.event_at,
        "updated_at": row.updated_at,
        "capture_day": row.capture_day,
        "year": row.year,
        "quarter": row.quarter,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "width": row.width,
        "height": row.height,
        "device_id": row.device_id,
        "checksum": row.checksum,
        "thumbhash": row.thumbhash,
        "duplicate_id": row.duplicate_id,
        "live_photo_video_id": row.live_photo_video_id,
        "moment_key": row.moment_key,
        "component_key": row.component_key,
    }


def _inventory_row_from_payload(payload: Mapping[str, object]) -> InventoryAsset:
    return InventoryAsset(
        asset_id=str(payload["asset_id"]),
        owner_id=str(payload["owner_id"]),
        captured_at=str(payload["captured_at"]),
        event_at=str(payload["event_at"]),
        updated_at=str(payload["updated_at"]),
        capture_day=str(payload["capture_day"]),
        year=int(payload["year"]),
        quarter=int(payload["quarter"]),
        latitude=float(payload["latitude"]) if payload["latitude"] is not None else None,
        longitude=float(payload["longitude"]) if payload["longitude"] is not None else None,
        width=int(payload["width"]),
        height=int(payload["height"]),
        device_id=str(payload["device_id"]),
        checksum=str(payload["checksum"]) if payload["checksum"] is not None else None,
        thumbhash=str(payload["thumbhash"]) if payload["thumbhash"] is not None else None,
        duplicate_id=(
            str(payload["duplicate_id"]) if payload.get("duplicate_id") is not None else None
        ),
        live_photo_video_id=(
            str(payload["live_photo_video_id"])
            if payload.get("live_photo_video_id") is not None
            else None
        ),
        moment_key=str(payload["moment_key"]),
        component_key=str(payload["component_key"]),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write_create_only_idempotent(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"existing {path.name} has different immutable content")
        os.chmod(path, 0o600)
        return
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
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError(f"existing {path.name} has different immutable content")
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_json_bytes(row) for row in rows)


def _stats_payload(stats: InventoryScanStats) -> dict[str, int]:
    return {
        "pages": stats.pages,
        "seen": stats.seen,
        "eligible": stats.eligible,
        "repeated": stats.repeated,
        "wrong_type": stats.wrong_type,
        "foreign_owner": stats.foreign_owner,
        "trashed": stats.trashed,
        "archived": stats.archived,
        "off_timeline": stats.off_timeline,
    }


def save_inventory_snapshot(directory: Path, snapshot: InventorySnapshot) -> dict[str, object]:
    """Seal a private JSONL inventory and a separately publishable aggregate manifest."""
    directory = Path(directory)
    _private_directory(directory)
    index_bytes = _jsonl_bytes(
        _inventory_row_payload(row) for row in sorted(snapshot.rows, key=lambda row: row.asset_id)
    )
    public_manifest = snapshot.public_manifest()
    public_bytes = _canonical_json_bytes(public_manifest)
    private_manifest = {
        "schema": "triage-image-inventory-private-v1",
        "inventory_sha256": snapshot.inventory_sha256,
        "private_index_file": "inventory.jsonl",
        "private_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "public_manifest_file": "inventory-public.json",
        "public_manifest_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "scan_stats": _stats_payload(snapshot.stats),
    }
    _write_create_only_idempotent(directory / "inventory.jsonl", index_bytes)
    _write_create_only_idempotent(directory / "inventory-public.json", public_bytes)
    _write_create_only_idempotent(
        directory / "inventory-private-manifest.json",
        _canonical_json_bytes(private_manifest),
    )
    return public_manifest


def load_inventory_snapshot(directory: Path) -> InventorySnapshot:
    """Load and verify a sealed private inventory without touching Immich."""
    directory = Path(directory)
    if directory.stat().st_mode & 0o077:
        raise PermissionError("private inventory directory must not be group/world accessible")
    private_path = directory / "inventory-private-manifest.json"
    index_path = directory / "inventory.jsonl"
    public_path = directory / "inventory-public.json"
    for path in (private_path, index_path, public_path):
        if path.stat().st_mode & 0o077:
            raise PermissionError(f"private inventory file {path.name} has unsafe permissions")
    private_manifest = json.loads(private_path.read_text(encoding="utf-8"))
    if private_manifest.get("schema") != "triage-image-inventory-private-v1":
        raise ValueError("unsupported private inventory schema")
    index_bytes = index_path.read_bytes()
    if hashlib.sha256(index_bytes).hexdigest() != private_manifest["private_index_sha256"]:
        raise ValueError("private inventory JSONL digest does not reproduce")
    rows = tuple(
        _inventory_row_from_payload(json.loads(line))
        for line in index_bytes.decode("utf-8").splitlines()
        if line.strip()
    )
    digest = inventory_sha256(rows)
    if digest != private_manifest["inventory_sha256"]:
        raise ValueError("private inventory lineage digest does not reproduce")
    stats = InventoryScanStats(**private_manifest["scan_stats"])
    snapshot = InventorySnapshot(rows, digest, stats)
    public_bytes = public_path.read_bytes()
    if hashlib.sha256(public_bytes).hexdigest() != private_manifest["public_manifest_sha256"]:
        raise ValueError("public inventory manifest digest does not reproduce")
    if json.loads(public_bytes) != snapshot.public_manifest():
        raise ValueError("public inventory manifest differs from private inventory")
    return snapshot


@dataclass(frozen=True, slots=True)
class ReservationBlocklist:
    asset_ids: frozenset[str] = frozenset()
    component_keys: frozenset[str] = frozenset()
    moment_keys: frozenset[str] = frozenset()

    def extended(self, rows: Iterable[InventoryAsset]) -> ReservationBlocklist:
        materialized = tuple(rows)
        return ReservationBlocklist(
            self.asset_ids | {row.asset_id for row in materialized},
            self.component_keys | {row.component_key for row in materialized},
            self.moment_keys | {row.moment_key for row in materialized},
        )


@dataclass(frozen=True, slots=True)
class TruthReservationRecord:
    asset_id: str
    component_key: str
    moment_key: str
    capture_day: str
    stratum: str
    reservation_kind: str
    absent_asset_type: str | None
    reason: str | None
    source_universe_sha256: str


@dataclass(frozen=True, slots=True)
class TruthCohortReservation:
    name: str
    inventory_sha256: str
    selection_sha256: str
    selected_count: int
    rows: tuple[TruthReservationRecord, ...]


@dataclass(frozen=True, slots=True)
class VerifiedAbsentAsset:
    """Explicitly verified truth outside the sealed IMAGE source universe."""

    asset_id: str
    asset_type: str
    reason: str
    source_universe_sha256: str

    def __post_init__(self) -> None:
        normalized_type = self.asset_type.upper()
        if normalized_type == AssetType.IMAGE.value:
            raise ValueError("an IMAGE asset cannot be an absent IMAGE-universe reservation")
        if normalized_type not in {member.value for member in AssetType}:
            raise ValueError("verified absent asset type is unsupported")
        if not self.asset_id or not self.reason:
            raise ValueError("verified absent reservations require an asset id and reason")
        if len(self.source_universe_sha256) != 64:
            raise ValueError("verified absent source universe must be a SHA-256 digest")
        object.__setattr__(self, "asset_type", normalized_type)


def mixed_truth_reservation_sha256(
    mapped_rows: Iterable[InventoryAsset],
    absent_rows: Iterable[VerifiedAbsentAsset],
) -> str:
    records = [
        (
            "mapped",
            row.asset_id,
            row.component_key,
            row.moment_key,
            row.capture_day,
            row.stratum,
        )
        for row in mapped_rows
    ]
    records.extend(
        (
            "absent",
            row.asset_id,
            row.asset_type,
            row.reason,
            row.source_universe_sha256,
        )
        for row in absent_rows
    )
    records.sort(key=lambda record: (record[1], record[0]))
    if len({record[1] for record in records}) != len(records):
        raise ValueError("truth reservation asset ids must be unique")
    return _sha256_json(_MIXED_TRUTH_DOMAIN, records)


@dataclass(frozen=True, slots=True)
class CohortSpec:
    target_size: int
    minimum_size: int
    max_per_day: int
    max_per_moment: int
    min_distinct_days: int
    min_year_quarter_coverage: float = 0.90
    max_sqrt_quota_tv: float = 0.10

    def __post_init__(self) -> None:
        if self.target_size < 1 or not 1 <= self.minimum_size <= self.target_size:
            raise ValueError("cohort sizes must satisfy 1 <= minimum <= target")
        if self.max_per_day < 1 or self.max_per_moment < 1:
            raise ValueError("day and moment caps must be positive")
        if not 1 <= self.min_distinct_days <= self.target_size:
            raise ValueError("min_distinct_days must fit inside target_size")
        if not 0.0 <= self.min_year_quarter_coverage <= 1.0:
            raise ValueError("year-quarter coverage must be between zero and one")
        if not 0.0 <= self.max_sqrt_quota_tv <= 1.0:
            raise ValueError("quota total variation must be between zero and one")


def certification_cohort_spec() -> CohortSpec:
    return CohortSpec(
        target_size=400,
        minimum_size=300,
        max_per_day=2,
        max_per_moment=1,
        min_distinct_days=300,
    )


def training_cohort_spec() -> CohortSpec:
    return CohortSpec(
        target_size=20_000,
        minimum_size=16_000,
        max_per_day=12,
        max_per_moment=3,
        min_distinct_days=1_000,
    )


@dataclass(frozen=True, slots=True)
class CohortAudit:
    selected_count: int
    target_size: int
    minimum_size: int
    distinct_days: int
    distinct_moments: int
    distinct_components: int
    max_day_count: int
    max_moment_count: int
    year_quarter_coverage: float
    sqrt_quota_total_variation: float
    year_quarter_counts: tuple[tuple[str, int], ...]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def public_dict(self) -> dict[str, object]:
        return {
            "selected_count": self.selected_count,
            "target_size": self.target_size,
            "minimum_size": self.minimum_size,
            "target_reached": self.selected_count == self.target_size,
            "distinct_days": self.distinct_days,
            "distinct_moments": self.distinct_moments,
            "distinct_components": self.distinct_components,
            "max_day_count": self.max_day_count,
            "max_moment_count": self.max_moment_count,
            "year_quarter_coverage": self.year_quarter_coverage,
            "sqrt_quota_total_variation": self.sqrt_quota_total_variation,
            "year_quarter_counts": dict(self.year_quarter_counts),
            "passed": self.passed,
            "failures": list(self.failures),
        }


class CohortQualityError(RuntimeError):
    def __init__(self, audit: CohortAudit) -> None:
        super().__init__("cohort quality gates failed: " + ", ".join(audit.failures))
        self.audit = audit


def audit_cohort(
    selected: Sequence[InventoryAsset],
    eligible: Sequence[InventoryAsset],
    spec: CohortSpec,
) -> CohortAudit:
    eligible_by_id = {row.asset_id: row for row in eligible}
    selected_ids = [row.asset_id for row in selected]
    failures: list[str] = []
    if len(set(selected_ids)) != len(selected_ids):
        failures.append("duplicate_asset_ids")
    if any(asset_id not in eligible_by_id for asset_id in selected_ids):
        failures.append("selection_outside_eligible_inventory")
    days = Counter(row.capture_day for row in selected)
    moments = Counter(row.moment_key for row in selected)
    components = Counter(row.component_key for row in selected)
    if len(selected) < spec.minimum_size:
        failures.append("below_minimum_size")
    if len(days) < spec.min_distinct_days:
        failures.append("too_few_distinct_days")
    if days and max(days.values()) > spec.max_per_day:
        failures.append("per_day_cap_exceeded")
    if moments and max(moments.values()) > spec.max_per_moment:
        failures.append("per_moment_cap_exceeded")
    if components and max(components.values()) > 1:
        failures.append("duplicate_component_selected")

    eligible_counts = Counter(row.stratum for row in eligible)
    selected_counts = Counter(row.stratum for row in selected)
    possible_strata = min(len(eligible_counts), spec.target_size)
    coverage = len(selected_counts) / possible_strata if possible_strata else 0.0
    if coverage + 1e-12 < spec.min_year_quarter_coverage:
        failures.append("year_quarter_coverage_below_gate")
    weight_total = sum(math.sqrt(count) for count in eligible_counts.values())
    selected_total = len(selected)
    if not weight_total or not selected_total:
        quota_tv = 1.0
    else:
        quota_tv = 0.5 * sum(
            abs(selected_counts.get(stratum, 0) / selected_total - math.sqrt(count) / weight_total)
            for stratum, count in eligible_counts.items()
        )
    if quota_tv > spec.max_sqrt_quota_tv + 1e-12:
        failures.append("sqrt_quota_total_variation_above_gate")

    return CohortAudit(
        selected_count=len(selected),
        target_size=spec.target_size,
        minimum_size=spec.minimum_size,
        distinct_days=len(days),
        distinct_moments=len(moments),
        distinct_components=len(components),
        max_day_count=max(days.values(), default=0),
        max_moment_count=max(moments.values(), default=0),
        year_quarter_coverage=coverage,
        sqrt_quota_total_variation=quota_tv,
        year_quarter_counts=tuple(sorted(selected_counts.items())),
        failures=tuple(failures),
    )


def cohort_selection_sha256(rows: Iterable[InventoryAsset]) -> str:
    private_rows = sorted(
        (
            row.asset_id,
            row.component_key,
            row.moment_key,
            row.capture_day,
            row.stratum,
        )
        for row in rows
    )
    if len({row[0] for row in private_rows}) != len(private_rows):
        raise ValueError("cohort asset ids must be unique")
    return _sha256_json(_SELECTION_DOMAIN, private_rows)


@dataclass(frozen=True, slots=True)
class CohortSelection:
    name: str
    rows: tuple[InventoryAsset, ...]
    selection_sha256: str
    audit: CohortAudit

    def public_manifest(self) -> dict[str, object]:
        return {
            "schema": "triage-cohort-public-v1",
            "name": self.name,
            "selection_sha256": self.selection_sha256,
            "audit": self.audit.public_dict(),
        }


def select_cohort(
    rows: Sequence[InventoryAsset],
    *,
    name: str,
    spec: CohortSpec,
    seed: str,
    blocked: ReservationBlocklist | None = None,
    require_quality: bool = True,
) -> CohortSelection:
    """Select a label-blind cohort with square-root year/quarter allocation."""
    blocked = blocked or ReservationBlocklist()
    unique = {row.asset_id: row for row in rows}
    if len(unique) != len(rows):
        raise ValueError("candidate inventory asset ids must be unique")
    eligible = [
        row
        for row in unique.values()
        if row.asset_id not in blocked.asset_ids
        and row.component_key not in blocked.component_keys
        and row.moment_key not in blocked.moment_keys
    ]
    by_stratum: dict[str, deque[InventoryAsset]] = {}
    stratum_sizes: dict[str, int] = {}
    for stratum, members in _group_by(eligible, key=lambda row: row.stratum).items():
        ordered = _day_round_robin_order(
            members,
            seed=seed,
            scope=f"{name}:{stratum}",
        )
        by_stratum[stratum] = deque(ordered)
        stratum_sizes[stratum] = len(ordered)

    selected: list[InventoryAsset] = []
    selected_by_stratum: Counter[str] = Counter()
    selected_days: Counter[str] = Counter()
    selected_moments: Counter[str] = Counter()
    selected_components: set[str] = set()
    while len(selected) < spec.target_size:
        available: list[tuple[float, str, str]] = []
        for stratum, queue in by_stratum.items():
            while queue:
                candidate = queue[0]
                if (
                    selected_days[candidate.capture_day] >= spec.max_per_day
                    or selected_moments[candidate.moment_key] >= spec.max_per_moment
                    or candidate.component_key in selected_components
                ):
                    queue.popleft()
                    continue
                break
            if queue:
                priority = (selected_by_stratum[stratum] + 0.5) / math.sqrt(stratum_sizes[stratum])
                available.append(
                    (priority, _stable_rank(seed, name, f"stratum:{stratum}"), stratum)
                )
        if not available:
            break
        _, _, chosen_stratum = min(available)
        chosen = by_stratum[chosen_stratum].popleft()
        selected.append(chosen)
        selected_by_stratum[chosen_stratum] += 1
        selected_days[chosen.capture_day] += 1
        selected_moments[chosen.moment_key] += 1
        selected_components.add(chosen.component_key)

    selected_tuple = tuple(selected)
    audit = audit_cohort(selected_tuple, eligible, spec)
    if require_quality and not audit.passed:
        raise CohortQualityError(audit)
    return CohortSelection(
        name=name,
        rows=selected_tuple,
        selection_sha256=cohort_selection_sha256(selected_tuple),
        audit=audit,
    )


def _group_by(
    rows: Iterable[InventoryAsset],
    *,
    key: Callable[[InventoryAsset], str],
) -> dict[str, list[InventoryAsset]]:
    groups: dict[str, list[InventoryAsset]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def _day_round_robin_order(
    rows: Sequence[InventoryAsset],
    *,
    seed: str,
    scope: str,
) -> list[InventoryAsset]:
    """Use one candidate per day before revisiting any day in a stratum."""
    by_day = _group_by(rows, key=lambda row: row.capture_day)
    queues = {
        day: deque(
            sorted(
                members,
                key=lambda row: (
                    _stable_rank(seed, scope, row.asset_id),
                    row.asset_id,
                ),
            )
        )
        for day, members in by_day.items()
    }
    days = sorted(
        queues,
        key=lambda day: (_stable_rank(seed, scope, f"day:{day}"), day),
    )
    ordered: list[InventoryAsset] = []
    while True:
        added = False
        for day in days:
            if queues[day]:
                ordered.append(queues[day].popleft())
                added = True
        if not added:
            return ordered


@dataclass(frozen=True, slots=True)
class CohortPlan:
    certification: CohortSelection
    training: CohortSelection

    def public_manifest(self) -> dict[str, object]:
        return {
            "schema": "triage-cohort-plan-public-v1",
            "certification": self.certification.public_manifest(),
            "training": self.training.public_manifest(),
            "cross_cohort_overlap": {
                "asset_ids": 0,
                "components": 0,
                "moments": 0,
            },
        }


def select_certification_then_training(
    rows: Sequence[InventoryAsset],
    *,
    certification_spec: CohortSpec,
    training_spec: CohortSpec,
    seed: str,
    already_reserved: ReservationBlocklist | None = None,
) -> CohortPlan:
    """Choose truth first, then exclude its IDs, duplicates, and events from training."""
    existing = already_reserved or ReservationBlocklist()
    certification = select_cohort(
        rows,
        name="location-certification-v2",
        spec=certification_spec,
        seed=seed,
        blocked=existing,
    )
    training_blocklist = existing.extended(certification.rows)
    training = select_cohort(
        rows,
        name="location-training-v2",
        spec=training_spec,
        seed=seed,
        blocked=training_blocklist,
    )
    cert_ids = {row.asset_id for row in certification.rows}
    cert_components = {row.component_key for row in certification.rows}
    cert_moments = {row.moment_key for row in certification.rows}
    if cert_ids & {row.asset_id for row in training.rows}:
        raise AssertionError("asset overlap survived cohort blocking")
    if cert_components & {row.component_key for row in training.rows}:
        raise AssertionError("component overlap survived cohort blocking")
    if cert_moments & {row.moment_key for row in training.rows}:
        raise AssertionError("moment overlap survived cohort blocking")
    return CohortPlan(certification, training)


class TruthReservationLedger:
    """Append-only, local-private ledger for evidence that must never train."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
                _fsync_directory(self.path.parent)
        os.chmod(self.path, 0o600)
        self._initialize()
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1, _LEDGER_SCHEMA_VERSION):
                raise RuntimeError(f"unsupported truth ledger schema version {version}")
            if version == 1:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    DROP TRIGGER IF EXISTS reservations_no_update;
                    DROP TRIGGER IF EXISTS reservations_no_delete;
                    DROP TRIGGER IF EXISTS reservations_declared_count;
                    DROP INDEX IF EXISTS reservations_component;
                    DROP INDEX IF EXISTS reservations_moment;
                    ALTER TABLE reservations RENAME TO reservations_v1;
                    CREATE TABLE reservations (
                        cohort_name TEXT NOT NULL REFERENCES cohorts(name),
                        asset_id TEXT PRIMARY KEY,
                        component_key TEXT NOT NULL,
                        moment_key TEXT NOT NULL,
                        capture_day TEXT NOT NULL,
                        stratum TEXT NOT NULL,
                        reservation_kind TEXT NOT NULL,
                        absent_asset_type TEXT,
                        reason TEXT,
                        source_universe_sha256 TEXT NOT NULL
                    );
                    INSERT INTO reservations(
                        cohort_name, asset_id, component_key, moment_key, capture_day,
                        stratum, reservation_kind, absent_asset_type, reason,
                        source_universe_sha256
                    )
                    SELECT
                        old.cohort_name, old.asset_id, old.component_key, old.moment_key,
                        old.capture_day, old.stratum, 'mapped', NULL, NULL,
                        cohorts.inventory_sha256
                    FROM reservations_v1 AS old
                    JOIN cohorts ON cohorts.name = old.cohort_name;
                    DROP TABLE reservations_v1;
                    COMMIT;
                    """
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cohorts (
                    name TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL CHECK (purpose = 'truth'),
                    inventory_sha256 TEXT NOT NULL,
                    selection_sha256 TEXT NOT NULL UNIQUE,
                    selected_count INTEGER NOT NULL CHECK (selected_count > 0),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    cohort_name TEXT NOT NULL REFERENCES cohorts(name),
                    asset_id TEXT PRIMARY KEY,
                    component_key TEXT NOT NULL,
                    moment_key TEXT NOT NULL,
                    capture_day TEXT NOT NULL,
                    stratum TEXT NOT NULL,
                    reservation_kind TEXT NOT NULL
                        CHECK (reservation_kind IN ('mapped', 'absent')),
                    absent_asset_type TEXT,
                    reason TEXT,
                    source_universe_sha256 TEXT NOT NULL,
                    CHECK (
                        (
                            reservation_kind = 'mapped'
                            AND component_key != '' AND moment_key != ''
                            AND capture_day != '' AND stratum != ''
                            AND absent_asset_type IS NULL AND reason IS NULL
                        ) OR (
                            reservation_kind = 'absent'
                            AND component_key = '' AND moment_key = ''
                            AND capture_day = '' AND stratum = ''
                            AND absent_asset_type IS NOT NULL AND reason IS NOT NULL
                        )
                    )
                );
                CREATE INDEX IF NOT EXISTS reservations_component
                    ON reservations(component_key);
                CREATE INDEX IF NOT EXISTS reservations_moment
                    ON reservations(moment_key);
                CREATE TRIGGER IF NOT EXISTS cohorts_no_update
                BEFORE UPDATE ON cohorts BEGIN
                    SELECT RAISE(ABORT, 'truth cohorts are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS cohorts_no_delete
                BEFORE DELETE ON cohorts BEGIN
                    SELECT RAISE(ABORT, 'truth cohorts are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS reservations_no_update
                BEFORE UPDATE ON reservations BEGIN
                    SELECT RAISE(ABORT, 'truth reservations are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS reservations_no_delete
                BEFORE DELETE ON reservations BEGIN
                    SELECT RAISE(ABORT, 'truth reservations are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS reservations_declared_count
                BEFORE INSERT ON reservations
                WHEN (
                    SELECT COUNT(*) FROM reservations
                    WHERE cohort_name = NEW.cohort_name
                ) >= (
                    SELECT selected_count FROM cohorts
                    WHERE name = NEW.cohort_name
                )
                BEGIN
                    SELECT RAISE(ABORT, 'truth cohort already has its declared rows');
                END;
                CREATE TRIGGER IF NOT EXISTS reservations_valid_shape
                BEFORE INSERT ON reservations
                WHEN NOT (
                    (
                        NEW.reservation_kind = 'mapped'
                        AND NEW.component_key != '' AND NEW.moment_key != ''
                        AND NEW.capture_day != '' AND NEW.stratum != ''
                        AND NEW.absent_asset_type IS NULL AND NEW.reason IS NULL
                    ) OR (
                        NEW.reservation_kind = 'absent'
                        AND NEW.component_key = '' AND NEW.moment_key = ''
                        AND NEW.capture_day = '' AND NEW.stratum = ''
                        AND NEW.absent_asset_type IS NOT NULL AND NEW.reason IS NOT NULL
                    )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid truth reservation shape');
                END;
                """
            )
            connection.execute(f"PRAGMA user_version = {_LEDGER_SCHEMA_VERSION}")

    def reserve_truth_cohort(
        self,
        *,
        name: str,
        inventory_sha256: str,
        rows: Sequence[InventoryAsset],
    ) -> str:
        """Create one immutable reservation, or verify an exact idempotent replay."""
        return self._reserve_truth_cohort(
            name=name,
            inventory_sha256=inventory_sha256,
            mapped_rows=rows,
            absent_rows=(),
        )

    def reserve_truth_cohort_if_unchanged(
        self,
        *,
        name: str,
        inventory_sha256: str,
        rows: Sequence[InventoryAsset],
        expected_ledger_sha256: str,
    ) -> str:
        """Append truth only if the selection-time ledger is still current.

        The comparison and append share one ``BEGIN IMMEDIATE`` transaction.
        An exact replay remains idempotent after the first append, while a new
        cohort cannot commit against stale exclusion evidence.
        """
        if len(expected_ledger_sha256) != 64:
            raise ValueError("expected ledger digest must be a SHA-256 hex digest")
        return self._reserve_truth_cohort(
            name=name,
            inventory_sha256=inventory_sha256,
            mapped_rows=rows,
            absent_rows=(),
            expected_ledger_sha256=expected_ledger_sha256,
        )

    def reserve_mixed_truth_cohort(
        self,
        *,
        name: str,
        inventory_sha256: str,
        mapped_rows: Sequence[InventoryAsset],
        absent_rows: Sequence[VerifiedAbsentAsset],
    ) -> str:
        """Reserve mapped truth plus explicitly verified out-of-universe assets."""
        if not absent_rows:
            raise ValueError("mixed truth reservation requires at least one absent asset")
        return self._reserve_truth_cohort(
            name=name,
            inventory_sha256=inventory_sha256,
            mapped_rows=mapped_rows,
            absent_rows=absent_rows,
        )

    def _reserve_truth_cohort(
        self,
        *,
        name: str,
        inventory_sha256: str,
        mapped_rows: Sequence[InventoryAsset],
        absent_rows: Sequence[VerifiedAbsentAsset],
        expected_ledger_sha256: str | None = None,
    ) -> str:
        if not _SAFE_COHORT_NAME.fullmatch(name):
            raise ValueError("truth cohort name must be a safe lowercase public identifier")
        if len(inventory_sha256) != 64:
            raise ValueError("inventory_sha256 must be a SHA-256 hex digest")
        mapped = tuple(mapped_rows)
        absent = tuple(absent_rows)
        if not mapped and not absent:
            raise ValueError("truth cohort must contain at least one row")
        all_asset_ids = [row.asset_id for row in mapped] + [row.asset_id for row in absent]
        if len(set(all_asset_ids)) != len(all_asset_ids):
            raise ValueError("truth cohort asset ids must be unique")
        if any(row.source_universe_sha256 != inventory_sha256 for row in absent):
            raise ValueError("absent truth source universe differs from cohort inventory")
        selection_digest = (
            cohort_selection_sha256(mapped)
            if not absent
            else mixed_truth_reservation_sha256(mapped, absent)
        )
        expected_rows = sorted(
            [
                (
                    row.asset_id,
                    row.component_key,
                    row.moment_key,
                    row.capture_day,
                    row.stratum,
                    "mapped",
                    None,
                    None,
                    inventory_sha256,
                )
                for row in mapped
            ]
            + [
                (
                    row.asset_id,
                    "",
                    "",
                    "",
                    "",
                    "absent",
                    row.asset_type,
                    row.reason,
                    row.source_universe_sha256,
                )
                for row in absent
            ],
            key=lambda row: row[0],
        )

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM cohorts WHERE name = ?", (name,)
            ).fetchone()
            if existing is not None:
                actual_rows = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT asset_id, component_key, moment_key, capture_day, stratum,
                               reservation_kind, absent_asset_type, reason,
                               source_universe_sha256
                        FROM reservations WHERE cohort_name = ? ORDER BY asset_id
                        """,
                        (name,),
                    )
                ]
                expected_header = (
                    inventory_sha256,
                    selection_digest,
                    len(expected_rows),
                )
                actual_header = (
                    existing["inventory_sha256"],
                    existing["selection_sha256"],
                    existing["selected_count"],
                )
                if actual_header != expected_header or actual_rows != expected_rows:
                    raise RuntimeError("truth cohort name already has different immutable content")
                connection.commit()
                return self.ledger_sha256(connection=connection)

            if (
                expected_ledger_sha256 is not None
                and self.ledger_sha256(connection=connection) != expected_ledger_sha256
            ):
                raise RuntimeError("truth ledger changed since cohort selection")

            for row in expected_rows:
                asset_conflict = connection.execute(
                    "SELECT 1 FROM reservations WHERE asset_id = ? LIMIT 1",
                    (row[0],),
                ).fetchone()
                if asset_conflict is not None:
                    raise RuntimeError("truth reservation overlaps an existing immutable cohort")
                if row[5] != "mapped":
                    continue
                group_conflict = connection.execute(
                    """
                    SELECT 1 FROM reservations
                    WHERE reservation_kind = 'mapped'
                      AND (component_key = ? OR moment_key = ?) LIMIT 1
                    """,
                    (row[1], row[2]),
                ).fetchone()
                if group_conflict is not None:
                    raise RuntimeError("truth reservation overlaps an existing immutable cohort")
            connection.execute(
                """
                INSERT INTO cohorts(
                    name, purpose, inventory_sha256, selection_sha256, selected_count, created_at
                ) VALUES (?, 'truth', ?, ?, ?, ?)
                """,
                (
                    name,
                    inventory_sha256,
                    selection_digest,
                    len(expected_rows),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO reservations(
                    cohort_name, asset_id, component_key, moment_key, capture_day, stratum,
                    reservation_kind, absent_asset_type, reason, source_universe_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ((name, *row) for row in expected_rows),
            )
            connection.commit()
            return self.ledger_sha256(connection=connection)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def blocklist(self, *, exclude_cohort: str | None = None) -> ReservationBlocklist:
        with self._connect() as connection:
            if exclude_cohort is None:
                rows = connection.execute(
                    """
                    SELECT asset_id, component_key, moment_key, reservation_kind
                    FROM reservations
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT asset_id, component_key, moment_key, reservation_kind
                    FROM reservations
                    WHERE cohort_name != ?
                    """,
                    (exclude_cohort,),
                ).fetchall()
        return ReservationBlocklist(
            frozenset(str(row["asset_id"]) for row in rows),
            frozenset(
                str(row["component_key"]) for row in rows if row["reservation_kind"] == "mapped"
            ),
            frozenset(
                str(row["moment_key"]) for row in rows if row["reservation_kind"] == "mapped"
            ),
        )

    def cohort_reservation(self, name: str) -> TruthCohortReservation | None:
        """Return one private immutable cohort reservation for authentication."""
        with self._connect() as connection:
            cohort = connection.execute(
                """
                SELECT name, inventory_sha256, selection_sha256, selected_count
                FROM cohorts WHERE name = ?
                """,
                (name,),
            ).fetchone()
            if cohort is None:
                return None
            rows = tuple(
                TruthReservationRecord(
                    asset_id=str(row["asset_id"]),
                    component_key=str(row["component_key"]),
                    moment_key=str(row["moment_key"]),
                    capture_day=str(row["capture_day"]),
                    stratum=str(row["stratum"]),
                    reservation_kind=str(row["reservation_kind"]),
                    absent_asset_type=(
                        str(row["absent_asset_type"])
                        if row["absent_asset_type"] is not None
                        else None
                    ),
                    reason=str(row["reason"]) if row["reason"] is not None else None,
                    source_universe_sha256=str(row["source_universe_sha256"]),
                )
                for row in connection.execute(
                    """
                    SELECT asset_id, component_key, moment_key, capture_day, stratum,
                           reservation_kind, absent_asset_type, reason,
                           source_universe_sha256
                    FROM reservations WHERE cohort_name = ? ORDER BY asset_id
                    """,
                    (name,),
                )
            )
        return TruthCohortReservation(
            name=str(cohort["name"]),
            inventory_sha256=str(cohort["inventory_sha256"]),
            selection_sha256=str(cohort["selection_sha256"]),
            selected_count=int(cohort["selected_count"]),
            rows=rows,
        )

    def commit_if_unchanged(
        self,
        *,
        expected_ledger_sha256: str,
        commit: Callable[[], Any],
    ) -> Any:
        """Hold the writer lock across a digest check and filesystem commit point."""
        if len(expected_ledger_sha256) != 64:
            raise ValueError("expected ledger digest must be a SHA-256 hex digest")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self.ledger_sha256(connection=connection) != expected_ledger_sha256:
                raise RuntimeError("truth ledger changed before the training commit")
            result = commit()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ledger_sha256(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        exclude_cohort: str | None = None,
    ) -> str:
        """Digest the append-only truth view, optionally before one named cohort.

        Exclusion exists for exact crash recovery: a caller can authenticate the
        selection-time view after that same cohort was already committed.
        """
        owns_connection = connection is None
        active = connection or self._connect()
        try:
            if exclude_cohort is None:
                cohort_cursor = active.execute(
                    """
                    SELECT name, purpose, inventory_sha256, selection_sha256, selected_count
                    FROM cohorts ORDER BY name
                    """
                )
                reservation_cursor = active.execute(
                    """
                    SELECT cohort_name, asset_id, component_key, moment_key, capture_day, stratum,
                           reservation_kind, absent_asset_type, reason, source_universe_sha256
                    FROM reservations ORDER BY cohort_name, asset_id
                    """
                )
            else:
                cohort_cursor = active.execute(
                    """
                    SELECT name, purpose, inventory_sha256, selection_sha256, selected_count
                    FROM cohorts WHERE name != ? ORDER BY name
                    """,
                    (exclude_cohort,),
                )
                reservation_cursor = active.execute(
                    """
                    SELECT cohort_name, asset_id, component_key, moment_key, capture_day, stratum,
                           reservation_kind, absent_asset_type, reason, source_universe_sha256
                    FROM reservations WHERE cohort_name != ? ORDER BY cohort_name, asset_id
                    """,
                    (exclude_cohort,),
                )
            cohorts = [tuple(row) for row in cohort_cursor]
            reservations = [tuple(row) for row in reservation_cursor]
            return _sha256_json(
                _LEDGER_DOMAIN,
                {"cohorts": cohorts, "reservations": reservations},
            )
        finally:
            if owns_connection:
                active.close()

    def public_manifest(self) -> dict[str, object]:
        with self._connect() as connection:
            cohorts = [
                {
                    "name": str(row["name"]),
                    "inventory_sha256": str(row["inventory_sha256"]),
                    "selection_sha256": str(row["selection_sha256"]),
                    "selected_count": int(row["selected_count"]),
                    "mapped_count": int(row["mapped_count"]),
                    "asset_only_count": int(row["asset_only_count"]),
                }
                for row in connection.execute(
                    """
                    SELECT
                        cohorts.name,
                        cohorts.inventory_sha256,
                        cohorts.selection_sha256,
                        cohorts.selected_count,
                        SUM(reservations.reservation_kind = 'mapped') AS mapped_count,
                        SUM(reservations.reservation_kind = 'absent') AS asset_only_count
                    FROM cohorts
                    JOIN reservations ON reservations.cohort_name = cohorts.name
                    GROUP BY cohorts.name
                    ORDER BY cohorts.name
                    """
                )
            ]
            absent_by_type = {
                str(row["absent_asset_type"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT absent_asset_type, COUNT(*) AS count
                    FROM reservations
                    WHERE reservation_kind = 'absent'
                    GROUP BY absent_asset_type
                    ORDER BY absent_asset_type
                    """
                )
            }
            digest = self.ledger_sha256(connection=connection)
        return {
            "schema": "triage-truth-ledger-public-v2",
            "ledger_sha256": digest,
            "cohort_count": len(cohorts),
            "reservation_count": sum(row["selected_count"] for row in cohorts),
            "mapped_reservation_count": sum(row["mapped_count"] for row in cohorts),
            "asset_only_reservation_count": sum(row["asset_only_count"] for row in cohorts),
            "asset_only_by_type": absent_by_type,
            "cohorts": cohorts,
        }


def reserve_truth_asset_ids(
    ledger: TruthReservationLedger,
    *,
    name: str,
    snapshot: InventorySnapshot,
    asset_ids: Iterable[str],
    verified_absent_types: Mapping[str, str] | None = None,
) -> str:
    """Map legacy truth, requiring explicit classification for every absent ID."""
    requested = tuple(str(asset_id) for asset_id in asset_ids)
    if not requested:
        raise ValueError("legacy truth set contains no asset ids")
    if len(set(requested)) != len(requested):
        raise ValueError("legacy truth set contains duplicate asset ids")
    by_id = {row.asset_id: row for row in snapshot.rows}
    missing = {asset_id for asset_id in requested if asset_id not in by_id}
    verified = {
        str(asset_id): str(asset_type).upper()
        for asset_id, asset_type in (verified_absent_types or {}).items()
    }
    unclassified = missing - set(verified)
    unexpected = set(verified) - missing
    if unclassified:
        raise ValueError(
            f"{len(unclassified)} legacy truth assets are absent and not explicitly classified"
        )
    if unexpected:
        raise ValueError(
            f"{len(unexpected)} verified-absent assets are present or outside the truth request"
        )
    mapped = [by_id[asset_id] for asset_id in requested if asset_id in by_id]
    if not missing:
        return ledger.reserve_truth_cohort(
            name=name,
            inventory_sha256=snapshot.inventory_sha256,
            rows=mapped,
        )
    absent = [
        VerifiedAbsentAsset(
            asset_id=asset_id,
            asset_type=verified[asset_id],
            reason="verified-live-type-outside-sealed-image-universe",
            source_universe_sha256=snapshot.inventory_sha256,
        )
        for asset_id in sorted(missing)
    ]
    return ledger.reserve_mixed_truth_cohort(
        name=name,
        inventory_sha256=snapshot.inventory_sha256,
        mapped_rows=mapped,
        absent_rows=absent,
    )


def load_truth_asset_ids(paths: Sequence[Path]) -> tuple[str, ...]:
    """Read explicit asset IDs from old JSON/JSONL truth artifacts."""
    ids: list[str] = []

    def append_row(row: object) -> None:
        if not isinstance(row, Mapping):
            raise ValueError("truth rows must be JSON objects")
        for key in ("image_id", "asset_id", "id"):
            value = row.get(key)
            if value:
                ids.append(str(value))
                return
        raise ValueError("truth row has no explicit image_id, asset_id, or id")

    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        if Path(path).suffix.casefold() == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    append_row(json.loads(line))
            continue
        payload = json.loads(text)
        if isinstance(payload, list):
            for row in payload:
                append_row(row)
            continue
        if not isinstance(payload, Mapping):
            raise ValueError("truth JSON must contain an array or object")
        explicit_ids = payload.get("image_ids") or payload.get("asset_ids")
        if isinstance(explicit_ids, list):
            ids.extend(str(value) for value in explicit_ids)
            continue
        rows = payload.get("rows") or payload.get("items") or payload.get("assets")
        if not isinstance(rows, list):
            raise ValueError("truth JSON object has no explicit ID list or row array")
        for row in rows:
            append_row(row)
    if len(set(ids)) != len(ids):
        raise ValueError("legacy truth inputs contain duplicate asset ids")
    return tuple(ids)


def save_cohort_plan(
    directory: Path,
    *,
    snapshot: InventorySnapshot,
    plan: CohortPlan,
    ledger_sha256: str,
) -> dict[str, object]:
    """Seal private cert/train indices beside an aggregate-only public manifest."""
    if len(ledger_sha256) != 64:
        raise ValueError("ledger_sha256 must be a SHA-256 hex digest")
    snapshot_by_id = {row.asset_id: row for row in snapshot.rows}
    for selection in (plan.certification, plan.training):
        if not selection.audit.passed:
            raise CohortQualityError(selection.audit)
        if cohort_selection_sha256(selection.rows) != selection.selection_sha256:
            raise ValueError("cohort selection digest does not reproduce")
        mismatched = sum(snapshot_by_id.get(row.asset_id) != row for row in selection.rows)
        if mismatched:
            raise ValueError(f"{mismatched} cohort rows differ from the sealed inventory")
    certification_ids = {row.asset_id for row in plan.certification.rows}
    training_ids = {row.asset_id for row in plan.training.rows}
    if certification_ids & training_ids:
        raise ValueError("certification and training asset sets overlap")
    certification_components = {row.component_key for row in plan.certification.rows}
    training_components = {row.component_key for row in plan.training.rows}
    if certification_components & training_components:
        raise ValueError("certification and training duplicate components overlap")
    certification_moments = {row.moment_key for row in plan.certification.rows}
    training_moments = {row.moment_key for row in plan.training.rows}
    if certification_moments & training_moments:
        raise ValueError("certification and training capture moments overlap")
    directory = Path(directory)
    _private_directory(directory)
    certification_bytes = _jsonl_bytes(
        _inventory_row_payload(row)
        for row in sorted(plan.certification.rows, key=lambda row: row.asset_id)
    )
    training_bytes = _jsonl_bytes(
        _inventory_row_payload(row)
        for row in sorted(plan.training.rows, key=lambda row: row.asset_id)
    )
    public_manifest = {
        **plan.public_manifest(),
        "inventory_sha256": snapshot.inventory_sha256,
        "truth_ledger_sha256": ledger_sha256,
    }
    public_bytes = _canonical_json_bytes(public_manifest)
    private_manifest = {
        "schema": "triage-cohort-plan-private-v1",
        "inventory_sha256": snapshot.inventory_sha256,
        "truth_ledger_sha256": ledger_sha256,
        "certification_index_file": "certification-index.jsonl",
        "certification_index_sha256": hashlib.sha256(certification_bytes).hexdigest(),
        "certification_selection_sha256": plan.certification.selection_sha256,
        "training_index_file": "training-index.jsonl",
        "training_index_sha256": hashlib.sha256(training_bytes).hexdigest(),
        "training_selection_sha256": plan.training.selection_sha256,
        "public_manifest_file": "cohort-plan-public.json",
        "public_manifest_sha256": hashlib.sha256(public_bytes).hexdigest(),
    }
    _write_create_only_idempotent(directory / "certification-index.jsonl", certification_bytes)
    _write_create_only_idempotent(directory / "training-index.jsonl", training_bytes)
    _write_create_only_idempotent(directory / "cohort-plan-public.json", public_bytes)
    _write_create_only_idempotent(
        directory / "cohort-plan-private-manifest.json",
        _canonical_json_bytes(private_manifest),
    )
    return public_manifest
