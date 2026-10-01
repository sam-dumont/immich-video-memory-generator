"""Read immutable annotation facts from the configured library database."""

from __future__ import annotations

import json
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from immich_memories.analysis.llm_caption_identity import LLM_CAPTION_PREFIX
from immich_memories.analysis.subject_framing import FaceBox
from immich_memories.db import Store
from immich_memories.db.tables import (
    asset_flags,
    asset_people,
    description_fields,
    descriptions,
    face_boxes,
    head_facts,
    motion_bursts,
    pixel_facts,
    pixel_facts_thresholds,
)
from immich_memories.store.batches import id_in, in_chunks
from immich_memories.store.caption_selection import selected_captions


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
    faces: tuple[FaceBox, ...] = ()
    description: str | None = None
    setting: str | None = None
    exposure: str | None = None
    heads: tuple[tuple[str, str], ...] = ()
    flags: tuple[StoredFlagFact, ...] = ()
    pixel: StoredPixelFacts | None = None
    motion: StoredMotionBurstFact | None = None
    head_confidences: tuple[tuple[str, float], ...] = ()


@dataclass
class _MutableAssetFacts:
    people: list[StoredPersonFact] = field(default_factory=list)
    faces: list[FaceBox] = field(default_factory=list)
    description: str | None = None
    setting: str | None = None
    exposure: str | None = None
    heads: dict[str, str] = field(default_factory=dict)
    flags: list[StoredFlagFact] = field(default_factory=list)
    pixel: StoredPixelFacts | None = None
    motion: StoredMotionBurstFact | None = None
    head_confidences: dict[str, float] = field(default_factory=dict)


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
    """Read banked facts; a null description producer excludes captions and their fields."""

    def __init__(
        self,
        store: Store,
        *,
        description_model: str | None,
        head_versions: Mapping[str, str],
        pixel_producer_key: str,
    ) -> None:
        self._store = store
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
            with self._store.connect() as connection:
                self._read_facts(connection, ordered_ids, records)
        except (OSError, SQLAlchemyError):
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
        connection: Connection,
        asset_ids: tuple[str, ...],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        self._read_people(connection, asset_ids, records)
        self._read_faces(connection, asset_ids, records)
        if self._description_model is not None:
            if self._description_model.startswith(LLM_CAPTION_PREFIX):
                for asset_id, caption in selected_captions(
                    connection, asset_ids, self._description_model
                ).items():
                    records[asset_id].description = caption.envelope.description
                    records[asset_id].setting = caption.envelope.setting
            else:
                self._read_descriptions(connection, asset_ids, records)
                self._read_description_fields(connection, asset_ids, records)
        self._read_flags(connection, asset_ids, records)
        self._read_heads(connection, asset_ids, records)
        self._read_pixels(connection, asset_ids, records)
        self._read_motion(connection, asset_ids, records)

    def _read_people(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        p = asset_people
        rows = _rows(
            connection,
            sa.select(p.c.asset_id, p.c.person_name, p.c.person_id, p.c.birth_date),
            p.c.asset_id,
            asset_ids,
        )
        # The order the file's SQL gave: SQLite's lower() and trim() touch ASCII and spaces only.
        rows.sort(
            key=lambda r: (
                r[0],
                _nulls_first(_ascii_lower(r[1].strip(" ")) if r[1] is not None else None),
                *(_nulls_first(value) for value in r[1:]),
            )
        )
        for asset_id, name, person_id, born in rows:
            records[str(asset_id)].people.append(
                StoredPersonFact(
                    person_id=_clean(person_id),
                    name=_clean(name),
                    birth_date=_as_date(born),
                )
            )

    def _read_faces(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        b = face_boxes
        rows = _rows(
            connection,
            sa.select(b.c.asset_id, b.c.named, b.c.x1, b.c.y1, b.c.x2, b.c.y2, b.c.person_id),
            b.c.asset_id,
            asset_ids,
        )
        rows.sort(key=lambda r: (r[0], *(_nulls_first(value) for value in r[2:6])))
        for asset_id, named, x1, y1, x2, y2, person_id in rows:
            records[str(asset_id)].faces.append(
                FaceBox(
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    named=bool(named),
                    person_id=_clean(person_id) or None,
                )
            )

    def _read_descriptions(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        d = descriptions
        rows = _rows(
            connection,
            sa.select(d.c.asset_id, d.c.text).where(d.c.model == self._description_model),
            d.c.asset_id,
            asset_ids,
        )
        for asset_id, text in rows:
            records[str(asset_id)].description = _clean(text) or None

    def _read_description_fields(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        d = description_fields
        rows = _rows(
            connection,
            sa.select(d.c.asset_id, d.c.field, d.c.value).where(
                d.c.model == self._description_model
            ),
            d.c.asset_id,
            asset_ids,
        )
        for asset_id, name, value in rows:
            record = records[str(asset_id)]
            cleaned = _clean(value)
            if str(name) == "setting" and cleaned:
                record.setting = cleaned
            elif str(name) == "exposure" and cleaned:
                record.exposure = cleaned

    def _read_flags(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        f = asset_flags
        rows = _rows(
            connection,
            sa.select(f.c.asset_id, f.c.flag, f.c.evidence, f.c.source),
            f.c.asset_id,
            asset_ids,
        )
        rows.sort(key=lambda r: tuple(_nulls_first(value) for value in r))
        for asset_id, flag, evidence, source in rows:
            cleaned_source = _clean(source)
            # The owner's clear-hold and never-use act on the gate and the material, never on
            # what a reader is told about the picture (store/owner_decisions.py).
            if "exposure" in cleaned_source.casefold() or cleaned_source == "owner":
                continue
            records[str(asset_id)].flags.append(
                StoredFlagFact(
                    flag=_clean(flag),
                    reason=_flag_reason(evidence),
                    source=cleaned_source,
                )
            )

    def _read_heads(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        h = head_facts
        rows = _rows(
            connection,
            sa.select(h.c.asset_id, h.c.head, h.c.version, h.c.label, h.c.confidence),
            h.c.asset_id,
            asset_ids,
        )
        for asset_id, head, version, label, confidence in rows:
            head_name = str(head)
            if self._head_versions.get(head_name) == str(version):
                records[str(asset_id)].heads[head_name] = _clean(label)
                if confidence is not None:
                    records[str(asset_id)].head_confidences[head_name] = float(confidence)

    def _read_pixels(
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        t = pixel_facts_thresholds
        soft_below = _as_float(
            connection.execute(
                sa.select(sa.func.min(t.c.value)).where(
                    t.c.name == "sharpness_p10", t.c.producer_key == self._pixel_producer_key
                )
            ).scalar()
        )
        p = pixel_facts
        rows = _rows(
            connection,
            sa.select(
                p.c.asset_id,
                p.c.sharpness,
                p.c.brightness,
                p.c.contrast,
                p.c.dark_fraction,
                p.c.bright_fraction,
                p.c.needs_rotation,
            ).where(p.c.producer_key == self._pixel_producer_key),
            p.c.asset_id,
            asset_ids,
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
        self,
        connection: Connection,
        asset_ids: Sequence[str],
        records: dict[str, _MutableAssetFacts],
    ) -> None:
        m = motion_bursts
        rows = _rows(
            connection,
            sa.select(
                m.c.asset_id, m.c.burst_id, m.c.still_ids, m.c.duration_seconds, m.c.beats_a_still
            ),
            m.c.asset_id,
            asset_ids,
        )
        for asset_id, burst_id, still_ids, duration, beats in rows:
            records[str(asset_id)].motion = StoredMotionBurstFact(
                burst_id=_clean(burst_id),
                still_ids=_json_strings(still_ids),
                duration_seconds=_as_float(duration),
                beats_a_still=bool(beats),
            )


def _rows(
    connection: Connection, query: sa.Select, key: sa.ColumnElement, asset_ids: Sequence[str]
) -> list[Any]:
    rows: list[Any] = []
    for chunk in in_chunks(connection, asset_ids):
        rows.extend(connection.execute(query.where(id_in(connection, key, chunk))))
    return rows


def _nulls_first(value: object) -> tuple[bool, object]:
    # SQLite orders NULL before every value; a missing value never compares with a present one.
    return (value is not None, value if value is not None else 0)


def _ascii_lower(value: str) -> str:
    return value.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _freeze(asset_id: str, record: _MutableAssetFacts) -> StoredAssetAnnotationFacts:
    return StoredAssetAnnotationFacts(
        asset_id=asset_id,
        people=tuple(record.people),
        faces=tuple(record.faces),
        description=record.description,
        setting=record.setting,
        exposure=record.exposure,
        heads=tuple(sorted(record.heads.items())),
        flags=tuple(sorted(record.flags, key=lambda item: (item.flag, item.reason, item.source))),
        pixel=record.pixel,
        motion=record.motion,
        head_confidences=tuple(sorted(record.head_confidences.items())),
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


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())
