"""Read immutable annotation facts from the configured library database."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


@dataclass(frozen=True)
class StoredPersonFact:
    """One recognized person as recorded by the annotation store."""

    person_id: str
    name: str
    birth_date: date | None


@dataclass(frozen=True)
class StoredFlagFact:
    """One non-exposure warning and its normalized human reason."""

    flag: str
    reason: str
    source: str


@dataclass(frozen=True)
class StoredPixelFacts:
    """Pixel measurements produced by one exact implementation."""

    sharpness: float | None
    brightness: float | None
    contrast: float | None
    dark_fraction: float | None
    bright_fraction: float | None
    needs_rotation: bool
    soft_below: float | None


@dataclass(frozen=True)
class StoredMotionBurstFact:
    """Legacy motion evidence for one Live Photo carrier."""

    burst_id: str
    still_ids: tuple[str, ...]
    duration_seconds: float | None
    beats_a_still: bool


@dataclass(frozen=True)
class StoredAssetAnnotationFacts:
    """The immutable stored half of one asset's annotation evidence."""

    asset_id: str
    people: tuple[StoredPersonFact, ...] = ()
    description: str | None = None
    setting: str | None = None
    exposure: str | None = None
    heads: tuple[tuple[str, str], ...] = ()
    flags: tuple[StoredFlagFact, ...] = ()
    pixel: StoredPixelFacts | None = None
    motion: StoredMotionBurstFact | None = None


@dataclass
class _MutableAssetFacts:
    people: list[StoredPersonFact] = field(default_factory=list)
    description: str | None = None
    setting: str | None = None
    exposure: str | None = None
    heads: dict[str, str] = field(default_factory=dict)
    flags: list[StoredFlagFact] = field(default_factory=list)
    pixel: StoredPixelFacts | None = None
    motion: StoredMotionBurstFact | None = None


@dataclass(frozen=True)
class AssetAnnotationFactBatch:
    """A complete store read, or a typed unavailable result with no partial facts."""

    requested_asset_ids: tuple[str, ...]
    facts: tuple[StoredAssetAnnotationFacts, ...]
    unavailable_asset_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def as_mapping(self) -> Mapping[str, StoredAssetAnnotationFacts]:
        """Return facts in the caller's stable requested order."""
        return {fact.asset_id: fact for fact in self.facts}


