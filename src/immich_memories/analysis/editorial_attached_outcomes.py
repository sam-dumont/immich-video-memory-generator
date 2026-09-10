"""Seal one attached-acquisition attempt without caching transient failures globally."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from immich_memories.analysis.editorial_bound_sample import BoundVideoSample
from immich_memories.security import write_secret_file

SCHEMA = "attached-attempt-outcomes-v1"
REFERENCE_NAME = "attached-outcomes-reference.private.json"
_REASONS = {
    "metadata": "missing_captured_video_metadata",
    "fetch": "playback_unavailable_this_attempt",
    "decode": "frame_unavailable_this_attempt",
}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def request_key(request: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_outcome(outcome: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(outcome, dict):
        raise ValueError("invalid attached outcome")
    if outcome.get("status") == "available":
        _validate_available_outcome(outcome, request)
    elif not _is_valid_unavailable_outcome(outcome, request):
        raise ValueError("invalid unavailable attached outcome")


def _validate_available_outcome(outcome: dict[str, Any], request: dict[str, Any]) -> None:
    if set(outcome) != {"status", "sample"} or not isinstance(outcome["sample"], dict):
        raise ValueError("invalid available attached outcome")
    try:
        sample = BoundVideoSample.from_dict(outcome["sample"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid attached sample binding") from exc
    expected = {
        "source_id": sample.source_id,
        "parent_ids": list(sample.parent_ids),
        "link_sha256": sample.link_sha256,
        "metadata_sha256": sample.metadata_sha256,
        "start": sample.start,
        "end": sample.end,
        "extractor": sample.extractor_version,
    }
    if request != expected or sample.timestamp != sample.start + (sample.end - sample.start) / 2:
        raise ValueError("attached outcome does not bind its exact midpoint request")


def _is_valid_unavailable_outcome(outcome: dict[str, Any], request: dict[str, Any]) -> bool:
    fields = {"status", "reason", "phase", "error_type", "http_status", "payload_sha256"}
    phase = outcome.get("phase")
    if (
        set(outcome) != fields
        or outcome.get("status") != "unavailable"
        or not isinstance(phase, str)
        or phase not in _REASONS
        or outcome["reason"] != _REASONS[phase]
    ):
        return False
    payload_bound = (
        _is_digest(outcome["payload_sha256"])
        if phase == "decode"
        else outcome["payload_sha256"] is None
    )
    return (
        _is_named_error(outcome["error_type"])
        and _is_transport_status(outcome["http_status"])
        and payload_bound
        and (phase == "metadata") == (request.get("metadata_sha256") is None)
    )


def _is_named_error(value: Any) -> bool:
    return value is None or isinstance(value, str) and value.isidentifier()


def _is_transport_status(value: Any) -> bool:
    return value is None or type(value) is int and 100 <= value <= 599


def _validate_complete(record: dict[str, Any]) -> None:
    fields = {"schema", "scope", "material", "requests", "outcomes", "complete"}
    if (
        not isinstance(record, dict)
        or set(record) != fields
        or record["schema"] != SCHEMA
        or record["complete"] is not True
        or not isinstance(record["scope"], dict)
        or not isinstance(record["material"], dict)
        or not isinstance(record["requests"], dict)
        or not isinstance(record["outcomes"], dict)
        or record["requests"].keys() != record["outcomes"].keys()
    ):
        raise ValueError("attached replay requires one complete exact attempt")
    for key, request in record["requests"].items():
        if not isinstance(request, dict) or request_key(request) != key:
            raise ValueError("invalid attached request identity")
        _validate_outcome(record["outcomes"][key], request)


@dataclass(frozen=True)
class AttachedOutcomeReplay:
    """An explicitly selected completed attempt, never an implicit negative cache."""

    path: Path
    sha256: str

    def __post_init__(self) -> None:
        if not _is_digest(self.sha256):
            raise ValueError("attached replay requires an exact SHA256")

    def read(self) -> dict[str, Any]:
        payload = self.path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != self.sha256:
            raise ValueError("attached replay artifact hash mismatch")
        record = json.loads(payload)
        _validate_complete(record)
        return record

    @classmethod
    def from_output(cls, output: Path) -> AttachedOutcomeReplay:
        record = json.loads((output / REFERENCE_NAME).read_text())
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "path", "sha256"}
            or record["schema"] != SCHEMA
        ):
            raise ValueError("invalid attached outcome reference")
        reference = cls(Path(record["path"]), record["sha256"])
        reference.read()
        return reference

    def as_record(self) -> dict[str, str]:
        return {"schema": SCHEMA, "path": str(self.path), "sha256": self.sha256}


class AttachedAttemptOutcomes:
    """Bind a complete final demand set before any fetch, decode or model observation."""

    def __init__(
        self, *, output: Path, scope: dict[str, Any], replay: AttachedOutcomeReplay | None = None
    ) -> None:
        if not isinstance(scope, dict):
            raise ValueError("attached attempt requires an explicit source/config scope")
        self.path = output / f"attached-outcomes-{uuid4().hex}.private.json"
        self.reference_path = output / REFERENCE_NAME
        self.record = {
            "schema": SCHEMA,
            "scope": _copy(scope),
            "material": None,
            "requests": {},
            "outcomes": {},
            "complete": False,
        }
        self.replay = replay.read() if replay is not None else None
        if self.replay is not None and self.replay["scope"] != self.record["scope"]:
            raise ValueError("attached replay source/config scope mismatch")
        self._write()

    def begin(self, material: dict[str, Any], requests: dict[str, dict[str, Any]]) -> None:
        if self.record["material"] is not None or not isinstance(material, dict):
            raise ValueError("attached material must begin exactly once")
        if any(request_key(value) != key for key, value in requests.items()):
            raise ValueError("invalid attached request identity")
        material, requests = _copy(material), _copy(requests)
        if self.replay is not None and (
            self.replay["material"] != material or self.replay["requests"] != requests
        ):
            raise ValueError("attached replay material or complete request set changed")
        self.record.update(material=material, requests=requests)
        self._write()

    def prior(self, key: str) -> dict[str, Any] | None:
        if (
            self.record["material"] is None
            or self.record["complete"]
            or key not in self.record["requests"]
        ):
            raise ValueError("attached acquisition is outside the open declared material")
        return _copy(self.replay["outcomes"][key]) if self.replay is not None else None

    def observed(self, key: str, outcome: dict[str, Any]) -> None:
        prior = self.prior(key)
        _validate_outcome(outcome, self.record["requests"][key])
        if prior is not None and prior != outcome:
            raise ValueError("attached replay outcome changed")
        existing = self.record["outcomes"].get(key)
        if existing is not None and existing != outcome:
            raise ValueError("attached outcome changed within one attempt")
        self.record["outcomes"][key] = _copy(outcome)
        self._write()

    def finish(self) -> None:
        if self.record["material"] is None or self.record["complete"]:
            raise ValueError("attached material must finish exactly once after beginning")
        if self.record["requests"].keys() != self.record["outcomes"].keys():
            raise ValueError("attached attempt is missing demanded outcomes")
        self.record["complete"] = True
        _validate_complete(self.record)
        self._write()

    def _write(self) -> None:
        payload = json.dumps(self.record, sort_keys=True, indent=2, allow_nan=False)
        write_secret_file(self.path, payload)
        reference = AttachedOutcomeReplay(self.path, hashlib.sha256(payload.encode()).hexdigest())
        write_secret_file(self.reference_path, json.dumps(reference.as_record(), indent=2))
