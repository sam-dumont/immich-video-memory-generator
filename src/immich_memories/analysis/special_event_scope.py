"""Exact discovered-event membership, shared by product and validation entry points."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TypeVar

from immich_memories.api.models import Asset, VideoClipInfo

_Source = TypeVar("_Source", bound=Asset | VideoClipInfo)


def validate_special_event_scope(
    event_id: str | None, asset_ids: Sequence[str], *, product: str = "special_day"
) -> tuple[str, ...]:
    """Reject incomplete scopes rather than silently expanding an event to its day."""
    if isinstance(asset_ids, (str, bytes)):
        raise ValueError("special event membership must be a sequence of asset IDs")
    members = tuple(asset_ids)
    if event_id is None and not members:
        return ()
    if product != "special_day":
        raise ValueError("exact special event membership requires the special_day product")
    if not isinstance(event_id, str) or not event_id.strip() or not members:
        raise ValueError("special event identity and nonempty membership must travel together")
    if any(not isinstance(value, str) or not value.strip() for value in members):
        raise ValueError("special event members must be nonblank asset IDs")
    if len(set(members)) != len(members):
        raise ValueError("special event members must be unique")
    digest = hashlib.sha256(json.dumps(sorted(members), separators=(",", ":")).encode()).hexdigest()
    if event_id != f"special-event-{digest[:24]}":
        raise ValueError("special event identity does not match its canonical membership")
    return members


@dataclass(frozen=True)
class SpecialEventAdmission:
    """Explicit prior admission, bound to its event and existing evidence.

    A canonical event ID alone only defines a provisional source scope. Catalogue
    readers and explicit owner-confirmed inputs create this record; no prompt,
    event name, picture count, or person presence may infer it. Evidence references
    and raw source IDs are private audit data, never model prompt material.
    """

    origin: str
    event_id: str
    membership_sha256: str
    evidence_sha256: str
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.origin not in {"catalogue", "owner_confirmed"}:
            raise ValueError("unknown special event admission origin")
        for digest in (self.membership_sha256, self.evidence_sha256):
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("special event admission needs full SHA256 evidence identities")
        if self.event_id != f"special-event-{self.membership_sha256[:24]}":
            raise ValueError("special event admission identity does not match its membership")
        if not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip():
            raise ValueError("special event admission needs an existing evidence reference")

    def validate_scope(self, event_id: str | None, members: Sequence[str], *, product: str) -> None:
        actual = validate_special_event_scope(event_id, members, product=product)
        digest = hashlib.sha256(
            json.dumps(sorted(actual), separators=(",", ":")).encode()
        ).hexdigest()
        if self.event_id != event_id or self.membership_sha256 != digest:
            raise ValueError("special event admission does not bind the selected event scope")

    def as_record(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_catalogue_record(cls, record: Mapping, *, evidence_ref: str) -> SpecialEventAdmission:
        """Called only by the accepted catalogue reader, on the exact existing row."""
        members = validate_special_event_scope(record.get("event_id"), record.get("asset_ids", ()))
        if not members:
            raise ValueError("legacy day-only catalogue records have no exact event admission")
        return cls(
            origin="catalogue",
            event_id=record["event_id"],
            membership_sha256=hashlib.sha256(
                json.dumps(sorted(members), separators=(",", ":")).encode()
            ).hexdigest(),
            evidence_sha256=hashlib.sha256(
                json.dumps(
                    dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            evidence_ref=evidence_ref,
        )


def read_special_event_admission(value: object) -> SpecialEventAdmission | None:
    """Read explicit provenance; missing input stays provisional, never inferred."""
    if value is None:
        return None
    if isinstance(value, SpecialEventAdmission):
        return value
    fields = {"origin", "event_id", "membership_sha256", "evidence_sha256", "evidence_ref"}
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or any(not isinstance(item, str) for item in value.values())
    ):
        raise ValueError("special event admission needs its complete explicit evidence record")
    return SpecialEventAdmission(**dict(value))


def select_source_members(
    sources: Iterable[_Source], asset_ids: Sequence[str] | None
) -> tuple[_Source, ...]:
    """Intersect visual members while retaining each still's Live Photo link."""
    if asset_ids is None:
        return tuple(sources)
    allowed = set(asset_ids)
    return tuple(
        source
        for source in sources
        if (source.asset if isinstance(source, VideoClipInfo) else source).id in allowed
    )