class AssetAnnotationFactRepository:
    """Fetch one complete annotation-fact snapshot without writing the store."""

    def __init__(
        self,
        store_path: Path,
        *,
        description_model: str,
        head_versions: Mapping[str, str],
        pixel_producer_key: str,
    ) -> None:
        self._store_path = Path(store_path)
        self._description_model = description_model
        self._head_versions = dict(head_versions)
        self._pixel_producer_key = pixel_producer_key

    def facts_for(self, asset_ids: tuple[str, ...]) -> AssetAnnotationFactBatch:
        """Read exact-producer facts for the unique requested asset IDs."""
        ordered_ids = tuple(dict.fromkeys(asset_ids))
        if not ordered_ids:
            raise ValueError("annotation fact read needs at least one asset ID")
        records = {asset_id: _MutableAssetFacts() for asset_id in ordered_ids}
        try:
            uri = f"{self._store_path.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                self._read_facts(connection, ordered_ids, records)
        except (OSError, sqlite3.Error):
            return AssetAnnotationFactBatch(
                requested_asset_ids=ordered_ids,
                facts=(),
                unavailable_asset_ids=ordered_ids,
                warnings=("!! annotation fact store unavailable",),
            )
        return AssetAnnotationFactBatch(
            requested_asset_ids=ordered_ids,
            facts=tuple(_freeze(asset_id, records[asset_id]) for asset_id in ordered_ids),
            unavailable_asset_ids=(),
        )

    def _read_facts(
        self,
        connection: sqlite3.Connection,
        asset_ids: tuple[str, ...],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _annotation_wanted (asset_id TEXT PRIMARY KEY)"
        )
        connection.executemany(
            "INSERT OR IGNORE INTO _annotation_wanted VALUES (?)",
            ((asset_id,) for asset_id in asset_ids),
        )
        self._read_people(connection, records)
        self._read_descriptions(connection, records)
        self._read_description_fields(connection, records)
        self._read_flags(connection, records)
        self._read_heads(connection, records)
        self._read_pixels(connection, records)
        self._read_motion(connection, records)

    def _read_people(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        rows = connection.execute(
            "SELECT p.asset_id, p.person_name, p.person_id, p.birth_date "
            "FROM asset_people p "
            "JOIN _annotation_wanted w ON w.asset_id = p.asset_id "
            "ORDER BY p.asset_id, lower(trim(p.person_name)), p.person_name, "
            "p.person_id, p.birth_date"
        )
        for asset_id, name, person_id, born in rows:
            records[str(asset_id)].people.append(
                StoredPersonFact(
                    person_id=_clean(person_id),
                    name=_clean(name),
                    birth_date=_as_date(born),
                )
            )

    def _read_descriptions(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        rows = connection.execute(
            "SELECT d.asset_id, d.text FROM descriptions d "
            "JOIN _annotation_wanted w ON w.asset_id = d.asset_id "
            "WHERE d.model = ? ORDER BY d.asset_id, d.text",
            (self._description_model,),
        )
        for asset_id, text in rows:
            records[str(asset_id)].description = _clean(text) or None

    def _read_description_fields(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        rows = connection.execute(
            "SELECT d.asset_id, d.field, d.value FROM description_fields d "
            "JOIN _annotation_wanted w ON w.asset_id = d.asset_id "
            "WHERE d.model = ? ORDER BY d.asset_id, d.field, d.value",
            (self._description_model,),
        )
        for asset_id, name, value in rows:
            record = records[str(asset_id)]
            cleaned = _clean(value)
            if str(name) == "setting" and cleaned:
                record.setting = cleaned
            elif str(name) == "exposure" and cleaned:
                record.exposure = cleaned

    def _read_flags(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        rows = connection.execute(
            "SELECT f.asset_id, f.flag, f.evidence, f.source FROM flags f "
            "JOIN _annotation_wanted w ON w.asset_id = f.asset_id "
            "ORDER BY f.asset_id, f.flag, f.evidence, f.source"
        )
        for asset_id, flag, evidence, source in rows:
            cleaned_source = _clean(source)
            if "exposure" in cleaned_source.casefold():
                continue
            records[str(asset_id)].flags.append(
                StoredFlagFact(
                    flag=_clean(flag),
                    reason=_flag_reason(evidence),
                    source=cleaned_source,
                )
            )

    def _read_heads(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        rows = connection.execute(
            "SELECT h.asset_id, h.head, h.version, h.label FROM head_facts h "
            "JOIN _annotation_wanted w ON w.asset_id = h.asset_id "
            "ORDER BY h.asset_id, h.head, h.version, h.label"
        )
        for asset_id, head, version, label in rows:
            head_name = str(head)
            if self._head_versions.get(head_name) == str(version):
                records[str(asset_id)].heads[head_name] = _clean(label)

    def _read_pixels(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        threshold_row = connection.execute(
            "SELECT value FROM pixel_facts_thresholds "
            "WHERE name = 'sharpness_p10' AND producer_key = ? "
            "ORDER BY value LIMIT 1",
            (self._pixel_producer_key,),
        ).fetchone()
        soft_below = (
            float(threshold_row[0])
            if threshold_row is not None and threshold_row[0] is not None
            else None
        )
        rows = connection.execute(
            "SELECT p.asset_id, p.sharpness, p.brightness, p.contrast, "
            "p.dark_fraction, p.bright_fraction, p.needs_rotation "
            "FROM pixel_facts p "
            "JOIN _annotation_wanted w ON w.asset_id = p.asset_id "
            "WHERE p.producer_key = ? "
            "ORDER BY p.asset_id, p.sharpness, p.brightness, p.contrast, "
            "p.dark_fraction, p.bright_fraction, p.needs_rotation",
            (self._pixel_producer_key,),
        )
        for asset_id, sharpness, brightness, contrast, dark, bright, rotated in rows:
            records[str(asset_id)].pixel = StoredPixelFacts(
                sharpness=_as_float(sharpness),
                brightness=_as_float(brightness),
                contrast=_as_float(contrast),
                dark_fraction=_as_float(dark),
                bright_fraction=_as_float(bright),
                needs_rotation=bool(rotated),
                soft_below=soft_below,
            )

    def _read_motion(
        self, connection: sqlite3.Connection, records: dict[str, _MutableAssetFacts]
    ) -> None:
        try:
            rows = connection.execute(
                "SELECT m.asset_id, m.burst_id, m.still_ids, "
                "m.duration_seconds, m.beats_a_still FROM motion_bursts m "
                "JOIN _annotation_wanted w ON w.asset_id = m.asset_id "
                "ORDER BY m.asset_id, m.burst_id, m.still_ids, "
                "m.duration_seconds, m.beats_a_still"
            )
            for asset_id, burst_id, still_ids, duration, beats in rows:
                records[str(asset_id)].motion = StoredMotionBurstFact(
                    burst_id=_clean(burst_id),
                    still_ids=_json_strings(still_ids),
                    duration_seconds=_as_float(duration),
                    beats_a_still=bool(beats),
                )
        except sqlite3.OperationalError as exc:
            if not _is_missing_motion_table(exc):
                raise


def _freeze(asset_id: str, record: _MutableAssetFacts) -> StoredAssetAnnotationFacts:
    return StoredAssetAnnotationFacts(
        asset_id=asset_id,
        people=tuple(record.people),
        description=record.description,
        setting=record.setting,
        exposure=record.exposure,
        heads=tuple(sorted(record.heads.items())),
        flags=tuple(sorted(record.flags, key=lambda item: (item.flag, item.reason, item.source))),
        pixel=record.pixel,
        motion=record.motion,
    )


def _flag_reason(value: object) -> str:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return ""
    return _clean(payload.get("reason")) if isinstance(payload, dict) else ""


def _json_strings(value: object) -> tuple[str, ...]:
    try:
        payload = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(str(item) for item in payload if str(item).strip())


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float, str)) else None


def _is_missing_motion_table(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).casefold()
    return "no such table" in message and "motion_bursts" in message


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())
