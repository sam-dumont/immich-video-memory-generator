"""Content-bound sampled evidence; sample identities are never library asset IDs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from immich_memories.api.models import Asset


def source_metadata_digest(asset: Asset) -> str:
    encoded = json.dumps(asset.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def attached_link_digest(parents: tuple[Asset, ...], video_id: str) -> str:
    """Bind each real still's complete source metadata and declared attachment."""
    if not parents or any(
        parent.is_video or parent.live_photo_video_id != video_id for parent in parents
    ):
        raise ValueError("attached evidence needs the admitted still's declared video link")
    if len({parent.id for parent in parents}) != len(parents):
        raise ValueError("attached parent lineage repeats a still")
    material = [(parent.id, source_metadata_digest(parent)) for parent in parents]
    encoded = json.dumps(material, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class BoundVideoSample:
    """Describe an actual frame of admitted attached material, without granting admission.

    Authorization belongs to the source provider. These fields conserve that provider's
    parent/link identity, actual video metadata and bytes, and exact displayed interval.
    Identical video IDs with different intervals or payloads are different evidence.
    """

    source_id: str
    parent_ids: tuple[str, ...]
    link_sha256: str
    metadata_sha256: str
    payload_sha256: str
    start: float
    end: float
    timestamp: float
    frame_sha256: str
    extractor_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("sample requires its real video source ID")
        if (
            not isinstance(self.parent_ids, tuple)
            or not self.parent_ids
            or any(not isinstance(value, str) or not value.strip() for value in self.parent_ids)
            or len(set(self.parent_ids)) != len(self.parent_ids)
            or self.source_id in self.parent_ids
        ):
            raise ValueError("attached sample requires distinct admitted still parents")
        for value in (
            self.link_sha256,
            self.metadata_sha256,
            self.payload_sha256,
            self.frame_sha256,
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("sample provenance requires exact SHA-256 digests")
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in (self.start, self.end, self.timestamp)
            )
            or not 0 <= self.start <= self.timestamp < self.end
        ):
            raise ValueError("sample must lie inside a positive finite displayed interval")
        if not isinstance(self.extractor_version, str) or not self.extractor_version.strip():
            raise ValueError("sample requires a versioned frame extractor")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["parent_ids"] = list(self.parent_ids)
        # Equivalent numeric source coordinates have one identity.
        for key in ("start", "end", "timestamp"):
            result[key] = float(result[key])
        return {"schema": "attached-video-sample-v1"} | result

    @property
    def key(self) -> str:
        encoded = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()
        return "video-sample-" + hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BoundVideoSample:
        data = value.copy()
        if data.pop("schema", None) != "attached-video-sample-v1":
            raise ValueError("unknown attached sample schema")
        parents = data.get("parent_ids")
        if not isinstance(parents, list):
            raise ValueError("sample parent lineage must be a JSON array")
        data["parent_ids"] = tuple(parents)
        return cls(**data)
