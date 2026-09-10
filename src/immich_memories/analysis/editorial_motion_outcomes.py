"""Replay one attempt's unavailable motion without poisoning shared measurements."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from immich_memories.analysis.editorial_numbers import exact_number
from immich_memories.security import write_secret_file

SCHEMA = "motion-attempt-outcomes-v1"
REFERENCE_NAME = "motion-outcomes-reference.private.json"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_bound_unit(unit: object, key: str) -> bool:
    """A retained unit is its own identity: exact membership, primary and sampled sources."""
    if (
        not isinstance(unit, dict)
        or set(unit) != {"primary", "members", "raw_seconds", "sampled_sources"}
        or _digest(unit) != key
        or not isinstance(unit["members"], list)
        or not all(isinstance(value, str) for value in unit["members"])
        or len(set(unit["members"])) != len(unit["members"])
        or unit["primary"] not in unit["members"]
    ):
        return False
    seconds = exact_number(unit["raw_seconds"])
    if seconds is None or seconds < 0:
        return False
    sources = unit["sampled_sources"]
    return (
        isinstance(sources, list)
        and all(_is_digest(value) for value in sources)
        and len(set(sources)) == len(sources)
    )


def _is_recorded_outcome(value: dict) -> bool:
    if value.get("status") == "measured":
        return set(value) == {"status", "facts_sha256"} and _is_digest(value["facts_sha256"])
    return (
        set(value) == {"status", "error_type", "phase", "http_status"}
        and value["status"] == "unavailable"
        and isinstance(value["error_type"], str)
        and value["error_type"].isidentifier()
        and value["phase"] in {"fetch", "decode"}
        and (
            value["http_status"] is None
            or type(value["http_status"]) is int
            and 100 <= value["http_status"] <= 599
        )
    )


def _validate_unit_row(key: str, row: object) -> None:
    if not isinstance(row, dict) or set(row) != {"unit", "outcomes"}:
        raise ValueError("invalid motion replay unit")
    unit = row["unit"]
    if (
        not _is_bound_unit(unit, key)
        or not isinstance(row["outcomes"], dict)
        or set(row["outcomes"]) != set(unit["sampled_sources"])
    ):
        raise ValueError("incomplete or invalid motion replay membership")
    for value in row["outcomes"].values():
        if not isinstance(value, dict) or not _is_recorded_outcome(value):
            raise ValueError("invalid motion replay outcome")


@dataclass(frozen=True)
class MotionOutcomeReplay:
    """An explicitly named prior attempt, never an implicit cache lookup."""

    path: Path
    sha256: str

    def __post_init__(self) -> None:
        if not _is_digest(self.sha256):
            raise ValueError("motion replay requires an exact SHA256")

    def read(self) -> dict:
        payload = self.path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != self.sha256:
            raise ValueError("motion replay artifact hash mismatch")
        record = json.loads(payload)
        if not isinstance(record, dict):
            raise ValueError("motion replay artifact must be an object")
        return record

    @classmethod
    def from_output(cls, output: Path) -> MotionOutcomeReplay:
        """Capture this exact reference after the attempt finishes, before replay."""
        record = json.loads((output / REFERENCE_NAME).read_text())
        if set(record) != {"schema", "path", "sha256"} or record["schema"] != SCHEMA:
            raise ValueError("invalid motion outcome reference")
        reference = cls(Path(record["path"]), record["sha256"])
        reference.read()
        return reference

    def as_record(self) -> dict:
        return {"schema": SCHEMA, "path": str(self.path), "sha256": self.sha256}


class MotionAttemptOutcomes:
    """One closure-owned journal spans every retained-motion completion batch."""

    def __init__(self, *, output: Path, scope: dict, replay: MotionOutcomeReplay | None = None):
        self.path = output / f"motion-outcomes-{uuid4().hex}.private.json"
        self.reference_path = output / REFERENCE_NAME
        self.record: dict = {"schema": SCHEMA, "scope": scope, "units": {}}
        self.replay = replay.read() if replay is not None else None
        if self.replay is not None:
            self._validate(self.replay, scope)
        self._write()

    @staticmethod
    def _validate(record: dict, scope: dict) -> None:
        if (
            set(record) != {"schema", "scope", "units"}
            or record["schema"] != SCHEMA
            or record["scope"] != scope
            or not isinstance(record["units"], dict)
        ):
            raise ValueError("motion replay scope or schema mismatch")
        for key, row in record["units"].items():
            _validate_unit_row(key, row)

    def begin(self, carrier: dict, source_keys: list[str]) -> str:
        unit = {
            "primary": carrier["asset_id"],
            "members": list(carrier["members"]),
            "raw_seconds": carrier["raw_seconds"],
            "sampled_sources": source_keys,
        }
        key = _digest(unit)
        if self.replay is not None and key not in self.replay["units"]:
            raise ValueError("motion replay has no exact retained unit membership")
        self.record["units"].setdefault(key, {"unit": unit, "outcomes": {}})
        if not source_keys:
            self._write()
        return key

    def prior(self, unit_key: str, source_key: str) -> dict | None:
        if self.replay is None:
            return None
        return self.replay["units"][unit_key]["outcomes"][source_key]

    def observed(self, unit_key: str, source_key: str, fact: dict, *, error: dict | None = None):
        outcome = (
            error if error is not None else {"status": "measured", "facts_sha256": _digest(fact)}
        )
        prior = self.prior(unit_key, source_key)
        if prior is not None and prior != outcome:
            raise ValueError("motion replay measurement differs from the recorded outcome")
        existing = self.record["units"][unit_key]["outcomes"].get(source_key)
        if existing is not None and existing != outcome:
            raise ValueError("motion outcome changed within one attempt")
        self.record["units"][unit_key]["outcomes"][source_key] = outcome
        self._write()

    def _write(self) -> None:
        payload = json.dumps(self.record, sort_keys=True, indent=2)
        write_secret_file(self.path, payload)
        reference = MotionOutcomeReplay(self.path, hashlib.sha256(payload.encode()).hexdigest())
        write_secret_file(self.reference_path, json.dumps(reference.as_record(), indent=2))
