#!/usr/bin/env python3
"""Prepare and seal an offline, independently judged fresh400 truth package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.triage_heads.generate_labels import TEACHER_MODEL

FRESH_TRUTH_COUNT = 400
AUDIT_COUNT = 12
LOCATION_CLASSES = frozenset({"indoor", "outdoor", "undetermined"})
DEFAULT_CERTIFICATION_LEDGER_DIR = (
    Path.home()
    / ".immich-memories-matrix"
    / "triage-heads"
    / "recovery-1b"
    / "fresh-certification-attempt-ledger-v1"
)
PIXEL_JUDGE_PROMPT = (
    "Judge only from the attached pixels. Verify visible claims one by one. "
    "Do not inspect asset metadata, teacher labels, candidate outputs, thresholds, or metrics. "
    "Return exactly the constrained photo-card object; use 'insufficient evidence' when the "
    "setting is genuinely undecidable."
)
PIXEL_CARD_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["final_audit_id", "fields"],
    "properties": {
        "final_audit_id": {"type": "string", "minLength": 1},
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "required": ["description", "schema_version", "setting"],
            "properties": {
                "description": {"type": "string", "minLength": 1},
                "schema_version": {"const": "asset-description-v1"},
                "setting": {"type": "string", "minLength": 1},
            },
        },
    },
}
CONCLUSION_PROMPT = (
    "Judge only from the supplied photo-card text. Never infer beyond that text. "
    "Return indoor, outdoor, or undetermined; use undetermined whenever the text does not say."
)
LOCATION_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rid", "location"],
    "properties": {
        "rid": {"type": "string", "minLength": 1},
        "location": {"enum": ["indoor", "outdoor", "undetermined"]},
    },
}
AUDIT_PROMPT = (
    "Independently classify the attached pixels as indoor, outdoor, or undetermined. "
    "Do not inspect descriptions, teacher labels, candidate outputs, thresholds, or metrics."
)
AUDIT_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["final_audit_id", "location"],
    "properties": {
        "final_audit_id": {"type": "string", "minLength": 1},
        "location": {"enum": ["indoor", "outdoor", "undetermined"]},
    },
}


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


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _self_seal(payload: dict[str, object], field: str) -> dict[str, object]:
    sealed = dict(payload)
    sealed[field] = _sha256(_canonical_bytes(payload))
    return sealed


def _stable_digest(seed: str, scope: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{value}".encode()).hexdigest()


def fresh_truth_audit_seed(binding: FreshTruthBinding) -> str:
    """Derive the audit seed from immutable candidate and approval lineage."""
    return _audit_seed_from_binding_manifest(binding.as_manifest())


def _private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    path.chmod(0o700)


def _write_new_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


def _write_create_only_private(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        if _load_private_bytes(path, label=f"existing {path.name}") != payload:
            raise RuntimeError(f"existing {path.name} differs from immutable content")
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
        temporary.chmod(0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _load_private_bytes(path, label=f"existing {path.name}") != payload:
                raise RuntimeError(f"existing {path.name} differs from immutable content")
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        raise RuntimeError(f"created {path.name} is not single-link mode 0600")


def _load_private_bytes(path: Path, *, label: str) -> bytes:
    candidate = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise RuntimeError(f"{label} must be a regular local file") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"{label} must be a regular local file")
        if stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1:
            raise PermissionError(f"{label} must be single-link mode 0600")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_mode,
        before.st_nlink,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
        after.st_nlink,
    )
    if identity_before != identity_after:
        raise RuntimeError(f"{label} changed while it was being snapshotted")
    return raw


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    raw = _load_private_bytes(path, label=label)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON") from error
    if not isinstance(payload, dict) or raw != _canonical_bytes(payload):
        raise RuntimeError(f"{label} must be one canonical JSON object")
    return payload, raw


def _load_canonical_jsonl(path: Path, *, label: str) -> tuple[list[dict[str, object]], bytes]:
    raw = _load_private_bytes(path, label=label)
    rows: list[dict[str, object]] = []
    try:
        for line in raw.splitlines():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"{label} rows must be JSON objects")
            rows.append(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSONL") from error
    if raw != _jsonl_bytes(rows):
        raise RuntimeError(f"{label} must be canonical JSONL")
    return rows, raw


def _require_private_directory(path: Path, *, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError(f"{label} must be a private directory")
    if stat.S_IMODE(candidate.stat().st_mode) != 0o700:
        raise PermissionError(f"{label} must be mode 0700")
    return candidate


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_staged_directory(staging: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(f"{destination.name} already exists; immutable output is create-only")
    os.rename(staging, destination)
    _fsync_directory(destination.parent)


@dataclass(frozen=True, slots=True)
class FreshTruthSource:
    asset_id: str
    final_audit_id: str
    preview_sha256: str
    pixel_bytes: bytes


@dataclass(frozen=True, slots=True)
class FreshTruthBinding:
    head_version: str
    training_run_sha256: str
    candidate_sha256: Mapping[str, str]
    inventory_sha256: str
    selection_sha256: str
    certification_index_sha256: str
    approval_private_sha256: str
    approval_public_sha256: str
    image_set_sha256: str
    pixel_snapshot_sha256: str

    def validate(self) -> None:
        if self.head_version != "location-v1":
            raise ValueError("fresh truth must bind the frozen location-v1 head")
        if set(self.candidate_sha256) != {"linear", "mlp"}:
            raise ValueError("fresh truth must bind exactly the linear and MLP candidates")
        digests = (
            self.training_run_sha256,
            *self.candidate_sha256.values(),
            self.inventory_sha256,
            self.selection_sha256,
            self.certification_index_sha256,
            self.approval_private_sha256,
            self.approval_public_sha256,
            self.image_set_sha256,
            self.pixel_snapshot_sha256,
        )
        if any(not _is_sha256(value) for value in digests):
            raise ValueError("fresh truth lineage requires lowercase SHA-256 digests")

    def as_manifest(self) -> dict[str, object]:
        self.validate()
        return {
            "head_version": self.head_version,
            "training_run_sha256": self.training_run_sha256,
            "candidate_sha256": dict(sorted(self.candidate_sha256.items())),
            "fresh_approval": {
                "inventory_sha256": self.inventory_sha256,
                "selection_sha256": self.selection_sha256,
                "certification_index_sha256": self.certification_index_sha256,
                "approval_private_sha256": self.approval_private_sha256,
                "approval_public_sha256": self.approval_public_sha256,
                "image_set_sha256": self.image_set_sha256,
                "pixel_snapshot_sha256": self.pixel_snapshot_sha256,
            },
        }


def _audit_seed_from_binding_manifest(binding: object) -> str:
    if not isinstance(binding, dict) or set(binding) != {
        "head_version",
        "training_run_sha256",
        "candidate_sha256",
        "fresh_approval",
    }:
        raise RuntimeError("fresh truth binding violates its exact schema")
    candidates = binding.get("candidate_sha256")
    fresh = binding.get("fresh_approval")
    if (
        binding.get("head_version") != "location-v1"
        or not _is_sha256(binding.get("training_run_sha256"))
        or not isinstance(candidates, dict)
        or set(candidates) != {"linear", "mlp"}
        or any(not _is_sha256(value) for value in candidates.values())
        or not isinstance(fresh, dict)
        or set(fresh)
        != {
            "inventory_sha256",
            "selection_sha256",
            "certification_index_sha256",
            "approval_private_sha256",
            "approval_public_sha256",
            "image_set_sha256",
            "pixel_snapshot_sha256",
        }
        or any(not _is_sha256(value) for value in fresh.values())
    ):
        raise RuntimeError("fresh truth binding violates its exact schema")
    payload = _canonical_bytes(binding)
    return hashlib.sha256(b"triage-fresh-truth-audit-seed-v1\0" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class _PreparedTruthSnapshot:
    root: Path
    manifest: Mapping[str, object]
    mapping_rows: tuple[Mapping[str, object], ...]
    pixels: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class _ConclusionWorkSnapshot:
    root: Path
    manifest: Mapping[str, object]
    conclusion_rows: tuple[Mapping[str, object], ...]
    audit_rows: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class FreshTruthPackageSnapshot:
    root: Path
    manifest: Mapping[str, object]
    files: Mapping[str, bytes]
    truth_by_asset: Mapping[str, str]
    teacher_by_asset: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class FreshTruthCommitment:
    truth_manifest_sha256: str
    certification_inputs_sha256: str


@dataclass(frozen=True, slots=True)
class CertificationAttempt:
    root: Path
    ledger_path: Path
    ledger_entry_sha256: str
    attempt_sha256: str
    truth_manifest_sha256: str
    certification_inputs_sha256: str
    binding: Mapping[str, object]
    resumed: bool


@dataclass(frozen=True, slots=True)
class CertificationDecisionSnapshot:
    attempt: CertificationAttempt
    report: Mapping[str, object]
    curves: Mapping[str, object]
    decision: Mapping[str, object]


def _verify_self_digest(payload: Mapping[str, object], *, field: str, label: str) -> str:
    claimed = payload.get(field)
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if not _is_sha256(claimed) or _sha256(_canonical_bytes(unsigned)) != claimed:
        raise RuntimeError(f"{label} self-digest does not reproduce")
    return str(claimed)


def _validate_sealed_manifest_contract(manifest: Mapping[str, object]) -> None:
    binding = manifest.get("binding")
    if not isinstance(binding, dict) or set(binding) != {
        "head_version",
        "training_run_sha256",
        "candidate_sha256",
        "fresh_approval",
    }:
        raise RuntimeError("fresh truth binding violates its exact schema")
    candidates = binding.get("candidate_sha256")
    fresh = binding.get("fresh_approval")
    if (
        binding.get("head_version") != "location-v1"
        or not _is_sha256(binding.get("training_run_sha256"))
        or not isinstance(candidates, dict)
        or set(candidates) != {"linear", "mlp"}
        or any(not _is_sha256(value) for value in candidates.values())
        or not isinstance(fresh, dict)
        or set(fresh)
        != {
            "inventory_sha256",
            "selection_sha256",
            "certification_index_sha256",
            "approval_private_sha256",
            "approval_public_sha256",
            "image_set_sha256",
            "pixel_snapshot_sha256",
        }
        or any(not _is_sha256(value) for value in fresh.values())
    ):
        raise RuntimeError("fresh truth binding violates its exact schema")
    expected_audit_seed = _audit_seed_from_binding_manifest(binding)
    protocol = manifest.get("protocol")
    protocol_keys = {
        "pixel_judge_model",
        "conclusion_judge_model",
        "audit_judge_model",
        "teacher_model",
        "pixel_prompt_sha256",
        "pixel_output_schema_sha256",
        "audit_seed",
        "audit_seed_sha256",
        "audit_count",
        "conclusion_model",
        "conclusion_prompt_sha256",
        "conclusion_output_schema_sha256",
        "audit_model",
        "audit_prompt_sha256",
        "audit_output_schema_sha256",
        "pixel_tasks_sha256",
        "conclusion_tasks_sha256",
        "audit_tasks_sha256",
        "pixel_cards_output_sha256",
        "teacher_cards_output_sha256",
        "conclusions_output_sha256",
        "audit_output_sha256",
        "adjudications_output_sha256",
    }
    string_keys = {
        "pixel_judge_model",
        "conclusion_judge_model",
        "audit_judge_model",
        "teacher_model",
        "audit_seed",
        "conclusion_model",
        "audit_model",
    }
    digest_keys = protocol_keys - string_keys - {"audit_count"}
    if (
        not isinstance(protocol, dict)
        or set(protocol) != protocol_keys
        or protocol.get("audit_count") != AUDIT_COUNT
        or any(not isinstance(protocol.get(key), str) or not protocol[key] for key in string_keys)
        or any(not _is_sha256(protocol.get(key)) for key in digest_keys)
        or protocol.get("audit_seed") != expected_audit_seed
        or _sha256((str(protocol.get("audit_seed")) + "\n").encode())
        != protocol.get("audit_seed_sha256")
        or protocol.get("conclusion_model") != protocol.get("conclusion_judge_model")
        or protocol.get("audit_model") != protocol.get("audit_judge_model")
    ):
        raise RuntimeError("fresh truth judge protocol violates its exact schema")
    inputs = manifest.get("inputs")
    if (
        not isinstance(inputs, dict)
        or set(inputs) != {"prepare_sha256", "conclusion_work_sha256"}
        or any(not _is_sha256(value) for value in inputs.values())
    ):
        raise RuntimeError("fresh truth inputs violate their exact schema")
    audit = manifest.get("audit")
    if (
        not isinstance(audit, dict)
        or set(audit) != {"precommitted_count", "disagreement_count", "adjudicated_count", "passed"}
        or audit.get("precommitted_count") != AUDIT_COUNT
        or type(audit.get("disagreement_count")) is not int
        or type(audit.get("adjudicated_count")) is not int
        or not 0 <= audit["disagreement_count"] <= AUDIT_COUNT
        or audit.get("adjudicated_count") != audit.get("disagreement_count")
        or audit.get("passed") is not True
    ):
        raise RuntimeError("fresh truth audit evidence violates its exact schema")


def _validate_blinded_mapping_commitments(
    mapping_rows: Sequence[Mapping[str, object]],
    *,
    audit_seed: str,
) -> set[str]:
    """Reproduce the opaque RIDs and the precommitted audit sample."""
    if len(mapping_rows) != FRESH_TRUTH_COUNT or not audit_seed:
        raise RuntimeError("fresh truth mapping cannot reproduce its blinded commitments")
    audit_ids = [str(row["final_audit_id"]) for row in mapping_rows]
    if len(set(audit_ids)) != FRESH_TRUTH_COUNT:
        raise RuntimeError("fresh truth mapping has duplicate audit identities")
    expected_audit_ids = {
        str(row["final_audit_id"])
        for row in sorted(
            mapping_rows,
            key=lambda row: _stable_digest(audit_seed, "audit", str(row["final_audit_id"])),
        )[:AUDIT_COUNT]
    }
    selected_audit_ids: set[str] = set()
    all_rids: list[str] = []
    for row in mapping_rows:
        audit_id = str(row["final_audit_id"])
        if type(row["audit_selected"]) is not bool:
            raise RuntimeError("fresh truth mapping audit selection is not boolean")
        if row["audit_selected"] is True:
            selected_audit_ids.add(audit_id)
        truth_rid = str(row["truth_rid"])
        teacher_rid = str(row["teacher_rid"])
        if truth_rid != _stable_digest(audit_seed, "truth-rid", audit_id) or (
            teacher_rid != _stable_digest(audit_seed, "teacher-rid", audit_id)
        ):
            raise RuntimeError("fresh truth blinded RIDs do not reproduce")
        all_rids.extend((truth_rid, teacher_rid))
    if selected_audit_ids != expected_audit_ids:
        raise RuntimeError("fresh truth audit sample does not reproduce")
    if len(set(all_rids)) != 2 * FRESH_TRUTH_COUNT or any(not _is_sha256(rid) for rid in all_rids):
        raise RuntimeError("fresh truth RIDs are invalid or duplicated")
    return selected_audit_ids


def _load_prepared_truth(prepared_dir: Path) -> _PreparedTruthSnapshot:
    root = _require_private_directory(prepared_dir, label="fresh truth preparation")
    if {path.name for path in root.iterdir()} != {"judge", "private"}:
        raise RuntimeError("fresh truth preparation has missing or extra roots")
    judge = _require_private_directory(root / "judge", label="pixel judge bundle")
    private = _require_private_directory(root / "private", label="fresh truth private map")
    images = _require_private_directory(judge / "images", label="pixel judge images")
    if {path.name for path in judge.iterdir()} != {"images", "protocol.json", "tasks.jsonl"}:
        raise RuntimeError("pixel judge bundle has missing or extra files")
    if {path.name for path in private.iterdir()} != {"mapping.jsonl", "prepare-manifest.json"}:
        raise RuntimeError("fresh truth private map has missing or extra files")
    manifest, manifest_raw = _load_canonical_json(
        private / "prepare-manifest.json", label="fresh truth prepare manifest"
    )
    expected_manifest_keys = {
        "schema",
        "status",
        "binding",
        "counts",
        "protocol",
        "files",
        "prepare_sha256",
    }
    if set(manifest) != expected_manifest_keys or (
        manifest.get("schema"),
        manifest.get("status"),
    ) != ("triage-fresh-truth-prepare-v1", "judge-tasks-frozen"):
        raise RuntimeError("fresh truth prepare manifest violates its exact schema")
    _verify_self_digest(manifest, field="prepare_sha256", label="fresh truth prepare manifest")
    expected_audit_seed = _audit_seed_from_binding_manifest(manifest.get("binding"))
    task_rows, task_raw = _load_canonical_jsonl(judge / "tasks.jsonl", label="pixel judge tasks")
    mapping_rows, mapping_raw = _load_canonical_jsonl(
        private / "mapping.jsonl", label="fresh truth private mapping"
    )
    protocol, protocol_raw = _load_canonical_json(
        judge / "protocol.json", label="pixel judge protocol"
    )
    if (
        len(task_rows) != FRESH_TRUTH_COUNT
        or len(mapping_rows) != FRESH_TRUTH_COUNT
        or manifest.get("counts") != {"judge_tasks": 400, "pixels": 400}
    ):
        raise RuntimeError("fresh truth preparation is not exact400")
    task_keys = {"final_audit_id", "image_relpath"}
    mapping_keys = {
        "asset_id",
        "audit_selected",
        "final_audit_id",
        "preview_sha256",
        "teacher_rid",
        "truth_rid",
    }
    if any(set(row) != task_keys for row in task_rows) or any(
        set(row) != mapping_keys for row in mapping_rows
    ):
        raise RuntimeError("fresh truth preparation rows violate their exact schemas")
    task_ids = [str(row["final_audit_id"]) for row in task_rows]
    mapping_ids = [str(row["final_audit_id"]) for row in mapping_rows]
    if (
        len(set(task_ids)) != 400
        or len(set(mapping_ids)) != 400
        or set(task_ids) != set(mapping_ids)
    ):
        raise RuntimeError("fresh truth preparation identity sets do not match")
    if sum(row["audit_selected"] is True for row in mapping_rows) != AUDIT_COUNT:
        raise RuntimeError("fresh truth preparation has the wrong audit sample")
    prepare_protocol = manifest.get("protocol")
    prepare_protocol_keys = {
        "pixel_judge_model",
        "conclusion_judge_model",
        "audit_judge_model",
        "teacher_model",
        "pixel_prompt_sha256",
        "pixel_output_schema_sha256",
        "audit_seed",
        "audit_seed_sha256",
        "audit_count",
    }
    if not isinstance(prepare_protocol, dict) or set(prepare_protocol) != prepare_protocol_keys:
        raise RuntimeError("fresh truth prepare protocol violates its exact schema")
    audit_seed = prepare_protocol.get("audit_seed")
    if (
        not isinstance(audit_seed, str)
        or not audit_seed
        or audit_seed != expected_audit_seed
        or prepare_protocol.get("audit_count") != AUDIT_COUNT
        or not _is_sha256(prepare_protocol.get("pixel_prompt_sha256"))
        or not _is_sha256(prepare_protocol.get("pixel_output_schema_sha256"))
        or _sha256((audit_seed + "\n").encode()) != prepare_protocol.get("audit_seed_sha256")
        or any(
            not isinstance(prepare_protocol.get(key), str) or not prepare_protocol[key]
            for key in (
                "pixel_judge_model",
                "conclusion_judge_model",
                "audit_judge_model",
                "teacher_model",
            )
        )
    ):
        raise RuntimeError("fresh truth prepare protocol violates its exact schema")
    _validate_blinded_mapping_commitments(mapping_rows, audit_seed=audit_seed)
    if protocol.get("schema") != "triage-fresh-pixel-judge-protocol-v1":
        raise RuntimeError("fresh truth pixel protocol is unsupported")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("fresh truth prepare manifest file map is malformed")
    expected_files = {
        "judge/tasks.jsonl": _sha256(task_raw),
        "judge/protocol.json": _sha256(protocol_raw),
        "private/mapping.jsonl": _sha256(mapping_raw),
    }
    pixels: dict[str, bytes] = {}
    if len(list(images.iterdir())) != FRESH_TRUTH_COUNT:
        raise RuntimeError("pixel judge image store is not exact400")
    mapping_by_id = {str(row["final_audit_id"]): row for row in mapping_rows}
    for row in task_rows:
        audit_id = str(row["final_audit_id"])
        relative = str(row["image_relpath"])
        if relative != f"images/{audit_id}.jpg":
            raise RuntimeError("pixel judge task path is not opaque and canonical")
        pixel_path = judge / relative
        pixel = _load_private_bytes(pixel_path, label="pixel judge image")
        if _sha256(pixel) != mapping_by_id[audit_id]["preview_sha256"]:
            raise RuntimeError("pixel judge image digest differs from its private mapping")
        pixels[audit_id] = pixel
        expected_files[f"judge/{relative}"] = _sha256(pixel)
    if files != dict(sorted(expected_files.items())):
        raise RuntimeError("fresh truth prepare file digests do not reproduce")
    # Re-read the sealed manifest last so a source swap cannot straddle the snapshot.
    if (
        _load_private_bytes(private / "prepare-manifest.json", label="fresh truth prepare manifest")
        != manifest_raw
    ):
        raise RuntimeError("fresh truth prepare manifest changed during snapshot")
    return _PreparedTruthSnapshot(root, manifest, tuple(mapping_rows), pixels)


def _load_conclusion_work(
    conclusion_work_dir: Path, *, expected_prepare_sha256: str
) -> _ConclusionWorkSnapshot:
    root = _require_private_directory(conclusion_work_dir, label="fresh conclusion work")
    if {path.name for path in root.iterdir()} != {"audit", "conclusion", "private"}:
        raise RuntimeError("fresh conclusion work has missing or extra roots")
    conclusion = _require_private_directory(root / "conclusion", label="conclusion bundle")
    audit = _require_private_directory(root / "audit", label="audit bundle")
    audit_images = _require_private_directory(audit / "images", label="audit images")
    private = _require_private_directory(root / "private", label="conclusion private evidence")
    if {path.name for path in conclusion.iterdir()} != {"protocol.json", "tasks.jsonl"}:
        raise RuntimeError("conclusion bundle has missing or extra files")
    if {path.name for path in audit.iterdir()} != {"images", "protocol.json", "tasks.jsonl"}:
        raise RuntimeError("audit bundle has missing or extra files")
    if {path.name for path in private.iterdir()} != {"conclusion-manifest.json"}:
        raise RuntimeError("conclusion private evidence has missing or extra files")
    manifest, manifest_raw = _load_canonical_json(
        private / "conclusion-manifest.json", label="conclusion work manifest"
    )
    if set(manifest) != {
        "schema",
        "status",
        "prepare_sha256",
        "counts",
        "input_outputs",
        "protocol",
        "files",
        "conclusion_work_sha256",
    } or (manifest.get("schema"), manifest.get("status")) != (
        "triage-fresh-conclusion-work-v1",
        "blinded-tasks-frozen",
    ):
        raise RuntimeError("conclusion work manifest violates its exact schema")
    if manifest.get("prepare_sha256") != expected_prepare_sha256:
        raise RuntimeError("conclusion work belongs to a different fresh truth preparation")
    _verify_self_digest(manifest, field="conclusion_work_sha256", label="conclusion work manifest")
    conclusion_rows, conclusion_raw = _load_canonical_jsonl(
        conclusion / "tasks.jsonl", label="blinded conclusion tasks"
    )
    audit_rows, audit_raw = _load_canonical_jsonl(audit / "tasks.jsonl", label="audit tasks")
    conclusion_protocol, conclusion_protocol_raw = _load_canonical_json(
        conclusion / "protocol.json", label="conclusion protocol"
    )
    audit_protocol, audit_protocol_raw = _load_canonical_json(
        audit / "protocol.json", label="audit protocol"
    )
    if (
        len(conclusion_rows) != 800
        or len(audit_rows) != AUDIT_COUNT
        or manifest.get("counts") != {"audit_tasks": 12, "conclusion_tasks": 800}
        or any(set(row) != {"rid", "text"} for row in conclusion_rows)
        or any(set(row) != {"final_audit_id", "image_relpath"} for row in audit_rows)
        or len({str(row["rid"]) for row in conclusion_rows}) != 800
        or len({str(row["final_audit_id"]) for row in audit_rows}) != AUDIT_COUNT
    ):
        raise RuntimeError("conclusion work task rows violate their exact schemas")
    if conclusion_protocol.get("schema") != "triage-fresh-conclusion-protocol-v1" or (
        audit_protocol.get("schema") != "triage-fresh-audit-protocol-v1"
    ):
        raise RuntimeError("conclusion work uses an unsupported judge protocol")
    expected_files = {
        "conclusion/tasks.jsonl": _sha256(conclusion_raw),
        "conclusion/protocol.json": _sha256(conclusion_protocol_raw),
        "audit/tasks.jsonl": _sha256(audit_raw),
        "audit/protocol.json": _sha256(audit_protocol_raw),
    }
    if len(list(audit_images.iterdir())) != AUDIT_COUNT:
        raise RuntimeError("audit image store has the wrong cardinality")
    for row in audit_rows:
        audit_id = str(row["final_audit_id"])
        relative = str(row["image_relpath"])
        if relative != f"images/{audit_id}.jpg":
            raise RuntimeError("audit task image path is not opaque and canonical")
        pixel = _load_private_bytes(audit / relative, label="audit image")
        expected_files[f"audit/{relative}"] = _sha256(pixel)
    if manifest.get("files") != dict(sorted(expected_files.items())):
        raise RuntimeError("conclusion work file digests do not reproduce")
    if (
        _load_private_bytes(private / "conclusion-manifest.json", label="conclusion work manifest")
        != manifest_raw
    ):
        raise RuntimeError("conclusion work manifest changed during snapshot")
    return _ConclusionWorkSnapshot(root, manifest, tuple(conclusion_rows), tuple(audit_rows))


def prepare_fresh_truth_tasks(
    *,
    sources: Sequence[FreshTruthSource],
    binding: FreshTruthBinding,
    output_dir: Path,
    pixel_judge_model: str = "claude-opus-5",
    conclusion_judge_model: str = "claude-opus-5",
    audit_judge_model: str = "claude-fable-5",
    teacher_model: str = TEACHER_MODEL,
) -> dict[str, object]:
    """Atomically publish opaque pixel tasks and a separate private identity map."""
    binding.validate()
    if any(
        not value
        for value in (
            pixel_judge_model,
            conclusion_judge_model,
            audit_judge_model,
            teacher_model,
        )
    ):
        raise ValueError("fresh truth protocol identities must be nonempty")
    audit_seed = fresh_truth_audit_seed(binding)
    ordered = tuple(sorted(sources, key=lambda row: row.final_audit_id))
    if len(ordered) != FRESH_TRUTH_COUNT:
        raise ValueError("fresh truth preparation requires exactly 400 approved pixels")
    if (
        len({row.asset_id for row in ordered}) != FRESH_TRUTH_COUNT
        or len({row.final_audit_id for row in ordered}) != FRESH_TRUTH_COUNT
    ):
        raise ValueError("fresh truth source identities must be unique")
    for row in ordered:
        if (
            not row.asset_id
            or not row.final_audit_id
            or not _is_sha256(row.preview_sha256)
            or _sha256(row.pixel_bytes) != row.preview_sha256
        ):
            raise ValueError("fresh truth source pixel lineage is invalid")

    audit_ids = {
        row.final_audit_id
        for row in sorted(
            ordered,
            key=lambda item: _stable_digest(audit_seed, "audit", item.final_audit_id),
        )[:AUDIT_COUNT]
    }
    task_rows = [
        {
            "final_audit_id": row.final_audit_id,
            "image_relpath": f"images/{row.final_audit_id}.jpg",
        }
        for row in ordered
    ]
    mapping_rows = [
        {
            "asset_id": row.asset_id,
            "audit_selected": row.final_audit_id in audit_ids,
            "final_audit_id": row.final_audit_id,
            "preview_sha256": row.preview_sha256,
            "teacher_rid": _stable_digest(audit_seed, "teacher-rid", row.final_audit_id),
            "truth_rid": _stable_digest(audit_seed, "truth-rid", row.final_audit_id),
        }
        for row in ordered
    ]
    judge_protocol = {
        "schema": "triage-fresh-pixel-judge-protocol-v1",
        "model": pixel_judge_model,
        "prompt": PIXEL_JUDGE_PROMPT,
        "prompt_sha256": _sha256((PIXEL_JUDGE_PROMPT + "\n").encode()),
        "output_schema": PIXEL_CARD_SCHEMA,
        "output_schema_sha256": _sha256(_canonical_bytes(PIXEL_CARD_SCHEMA)),
    }
    task_bytes = _jsonl_bytes(task_rows)
    mapping_bytes = _jsonl_bytes(mapping_rows)
    protocol_bytes = _canonical_bytes(judge_protocol)

    destination = Path(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.staging-", dir=destination.parent
    ) as temporary_name:
        staging = Path(temporary_name)
        staging.chmod(0o700)
        judge_dir = staging / "judge"
        private_dir = staging / "private"
        images_dir = judge_dir / "images"
        _private_mkdir(judge_dir)
        _private_mkdir(images_dir)
        _private_mkdir(private_dir)
        _write_new_private(judge_dir / "tasks.jsonl", task_bytes)
        _write_new_private(judge_dir / "protocol.json", protocol_bytes)
        _write_new_private(private_dir / "mapping.jsonl", mapping_bytes)
        file_digests: dict[str, str] = {
            "judge/tasks.jsonl": _sha256(task_bytes),
            "judge/protocol.json": _sha256(protocol_bytes),
            "private/mapping.jsonl": _sha256(mapping_bytes),
        }
        for row in ordered:
            relative = f"judge/images/{row.final_audit_id}.jpg"
            _write_new_private(staging / relative, row.pixel_bytes)
            file_digests[relative] = row.preview_sha256
        manifest: dict[str, object] = {
            "schema": "triage-fresh-truth-prepare-v1",
            "status": "judge-tasks-frozen",
            "binding": binding.as_manifest(),
            "counts": {"judge_tasks": FRESH_TRUTH_COUNT, "pixels": FRESH_TRUTH_COUNT},
            "protocol": {
                "pixel_judge_model": pixel_judge_model,
                "conclusion_judge_model": conclusion_judge_model,
                "audit_judge_model": audit_judge_model,
                "teacher_model": teacher_model,
                "pixel_prompt_sha256": judge_protocol["prompt_sha256"],
                "pixel_output_schema_sha256": judge_protocol["output_schema_sha256"],
                "audit_seed": audit_seed,
                "audit_seed_sha256": _sha256((audit_seed + "\n").encode()),
                "audit_count": AUDIT_COUNT,
            },
            "files": dict(sorted(file_digests.items())),
        }
        manifest = _self_seal(manifest, "prepare_sha256")
        _write_new_private(private_dir / "prepare-manifest.json", _canonical_bytes(manifest))
        _fsync_directory(images_dir)
        _fsync_directory(judge_dir)
        _fsync_directory(private_dir)
        _fsync_directory(staging)
        _load_prepared_truth(staging)
        _publish_staged_directory(staging, destination)
    return dict(_load_prepared_truth(destination).manifest)


def _load_card_output(
    path: Path,
    *,
    label: str,
    expected_ids: set[str],
) -> tuple[dict[str, Mapping[str, object]], bytes]:
    rows, raw = _load_canonical_jsonl(path, label=label)
    row_keys = {"fields", "final_audit_id"}
    field_keys = {"description", "schema_version", "setting"}
    if len(rows) != FRESH_TRUTH_COUNT or any(set(row) != row_keys for row in rows):
        raise RuntimeError(f"{label} must contain exactly 400 exact-schema rows")
    by_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        audit_id = str(row["final_audit_id"])
        fields = row["fields"]
        if not isinstance(fields, dict) or set(fields) != field_keys:
            raise RuntimeError(f"{label} card fields violate their exact schema")
        description = fields["description"]
        setting = fields["setting"]
        if (
            fields["schema_version"] != "asset-description-v1"
            or not isinstance(description, str)
            or not description.strip()
            or not isinstance(setting, str)
            or not setting.strip()
            or any(character in description + setting for character in "\r\n")
            or audit_id in by_id
        ):
            raise RuntimeError(f"{label} contains an invalid or duplicate card")
        by_id[audit_id] = fields
    if set(by_id) != expected_ids:
        raise RuntimeError(f"{label} does not cover the exact prepared IDs")
    return by_id, raw


def prepare_blinded_conclusion_tasks(
    *,
    prepared_dir: Path,
    pixel_cards_path: Path,
    teacher_cards_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    """Blind corrected and teacher cards under opaque RIDs, plus freeze the audit sample."""
    prepared = _load_prepared_truth(prepared_dir)
    mapping_by_id = {str(row["final_audit_id"]): row for row in prepared.mapping_rows}
    expected_ids = set(mapping_by_id)
    pixel_cards, pixel_raw = _load_card_output(
        pixel_cards_path, label="Opus pixel-card output", expected_ids=expected_ids
    )
    teacher_cards, teacher_raw = _load_card_output(
        teacher_cards_path, label="teacher card output", expected_ids=expected_ids
    )
    conclusion_rows: list[dict[str, object]] = []
    for audit_id, mapping in mapping_by_id.items():
        for rid_key, cards in (
            ("truth_rid", pixel_cards),
            ("teacher_rid", teacher_cards),
        ):
            fields = cards[audit_id]
            conclusion_rows.append(
                {
                    "rid": mapping[rid_key],
                    "text": (f"description: {fields['description']}\nsetting: {fields['setting']}"),
                }
            )
    prepare_sha256 = str(prepared.manifest["prepare_sha256"])
    conclusion_rows.sort(
        key=lambda row: _stable_digest(prepare_sha256, "conclusion-order", str(row["rid"]))
    )
    audit_ids = sorted(
        audit_id for audit_id, row in mapping_by_id.items() if row["audit_selected"] is True
    )
    audit_rows = [
        {"final_audit_id": audit_id, "image_relpath": f"images/{audit_id}.jpg"}
        for audit_id in audit_ids
    ]
    conclusion_protocol = {
        "schema": "triage-fresh-conclusion-protocol-v1",
        "model": prepared.manifest["protocol"]["conclusion_judge_model"],  # type: ignore[index]
        "prompt": CONCLUSION_PROMPT,
        "prompt_sha256": _sha256((CONCLUSION_PROMPT + "\n").encode()),
        "output_schema": LOCATION_OUTPUT_SCHEMA,
        "output_schema_sha256": _sha256(_canonical_bytes(LOCATION_OUTPUT_SCHEMA)),
    }
    audit_protocol = {
        "schema": "triage-fresh-audit-protocol-v1",
        "model": prepared.manifest["protocol"]["audit_judge_model"],  # type: ignore[index]
        "prompt": AUDIT_PROMPT,
        "prompt_sha256": _sha256((AUDIT_PROMPT + "\n").encode()),
        "output_schema": AUDIT_OUTPUT_SCHEMA,
        "output_schema_sha256": _sha256(_canonical_bytes(AUDIT_OUTPUT_SCHEMA)),
    }
    conclusion_bytes = _jsonl_bytes(conclusion_rows)
    audit_bytes = _jsonl_bytes(audit_rows)
    conclusion_protocol_bytes = _canonical_bytes(conclusion_protocol)
    audit_protocol_bytes = _canonical_bytes(audit_protocol)

    destination = Path(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.staging-", dir=destination.parent
    ) as temporary_name:
        staging = Path(temporary_name)
        staging.chmod(0o700)
        conclusion_dir = staging / "conclusion"
        audit_dir = staging / "audit"
        audit_images = audit_dir / "images"
        private_dir = staging / "private"
        _private_mkdir(conclusion_dir)
        _private_mkdir(audit_dir)
        _private_mkdir(audit_images)
        _private_mkdir(private_dir)
        _write_new_private(conclusion_dir / "tasks.jsonl", conclusion_bytes)
        _write_new_private(conclusion_dir / "protocol.json", conclusion_protocol_bytes)
        _write_new_private(audit_dir / "tasks.jsonl", audit_bytes)
        _write_new_private(audit_dir / "protocol.json", audit_protocol_bytes)
        file_digests = {
            "conclusion/tasks.jsonl": _sha256(conclusion_bytes),
            "conclusion/protocol.json": _sha256(conclusion_protocol_bytes),
            "audit/tasks.jsonl": _sha256(audit_bytes),
            "audit/protocol.json": _sha256(audit_protocol_bytes),
        }
        for audit_id in audit_ids:
            relative = f"audit/images/{audit_id}.jpg"
            pixel = prepared.pixels[audit_id]
            _write_new_private(staging / relative, pixel)
            file_digests[relative] = _sha256(pixel)
        manifest: dict[str, object] = {
            "schema": "triage-fresh-conclusion-work-v1",
            "status": "blinded-tasks-frozen",
            "prepare_sha256": prepare_sha256,
            "counts": {"audit_tasks": AUDIT_COUNT, "conclusion_tasks": 800},
            "input_outputs": {
                "pixel_cards_sha256": _sha256(pixel_raw),
                "teacher_cards_sha256": _sha256(teacher_raw),
            },
            "protocol": {
                "conclusion_model": conclusion_protocol["model"],
                "conclusion_prompt_sha256": conclusion_protocol["prompt_sha256"],
                "conclusion_output_schema_sha256": conclusion_protocol["output_schema_sha256"],
                "audit_model": audit_protocol["model"],
                "audit_prompt_sha256": audit_protocol["prompt_sha256"],
                "audit_output_schema_sha256": audit_protocol["output_schema_sha256"],
            },
            "files": dict(sorted(file_digests.items())),
        }
        manifest = _self_seal(manifest, "conclusion_work_sha256")
        _write_new_private(private_dir / "conclusion-manifest.json", _canonical_bytes(manifest))
        _fsync_directory(conclusion_dir)
        _fsync_directory(audit_images)
        _fsync_directory(audit_dir)
        _fsync_directory(private_dir)
        _fsync_directory(staging)
        _load_conclusion_work(
            staging, expected_prepare_sha256=str(prepared.manifest["prepare_sha256"])
        )
        _publish_staged_directory(staging, destination)
    return dict(
        _load_conclusion_work(
            destination,
            expected_prepare_sha256=str(prepared.manifest["prepare_sha256"]),
        ).manifest
    )


def _load_location_rows(
    path: Path,
    *,
    label: str,
    identity_field: str,
    expected_ids: set[str],
) -> tuple[dict[str, str], bytes]:
    rows, raw = _load_canonical_jsonl(path, label=label)
    expected_keys = {identity_field, "location"}
    if len(rows) != len(expected_ids) or any(set(row) != expected_keys for row in rows):
        raise RuntimeError(f"{label} violates its exact schema or cardinality")
    resolved: dict[str, str] = {}
    for row in rows:
        identity = str(row[identity_field])
        location = row["location"]
        if identity in resolved or location not in LOCATION_CLASSES:
            raise RuntimeError(f"{label} has a duplicate identity or invalid location")
        resolved[identity] = str(location)
    if set(resolved) != expected_ids:
        raise RuntimeError(f"{label} does not cover the exact frozen task set")
    return resolved, raw


def _load_adjudications(path: Path, *, expected_ids: set[str]) -> tuple[dict[str, str], bytes]:
    rows, raw = _load_canonical_jsonl(path, label="owner adjudications")
    resolved: dict[str, str] = {}
    for row in rows:
        if set(row) != {"final_audit_id", "location", "reason"}:
            raise RuntimeError("owner adjudication violates its exact schema")
        audit_id = str(row["final_audit_id"])
        location = row["location"]
        reason = row["reason"]
        if (
            audit_id in resolved
            or location not in LOCATION_CLASSES
            or not isinstance(reason, str)
            or not reason.strip()
            or "\n" in reason
            or "\r" in reason
        ):
            raise RuntimeError("owner adjudication is duplicate or malformed")
        resolved[audit_id] = str(location)
    if set(resolved) != expected_ids:
        raise RuntimeError("owner adjudications must exactly cover every audit disagreement")
    return resolved, raw


def finalize_fresh_truth_package(
    *,
    prepared_dir: Path,
    conclusion_work_dir: Path,
    pixel_cards_path: Path,
    teacher_cards_path: Path,
    conclusions_path: Path,
    audit_path: Path,
    adjudications_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    """Validate external judge outputs and atomically seal the scoring package."""
    prepared = _load_prepared_truth(prepared_dir)
    prepare_sha256 = str(prepared.manifest["prepare_sha256"])
    work = _load_conclusion_work(conclusion_work_dir, expected_prepare_sha256=prepare_sha256)
    mapping_by_id = {str(row["final_audit_id"]): row for row in prepared.mapping_rows}
    expected_ids = set(mapping_by_id)
    pixel_cards, pixel_raw = _load_card_output(
        pixel_cards_path, label="Opus pixel-card output", expected_ids=expected_ids
    )
    teacher_cards, teacher_raw = _load_card_output(
        teacher_cards_path, label="teacher card output", expected_ids=expected_ids
    )
    expected_inputs = {
        "pixel_cards_sha256": _sha256(pixel_raw),
        "teacher_cards_sha256": _sha256(teacher_raw),
    }
    if work.manifest.get("input_outputs") != expected_inputs:
        raise RuntimeError("card outputs changed after blinded conclusion tasks were frozen")
    expected_rids = {
        str(row[key]) for row in prepared.mapping_rows for key in ("truth_rid", "teacher_rid")
    }
    conclusions, conclusions_raw = _load_location_rows(
        conclusions_path,
        label="Opus blinded conclusions",
        identity_field="rid",
        expected_ids=expected_rids,
    )
    frozen_task_rids = {str(row["rid"]) for row in work.conclusion_rows}
    if set(conclusions) != frozen_task_rids:
        raise RuntimeError("Opus conclusions differ from the frozen blinded task set")
    audit_ids = {str(row["final_audit_id"]) for row in work.audit_rows}
    audit, audit_raw = _load_location_rows(
        audit_path,
        label="Fable audit output",
        identity_field="final_audit_id",
        expected_ids=audit_ids,
    )

    truth_by_audit = {
        audit_id: conclusions[str(mapping["truth_rid"])]
        for audit_id, mapping in mapping_by_id.items()
    }
    disagreements = {
        audit_id for audit_id in audit_ids if audit[audit_id] != truth_by_audit[audit_id]
    }
    adjudications, adjudications_raw = _load_adjudications(
        adjudications_path, expected_ids=disagreements
    )
    truth_by_audit.update(adjudications)

    ordered_mapping = tuple(
        sorted(prepared.mapping_rows, key=lambda row: str(row["final_audit_id"]))
    )
    library_rows = [
        {
            "fields": dict(pixel_cards[str(row["final_audit_id"])]),
            "image_id": str(row["asset_id"]),
        }
        for row in ordered_mapping
    ]
    battery_key: dict[str, object] = {}
    shard_rows: list[dict[str, object]] = []
    truth_by_asset: dict[str, str] = {}
    teacher_by_asset: dict[str, str] = {}
    for row in ordered_mapping:
        asset_id = str(row["asset_id"])
        audit_id = str(row["final_audit_id"])
        truth_rid = str(row["truth_rid"])
        teacher_rid = str(row["teacher_rid"])
        truth_location = truth_by_audit[audit_id]
        teacher_location = conclusions[teacher_rid]
        truth_by_asset[asset_id] = truth_location
        teacher_by_asset[asset_id] = teacher_location
        battery_key[truth_rid] = {"asset": asset_id, "source": "truth"}
        battery_key[teacher_rid] = {"asset": asset_id, "source": "teacher"}
        shard_rows.extend(
            (
                {"location": truth_location, "rid": truth_rid},
                {"location": teacher_location, "rid": teacher_rid},
            )
        )
    shard_rows.sort(key=lambda row: str(row["rid"]))
    library_bytes = _jsonl_bytes(library_rows)
    battery_key_bytes = _canonical_bytes(dict(sorted(battery_key.items())))
    shard_bytes = _jsonl_bytes(shard_rows)
    prepare_manifest_raw = _load_private_bytes(
        prepared.root / "private" / "prepare-manifest.json",
        label="fresh truth prepare manifest",
    )
    conclusion_manifest_raw = _load_private_bytes(
        work.root / "private" / "conclusion-manifest.json",
        label="conclusion work manifest",
    )
    evidence_payloads = {
        "evidence/adjudications.jsonl": adjudications_raw,
        "evidence/audit.jsonl": audit_raw,
        "evidence/audit-protocol.json": _load_private_bytes(
            work.root / "audit" / "protocol.json", label="audit protocol"
        ),
        "evidence/audit-tasks.jsonl": _load_private_bytes(
            work.root / "audit" / "tasks.jsonl", label="audit tasks"
        ),
        "evidence/conclusion-manifest.json": conclusion_manifest_raw,
        "evidence/conclusion-protocol.json": _load_private_bytes(
            work.root / "conclusion" / "protocol.json", label="conclusion protocol"
        ),
        "evidence/conclusion-tasks.jsonl": _load_private_bytes(
            work.root / "conclusion" / "tasks.jsonl", label="blinded conclusion tasks"
        ),
        "evidence/conclusions.jsonl": conclusions_raw,
        "evidence/mapping.jsonl": _load_private_bytes(
            prepared.root / "private" / "mapping.jsonl", label="fresh truth private mapping"
        ),
        "evidence/pixel-cards.jsonl": pixel_raw,
        "evidence/pixel-protocol.json": _load_private_bytes(
            prepared.root / "judge" / "protocol.json", label="pixel judge protocol"
        ),
        "evidence/pixel-tasks.jsonl": _load_private_bytes(
            prepared.root / "judge" / "tasks.jsonl", label="pixel judge tasks"
        ),
        "evidence/prepare-manifest.json": prepare_manifest_raw,
        "evidence/teacher-cards.jsonl": teacher_raw,
    }
    package_payloads = {
        "library_truth.jsonl": library_bytes,
        "battery_key.json": battery_key_bytes,
        "battery_shards/bshard_000.jsonl": shard_bytes,
        **evidence_payloads,
    }
    prepare_protocol = prepared.manifest["protocol"]
    work_protocol = work.manifest["protocol"]
    manifest: dict[str, object] = {
        "schema": "triage-fresh-truth-package-v1",
        "status": "sealed-one-shot-certification-truth",
        "binding": prepared.manifest["binding"],
        "protocol": {
            **dict(prepare_protocol),  # type: ignore[arg-type]
            **dict(work_protocol),  # type: ignore[arg-type]
            "pixel_tasks_sha256": prepared.manifest["files"]["judge/tasks.jsonl"],  # type: ignore[index]
            "conclusion_tasks_sha256": work.manifest["files"]["conclusion/tasks.jsonl"],  # type: ignore[index]
            "audit_tasks_sha256": work.manifest["files"]["audit/tasks.jsonl"],  # type: ignore[index]
            "pixel_cards_output_sha256": _sha256(pixel_raw),
            "teacher_cards_output_sha256": _sha256(teacher_raw),
            "conclusions_output_sha256": _sha256(conclusions_raw),
            "audit_output_sha256": _sha256(audit_raw),
            "adjudications_output_sha256": _sha256(adjudications_raw),
        },
        "inputs": {
            "prepare_sha256": prepare_sha256,
            "conclusion_work_sha256": work.manifest["conclusion_work_sha256"],
        },
        "counts": {
            "adjudications": len(adjudications),
            "audit_rows": len(audit),
            "library_truth": len(library_rows),
            "teacher_labels": len(teacher_by_asset),
            "truth_labels": len(truth_by_asset),
        },
        "audit": {
            "precommitted_count": AUDIT_COUNT,
            "disagreement_count": len(disagreements),
            "adjudicated_count": len(adjudications),
            "passed": len(disagreements) == len(adjudications),
        },
        "files": {name: _sha256(payload) for name, payload in sorted(package_payloads.items())},
    }
    manifest = _self_seal(manifest, "truth_manifest_sha256")

    destination = Path(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.staging-", dir=destination.parent
    ) as temporary_name:
        staging = Path(temporary_name)
        staging.chmod(0o700)
        _private_mkdir(staging / "battery_shards")
        _private_mkdir(staging / "evidence")
        for relative, payload in package_payloads.items():
            _write_new_private(staging / relative, payload)
        _write_new_private(staging / "truth-manifest.json", _canonical_bytes(manifest))
        _fsync_directory(staging / "battery_shards")
        _fsync_directory(staging / "evidence")
        _fsync_directory(staging)
        load_fresh_truth_package(staging)
        _publish_staged_directory(staging, destination)
    return dict(load_fresh_truth_package(destination).manifest)


def fresh_truth_inputs_sha256(truth_manifest_bytes: bytes) -> str:
    """Identify an exact sealed input set without opening any scoring payload."""
    digest = hashlib.sha256(b"triage-fresh-certification-inputs-v1\0")
    digest.update(len(truth_manifest_bytes).to_bytes(8, "big"))
    digest.update(truth_manifest_bytes)
    return digest.hexdigest()


def _load_fresh_truth_manifest(
    root: Path,
    *,
    expected_binding: FreshTruthBinding | None,
) -> tuple[dict[str, object], bytes]:
    manifest, manifest_raw = _load_canonical_json(
        root / "truth-manifest.json", label="fresh truth manifest"
    )
    if set(manifest) != {
        "schema",
        "status",
        "binding",
        "protocol",
        "inputs",
        "counts",
        "audit",
        "files",
        "truth_manifest_sha256",
    } or (manifest.get("schema"), manifest.get("status")) != (
        "triage-fresh-truth-package-v1",
        "sealed-one-shot-certification-truth",
    ):
        raise RuntimeError("fresh truth manifest violates its exact schema")
    _verify_self_digest(manifest, field="truth_manifest_sha256", label="fresh truth manifest")
    _validate_sealed_manifest_contract(manifest)
    if expected_binding is not None and manifest.get("binding") != expected_binding.as_manifest():
        raise RuntimeError("fresh truth package belongs to different frozen training or approval")
    return manifest, manifest_raw


def load_fresh_truth_commitment(
    truth_dir: Path,
    *,
    expected_binding: FreshTruthBinding,
) -> FreshTruthCommitment:
    """Authenticate only the non-scoring manifest before consuming the one shot."""
    root = _require_private_directory(truth_dir, label="fresh truth package")
    if {path.name for path in root.iterdir()} != {
        "battery_key.json",
        "battery_shards",
        "evidence",
        "library_truth.jsonl",
        "truth-manifest.json",
    }:
        raise RuntimeError("fresh truth package has missing or extra roots")
    _require_private_directory(root / "battery_shards", label="battery shards")
    _require_private_directory(root / "evidence", label="truth evidence")
    manifest, manifest_raw = _load_fresh_truth_manifest(root, expected_binding=expected_binding)
    return FreshTruthCommitment(
        truth_manifest_sha256=str(manifest["truth_manifest_sha256"]),
        certification_inputs_sha256=fresh_truth_inputs_sha256(manifest_raw),
    )


def load_fresh_truth_package(
    truth_dir: Path,
    *,
    expected_binding: FreshTruthBinding | None = None,
    expected_truth_manifest_sha256: str | None = None,
) -> FreshTruthPackageSnapshot:
    """Authenticate one exact400 canonical truth package without mutable path lookups."""
    root = _require_private_directory(truth_dir, label="fresh truth package")
    if {path.name for path in root.iterdir()} != {
        "battery_key.json",
        "battery_shards",
        "evidence",
        "library_truth.jsonl",
        "truth-manifest.json",
    }:
        raise RuntimeError("fresh truth package has missing or extra roots")
    shards = _require_private_directory(root / "battery_shards", label="battery shards")
    evidence = _require_private_directory(root / "evidence", label="truth evidence")
    if {path.name for path in shards.iterdir()} != {"bshard_000.jsonl"}:
        raise RuntimeError("fresh truth package must contain its one canonical shard")
    expected_evidence_names = {
        "adjudications.jsonl",
        "audit.jsonl",
        "audit-protocol.json",
        "audit-tasks.jsonl",
        "conclusion-manifest.json",
        "conclusion-protocol.json",
        "conclusion-tasks.jsonl",
        "conclusions.jsonl",
        "mapping.jsonl",
        "pixel-cards.jsonl",
        "pixel-protocol.json",
        "pixel-tasks.jsonl",
        "prepare-manifest.json",
        "teacher-cards.jsonl",
    }
    if {path.name for path in evidence.iterdir()} != expected_evidence_names:
        raise RuntimeError("fresh truth package evidence is incomplete or has extras")
    manifest, manifest_raw = _load_fresh_truth_manifest(root, expected_binding=expected_binding)
    if (
        expected_truth_manifest_sha256 is not None
        and manifest["truth_manifest_sha256"] != expected_truth_manifest_sha256
    ):
        raise RuntimeError("fresh truth package differs from the pre-reveal commitment")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("fresh truth package file map is malformed")
    actual_paths = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != "truth-manifest.json"
    }
    if set(files) != actual_paths:
        raise RuntimeError("fresh truth package file map has missing or extra entries")
    snapshots: dict[str, bytes] = {}
    for relative in sorted(actual_paths):
        payload = _load_private_bytes(root / relative, label=f"fresh truth {relative}")
        if not _is_sha256(files[relative]) or _sha256(payload) != files[relative]:
            raise RuntimeError(f"fresh truth {relative} digest does not reproduce")
        snapshots[relative] = payload

    prepare_manifest, _ = _load_canonical_json(
        evidence / "prepare-manifest.json", label="sealed prepare manifest evidence"
    )
    conclusion_manifest, _ = _load_canonical_json(
        evidence / "conclusion-manifest.json", label="sealed conclusion manifest evidence"
    )
    if (
        set(prepare_manifest)
        != {
            "schema",
            "status",
            "binding",
            "counts",
            "protocol",
            "files",
            "prepare_sha256",
        }
        or set(conclusion_manifest)
        != {
            "schema",
            "status",
            "prepare_sha256",
            "counts",
            "input_outputs",
            "protocol",
            "files",
            "conclusion_work_sha256",
        }
        or (
            prepare_manifest.get("schema"),
            prepare_manifest.get("status"),
            prepare_manifest.get("counts"),
        )
        != (
            "triage-fresh-truth-prepare-v1",
            "judge-tasks-frozen",
            {"judge_tasks": FRESH_TRUTH_COUNT, "pixels": FRESH_TRUTH_COUNT},
        )
        or (
            conclusion_manifest.get("schema"),
            conclusion_manifest.get("status"),
            conclusion_manifest.get("counts"),
        )
        != (
            "triage-fresh-conclusion-work-v1",
            "blinded-tasks-frozen",
            {"audit_tasks": AUDIT_COUNT, "conclusion_tasks": 2 * FRESH_TRUTH_COUNT},
        )
    ):
        raise RuntimeError("fresh truth copied workflow manifests violate their exact schemas")
    _verify_self_digest(
        prepare_manifest, field="prepare_sha256", label="sealed prepare manifest evidence"
    )
    _verify_self_digest(
        conclusion_manifest,
        field="conclusion_work_sha256",
        label="sealed conclusion manifest evidence",
    )
    protocol = manifest["protocol"]
    prepare_protocol = prepare_manifest.get("protocol")
    conclusion_protocol = conclusion_manifest.get("protocol")
    prepare_files = prepare_manifest.get("files")
    conclusion_files = conclusion_manifest.get("files")
    if not all(
        isinstance(value, dict)
        for value in (
            protocol,
            prepare_protocol,
            conclusion_protocol,
            prepare_files,
            conclusion_files,
        )
    ):
        raise RuntimeError("fresh truth copied workflow lineage is malformed")
    expected_protocol_lineage = {
        "pixel_judge_model": prepare_protocol.get("pixel_judge_model"),
        "conclusion_judge_model": prepare_protocol.get("conclusion_judge_model"),
        "audit_judge_model": prepare_protocol.get("audit_judge_model"),
        "teacher_model": prepare_protocol.get("teacher_model"),
        "pixel_prompt_sha256": prepare_protocol.get("pixel_prompt_sha256"),
        "pixel_output_schema_sha256": prepare_protocol.get("pixel_output_schema_sha256"),
        "audit_seed": prepare_protocol.get("audit_seed"),
        "audit_seed_sha256": prepare_protocol.get("audit_seed_sha256"),
        "audit_count": prepare_protocol.get("audit_count"),
        "conclusion_model": conclusion_protocol.get("conclusion_model"),
        "conclusion_prompt_sha256": conclusion_protocol.get("conclusion_prompt_sha256"),
        "conclusion_output_schema_sha256": conclusion_protocol.get(
            "conclusion_output_schema_sha256"
        ),
        "audit_model": conclusion_protocol.get("audit_model"),
        "audit_prompt_sha256": conclusion_protocol.get("audit_prompt_sha256"),
        "audit_output_schema_sha256": conclusion_protocol.get("audit_output_schema_sha256"),
        "pixel_tasks_sha256": prepare_files.get("judge/tasks.jsonl"),
        "conclusion_tasks_sha256": conclusion_files.get("conclusion/tasks.jsonl"),
        "audit_tasks_sha256": conclusion_files.get("audit/tasks.jsonl"),
        "pixel_cards_output_sha256": _sha256(snapshots["evidence/pixel-cards.jsonl"]),
        "teacher_cards_output_sha256": _sha256(snapshots["evidence/teacher-cards.jsonl"]),
        "conclusions_output_sha256": _sha256(snapshots["evidence/conclusions.jsonl"]),
        "audit_output_sha256": _sha256(snapshots["evidence/audit.jsonl"]),
        "adjudications_output_sha256": _sha256(snapshots["evidence/adjudications.jsonl"]),
    }
    if protocol != expected_protocol_lineage:
        raise RuntimeError("fresh truth judge task/output digest bindings do not reproduce")
    copied_workflow_files = {
        "evidence/pixel-tasks.jsonl": prepare_files.get("judge/tasks.jsonl"),
        "evidence/pixel-protocol.json": prepare_files.get("judge/protocol.json"),
        "evidence/mapping.jsonl": prepare_files.get("private/mapping.jsonl"),
        "evidence/conclusion-tasks.jsonl": conclusion_files.get("conclusion/tasks.jsonl"),
        "evidence/conclusion-protocol.json": conclusion_files.get("conclusion/protocol.json"),
        "evidence/audit-tasks.jsonl": conclusion_files.get("audit/tasks.jsonl"),
        "evidence/audit-protocol.json": conclusion_files.get("audit/protocol.json"),
    }
    if any(
        not _is_sha256(expected) or _sha256(snapshots[relative]) != expected
        for relative, expected in copied_workflow_files.items()
    ):
        raise RuntimeError("fresh truth copied task/protocol digests do not reproduce")
    if (
        manifest.get("inputs")
        != {
            "prepare_sha256": prepare_manifest["prepare_sha256"],
            "conclusion_work_sha256": conclusion_manifest["conclusion_work_sha256"],
        }
        or conclusion_manifest.get("prepare_sha256") != prepare_manifest["prepare_sha256"]
    ):
        raise RuntimeError("fresh truth workflow manifest bindings do not reproduce")
    if manifest.get("binding") != prepare_manifest.get("binding"):
        raise RuntimeError("fresh truth binding differs from its sealed preparation")
    if conclusion_manifest.get("input_outputs") != {
        "pixel_cards_sha256": _sha256(snapshots["evidence/pixel-cards.jsonl"]),
        "teacher_cards_sha256": _sha256(snapshots["evidence/teacher-cards.jsonl"]),
    }:
        raise RuntimeError("fresh truth conclusion inputs differ from their sealed card outputs")

    library_rows, library_raw = _load_canonical_jsonl(
        root / "library_truth.jsonl", label="fresh library truth"
    )
    if len(library_rows) != 400:
        raise RuntimeError("fresh library truth must contain exactly 400 rows")
    library_ids: set[str] = set()
    for row in library_rows:
        if set(row) != {"fields", "image_id"} or not isinstance(row["fields"], dict):
            raise RuntimeError("fresh library truth violates its exact schema")
        fields = row["fields"]
        if (
            set(fields) != {"description", "schema_version", "setting"}
            or fields.get("schema_version") != "asset-description-v1"
        ):
            raise RuntimeError("fresh library truth card violates its exact schema")
        asset_id = str(row["image_id"])
        if not asset_id or asset_id in library_ids:
            raise RuntimeError("fresh library truth has duplicate or empty assets")
        library_ids.add(asset_id)
    key, key_raw = _load_canonical_json(root / "battery_key.json", label="fresh battery key")
    if len(key) != 800:
        raise RuntimeError("fresh battery key must contain exactly 800 RIDs")
    source_counts = {"truth": 0, "teacher": 0}
    for rid, entry in key.items():
        if (
            not _is_sha256(rid)
            or not isinstance(entry, dict)
            or set(entry) != {"asset", "source"}
            or entry["source"] not in source_counts
            or str(entry["asset"]) not in library_ids
        ):
            raise RuntimeError("fresh battery key violates its exact RID schema")
        source_counts[str(entry["source"])] += 1
    if source_counts != {"truth": 400, "teacher": 400}:
        raise RuntimeError("fresh battery key must contain exact truth and teacher RID sets")
    shard_rows, shard_raw = _load_canonical_jsonl(
        shards / "bshard_000.jsonl", label="fresh battery shard"
    )
    if len(shard_rows) != 800 or any(set(row) != {"location", "rid"} for row in shard_rows):
        raise RuntimeError("fresh battery shard violates its exact schema")
    labels_by_rid: dict[str, str] = {}
    for row in shard_rows:
        rid = str(row["rid"])
        location = row["location"]
        if rid in labels_by_rid or rid not in key or location not in LOCATION_CLASSES:
            raise RuntimeError("fresh battery shard has an extra, duplicate, or invalid RID")
        labels_by_rid[rid] = str(location)
    if set(labels_by_rid) != set(key):
        raise RuntimeError("fresh battery shard does not cover the exact battery key")
    truth_by_asset: dict[str, str] = {}
    teacher_by_asset: dict[str, str] = {}
    for rid, entry in key.items():
        asset_id = str(entry["asset"])
        target = truth_by_asset if entry["source"] == "truth" else teacher_by_asset
        if asset_id in target:
            raise RuntimeError("fresh battery key duplicates a source for one asset")
        target[asset_id] = labels_by_rid[rid]
    if set(truth_by_asset) != library_ids or set(teacher_by_asset) != library_ids:
        raise RuntimeError("fresh truth and teacher labels do not cover the library set")

    mapping_rows, _ = _load_canonical_jsonl(
        evidence / "mapping.jsonl", label="sealed fresh truth mapping"
    )
    mapping_keys = {
        "asset_id",
        "audit_selected",
        "final_audit_id",
        "preview_sha256",
        "teacher_rid",
        "truth_rid",
    }
    if len(mapping_rows) != 400 or any(set(row) != mapping_keys for row in mapping_rows):
        raise RuntimeError("sealed fresh truth mapping violates its exact400 schema")
    mapping_by_audit: dict[str, Mapping[str, object]] = {}
    mapping_assets: set[str] = set()
    mapping_rids: set[str] = set()
    for row in mapping_rows:
        audit_id = str(row["final_audit_id"])
        asset_id = str(row["asset_id"])
        rids = (str(row["truth_rid"]), str(row["teacher_rid"]))
        if (
            not audit_id
            or not asset_id
            or audit_id in mapping_by_audit
            or asset_id in mapping_assets
            or not _is_sha256(row["preview_sha256"])
            or any(not _is_sha256(rid) or rid in mapping_rids for rid in rids)
            or type(row["audit_selected"]) is not bool
        ):
            raise RuntimeError("sealed fresh truth mapping has invalid or duplicate identities")
        mapping_by_audit[audit_id] = row
        mapping_assets.add(asset_id)
        mapping_rids.update(rids)
    if mapping_assets != library_ids or len(mapping_rids) != 800:
        raise RuntimeError("sealed fresh truth mapping does not cover the scoring identities")

    pixel_tasks, _ = _load_canonical_jsonl(
        evidence / "pixel-tasks.jsonl", label="sealed pixel tasks"
    )
    if (
        len(pixel_tasks) != 400
        or any(
            set(row) != {"final_audit_id", "image_relpath"}
            or row["image_relpath"] != f"images/{row['final_audit_id']}.jpg"
            for row in pixel_tasks
        )
        or {str(row["final_audit_id"]) for row in pixel_tasks} != set(mapping_by_audit)
    ):
        raise RuntimeError("sealed pixel tasks violate the opaque exact400 schema")
    conclusion_tasks, _ = _load_canonical_jsonl(
        evidence / "conclusion-tasks.jsonl", label="sealed blinded conclusion tasks"
    )
    if (
        len(conclusion_tasks) != 800
        or any(set(row) != {"rid", "text"} for row in conclusion_tasks)
        or {str(row["rid"]) for row in conclusion_tasks} != mapping_rids
    ):
        raise RuntimeError("sealed conclusion tasks violate the blinded exact800 schema")
    audit_tasks, _ = _load_canonical_jsonl(
        evidence / "audit-tasks.jsonl", label="sealed audit tasks"
    )
    selected_audit_ids = _validate_blinded_mapping_commitments(
        mapping_rows,
        audit_seed=str(protocol["audit_seed"]),
    )
    expected_prepare_files = {
        "judge/tasks.jsonl": _sha256(snapshots["evidence/pixel-tasks.jsonl"]),
        "judge/protocol.json": _sha256(snapshots["evidence/pixel-protocol.json"]),
        "private/mapping.jsonl": _sha256(snapshots["evidence/mapping.jsonl"]),
        **{
            f"judge/images/{audit_id}.jpg": str(row["preview_sha256"])
            for audit_id, row in mapping_by_audit.items()
        },
    }
    if prepare_files != dict(sorted(expected_prepare_files.items())):
        raise RuntimeError("sealed fresh preparation file inventory does not reproduce")
    expected_conclusion_files = {
        "conclusion/tasks.jsonl": _sha256(snapshots["evidence/conclusion-tasks.jsonl"]),
        "conclusion/protocol.json": _sha256(snapshots["evidence/conclusion-protocol.json"]),
        "audit/tasks.jsonl": _sha256(snapshots["evidence/audit-tasks.jsonl"]),
        "audit/protocol.json": _sha256(snapshots["evidence/audit-protocol.json"]),
        **{
            f"audit/images/{audit_id}.jpg": str(mapping_by_audit[audit_id]["preview_sha256"])
            for audit_id in selected_audit_ids
        },
    }
    if conclusion_files != dict(sorted(expected_conclusion_files.items())):
        raise RuntimeError("sealed conclusion work file inventory does not reproduce")
    if (
        len(selected_audit_ids) != AUDIT_COUNT
        or len(audit_tasks) != AUDIT_COUNT
        or any(
            set(row) != {"final_audit_id", "image_relpath"}
            or row["image_relpath"] != f"images/{row['final_audit_id']}.jpg"
            for row in audit_tasks
        )
        or {str(row["final_audit_id"]) for row in audit_tasks} != selected_audit_ids
    ):
        raise RuntimeError("sealed audit tasks differ from the precommitted sample")

    pixel_cards, _ = _load_card_output(
        evidence / "pixel-cards.jsonl",
        label="sealed Opus pixel cards",
        expected_ids=set(mapping_by_audit),
    )
    teacher_cards, _ = _load_card_output(
        evidence / "teacher-cards.jsonl",
        label="sealed teacher cards",
        expected_ids=set(mapping_by_audit),
    )
    library_by_asset = {str(row["image_id"]): row["fields"] for row in library_rows}
    conclusion_text_by_rid = {str(row["rid"]): str(row["text"]) for row in conclusion_tasks}
    exact_key: dict[str, dict[str, str]] = {}
    for audit_id, mapping in mapping_by_audit.items():
        asset_id = str(mapping["asset_id"])
        truth_rid = str(mapping["truth_rid"])
        teacher_rid = str(mapping["teacher_rid"])
        if library_by_asset[asset_id] != pixel_cards[audit_id]:
            raise RuntimeError("sealed library truth differs from its pixel-card evidence")
        for rid, source, fields in (
            (truth_rid, "truth", pixel_cards[audit_id]),
            (teacher_rid, "teacher", teacher_cards[audit_id]),
        ):
            exact_key[rid] = {"asset": asset_id, "source": source}
            expected_text = f"description: {fields['description']}\nsetting: {fields['setting']}"
            if conclusion_text_by_rid[rid] != expected_text:
                raise RuntimeError("sealed blinded conclusion text differs from its card source")
    if key != dict(sorted(exact_key.items())):
        raise RuntimeError("fresh battery key differs from its private opaque RID mapping")

    conclusions, _ = _load_location_rows(
        evidence / "conclusions.jsonl",
        label="sealed Opus conclusions",
        identity_field="rid",
        expected_ids=mapping_rids,
    )
    audit_labels, _ = _load_location_rows(
        evidence / "audit.jsonl",
        label="sealed Fable audit",
        identity_field="final_audit_id",
        expected_ids=selected_audit_ids,
    )
    original_truth_by_audit = {
        audit_id: conclusions[str(mapping["truth_rid"])]
        for audit_id, mapping in mapping_by_audit.items()
    }
    disagreement_ids = {
        audit_id
        for audit_id in selected_audit_ids
        if audit_labels[audit_id] != original_truth_by_audit[audit_id]
    }
    adjudications, _ = _load_adjudications(
        evidence / "adjudications.jsonl", expected_ids=disagreement_ids
    )
    expected_truth_by_asset: dict[str, str] = {}
    expected_teacher_by_asset: dict[str, str] = {}
    for audit_id, mapping in mapping_by_audit.items():
        asset_id = str(mapping["asset_id"])
        expected_truth_by_asset[asset_id] = adjudications.get(
            audit_id, original_truth_by_audit[audit_id]
        )
        expected_teacher_by_asset[asset_id] = conclusions[str(mapping["teacher_rid"])]
    if truth_by_asset != expected_truth_by_asset or teacher_by_asset != expected_teacher_by_asset:
        raise RuntimeError("fresh scoring labels do not reproduce judge and adjudication evidence")
    if manifest["audit"] != {
        "precommitted_count": AUDIT_COUNT,
        "disagreement_count": len(disagreement_ids),
        "adjudicated_count": len(adjudications),
        "passed": True,
    }:
        raise RuntimeError("fresh audit counts do not reproduce external evidence")

    pixel_protocol, _ = _load_canonical_json(
        evidence / "pixel-protocol.json", label="sealed pixel protocol"
    )
    conclusion_protocol_payload, _ = _load_canonical_json(
        evidence / "conclusion-protocol.json", label="sealed conclusion protocol"
    )
    audit_protocol_payload, _ = _load_canonical_json(
        evidence / "audit-protocol.json", label="sealed audit protocol"
    )
    if (
        pixel_protocol
        != {
            "schema": "triage-fresh-pixel-judge-protocol-v1",
            "model": protocol["pixel_judge_model"],
            "prompt": PIXEL_JUDGE_PROMPT,
            "prompt_sha256": _sha256((PIXEL_JUDGE_PROMPT + "\n").encode()),
            "output_schema": PIXEL_CARD_SCHEMA,
            "output_schema_sha256": _sha256(_canonical_bytes(PIXEL_CARD_SCHEMA)),
        }
        or conclusion_protocol_payload
        != {
            "schema": "triage-fresh-conclusion-protocol-v1",
            "model": protocol["conclusion_judge_model"],
            "prompt": CONCLUSION_PROMPT,
            "prompt_sha256": _sha256((CONCLUSION_PROMPT + "\n").encode()),
            "output_schema": LOCATION_OUTPUT_SCHEMA,
            "output_schema_sha256": _sha256(_canonical_bytes(LOCATION_OUTPUT_SCHEMA)),
        }
        or audit_protocol_payload
        != {
            "schema": "triage-fresh-audit-protocol-v1",
            "model": protocol["audit_judge_model"],
            "prompt": AUDIT_PROMPT,
            "prompt_sha256": _sha256((AUDIT_PROMPT + "\n").encode()),
            "output_schema": AUDIT_OUTPUT_SCHEMA,
            "output_schema_sha256": _sha256(_canonical_bytes(AUDIT_OUTPUT_SCHEMA)),
        }
    ):
        raise RuntimeError("fresh truth prompt/schema/model evidence does not reproduce")
    if manifest.get("counts") != {
        "adjudications": manifest["audit"]["adjudicated_count"],  # type: ignore[index]
        "audit_rows": AUDIT_COUNT,
        "library_truth": 400,
        "teacher_labels": 400,
        "truth_labels": 400,
    }:
        raise RuntimeError("fresh truth manifest counts do not reproduce")
    if (
        snapshots["library_truth.jsonl"] != library_raw
        or snapshots["battery_key.json"] != key_raw
        or (snapshots["battery_shards/bshard_000.jsonl"] != shard_raw)
    ):
        raise AssertionError("fresh truth scoring snapshots diverged during validation")
    if (
        _load_private_bytes(root / "truth-manifest.json", label="fresh truth manifest")
        != manifest_raw
    ):
        raise RuntimeError("fresh truth manifest changed during snapshot")
    snapshots["truth-manifest.json"] = manifest_raw
    return FreshTruthPackageSnapshot(
        root=root,
        manifest=manifest,
        files=snapshots,
        truth_by_asset=truth_by_asset,
        teacher_by_asset=teacher_by_asset,
    )


def validate_fresh_truth_snapshots(
    snapshots: Mapping[str, bytes],
    *,
    expected_binding: FreshTruthBinding,
) -> FreshTruthPackageSnapshot:
    """Revalidate already-snapshotted package bytes at an in-memory API boundary."""
    if "truth-manifest.json" not in snapshots:
        raise RuntimeError("fresh truth snapshots lack truth-manifest.json")
    with tempfile.TemporaryDirectory(prefix="triage-fresh-truth-snapshot-") as temporary_name:
        root = Path(temporary_name)
        root.chmod(0o700)
        _private_mkdir(root / "battery_shards")
        _private_mkdir(root / "evidence")
        for relative, payload in snapshots.items():
            candidate = Path(relative)
            if candidate.is_absolute() or ".." in candidate.parts or len(candidate.parts) > 2:
                raise RuntimeError("fresh truth snapshot contains an unsafe relative path")
            destination = root / candidate
            if not destination.parent.is_dir():
                raise RuntimeError("fresh truth snapshot contains an unexpected directory")
            _write_new_private(destination, payload)
        return load_fresh_truth_package(root, expected_binding=expected_binding)


_SELECTION_RULE = (
    "MLP only when paired and aggregate certification accuracy improve by >=2 points, "
    "coverage is >=0.85, and abstention honesty passes"
)
_ACCEPTANCE_GATES = {
    "accuracy_on_covered_minimum": 0.97,
    "coverage_minimum": 0.85,
    "ece_covered_maximum": 0.05,
    "emitted_undetermined_teacher_multiplier_maximum": 1.5,
}


def _attempt_payload(
    *,
    binding: FreshTruthBinding,
    truth_manifest_sha256: str,
    certification_inputs_sha256: str,
) -> dict[str, object]:
    if not _is_sha256(truth_manifest_sha256) or not _is_sha256(certification_inputs_sha256):
        raise ValueError("certification attempt requires exact truth input digests")
    payload: dict[str, object] = {
        "schema": "triage-location-certification-attempt-v1",
        "status": "frozen-before-reveal",
        "binding": binding.as_manifest(),
        "truth_manifest_sha256": truth_manifest_sha256,
        "certification_inputs_sha256": certification_inputs_sha256,
        "selection_rule": _SELECTION_RULE,
        "acceptance_gates": _ACCEPTANCE_GATES,
    }
    return _self_seal(payload, "attempt_sha256")


def _attempt_identity(binding_manifest: Mapping[str, object]) -> tuple[str, str]:
    head_version = binding_manifest.get("head_version")
    fresh = binding_manifest.get("fresh_approval")
    if (
        head_version != "location-v1"
        or not isinstance(fresh, Mapping)
        or not _is_sha256(fresh.get("selection_sha256"))
    ):
        raise RuntimeError("certification attempt lacks its fresh selection/head identity")
    return str(head_version), str(fresh["selection_sha256"])


def _attempt_directory_name(binding_manifest: Mapping[str, object]) -> str:
    head_version, selection_sha256 = _attempt_identity(binding_manifest)
    return f"fresh-location-cert-v3-{head_version}-{selection_sha256}"


def _attempt_ledger_payload(
    *,
    attempt_payload: Mapping[str, object],
    canonical_attempt_path: Path,
) -> dict[str, object]:
    binding = attempt_payload["binding"]
    if not isinstance(binding, Mapping):
        raise RuntimeError("certification attempt binding is malformed")
    head_version, selection_sha256 = _attempt_identity(binding)
    candidates = binding.get("candidate_sha256")
    if not isinstance(candidates, Mapping) or set(candidates) != {"linear", "mlp"}:
        raise RuntimeError("certification attempt candidate binding is malformed")
    payload: dict[str, object] = {
        "schema": "triage-certification-attempt-ledger-entry-v1",
        "status": "attempt-path-reserved",
        "head_version": head_version,
        "fresh_selection_sha256": selection_sha256,
        "training_run_sha256": binding["training_run_sha256"],
        "candidate_sha256": dict(sorted(candidates.items())),
        "truth_manifest_sha256": attempt_payload["truth_manifest_sha256"],
        "certification_inputs_sha256": attempt_payload["certification_inputs_sha256"],
        "attempt_sha256": attempt_payload["attempt_sha256"],
        "canonical_attempt_path": str(canonical_attempt_path),
    }
    return _self_seal(payload, "ledger_entry_sha256")


def _load_attempt_ledger_entry(path: Path) -> tuple[dict[str, object], bytes]:
    payload, raw = _load_canonical_json(path, label="canonical certification attempt ledger")
    if set(payload) != {
        "schema",
        "status",
        "head_version",
        "fresh_selection_sha256",
        "training_run_sha256",
        "candidate_sha256",
        "truth_manifest_sha256",
        "certification_inputs_sha256",
        "attempt_sha256",
        "canonical_attempt_path",
        "ledger_entry_sha256",
    } or (payload.get("schema"), payload.get("status")) != (
        "triage-certification-attempt-ledger-entry-v1",
        "attempt-path-reserved",
    ):
        raise RuntimeError("canonical certification attempt ledger violates its exact schema")
    _verify_self_digest(
        payload,
        field="ledger_entry_sha256",
        label="canonical certification attempt ledger",
    )
    candidates = payload.get("candidate_sha256")
    if (
        payload.get("head_version") != "location-v1"
        or not isinstance(candidates, dict)
        or set(candidates) != {"linear", "mlp"}
        or any(not _is_sha256(value) for value in candidates.values())
        or any(
            not _is_sha256(payload.get(field))
            for field in (
                "fresh_selection_sha256",
                "training_run_sha256",
                "truth_manifest_sha256",
                "certification_inputs_sha256",
                "attempt_sha256",
                "ledger_entry_sha256",
            )
        )
        or not isinstance(payload.get("canonical_attempt_path"), str)
        or not Path(str(payload["canonical_attempt_path"])).is_absolute()
    ):
        raise RuntimeError("canonical certification attempt ledger binding is malformed")
    return payload, raw


def _ensure_attempt_ledger_directory(path: Path) -> Path:
    ledger = Path(path)
    if ledger.exists() or ledger.is_symlink():
        return _require_private_directory(ledger, label="canonical certification attempt ledger")
    parent = ledger.parent
    if not parent.exists():
        parent.mkdir(parents=True, mode=0o700)
        parent.chmod(0o700)
    _require_private_directory(parent, label="certification recovery directory")
    try:
        _private_mkdir(ledger)
        _fsync_directory(parent)
    except FileExistsError:
        pass
    return _require_private_directory(ledger, label="canonical certification attempt ledger")


def _reserve_attempt_ledger(
    *,
    attempt_payload: Mapping[str, object],
    attempt_root: Path,
    ledger_dir: Path,
) -> tuple[Path, str, bool]:
    ledger = _ensure_attempt_ledger_directory(ledger_dir)
    binding = attempt_payload["binding"]
    if not isinstance(binding, Mapping):
        raise RuntimeError("certification attempt binding is malformed")
    head_version, selection_sha256 = _attempt_identity(binding)
    ledger_path = ledger / f"{head_version}--{selection_sha256}.json"
    canonical_attempt_path = attempt_root.resolve(strict=False)
    proposed = _attempt_ledger_payload(
        attempt_payload=attempt_payload,
        canonical_attempt_path=canonical_attempt_path,
    )
    proposed_bytes = _canonical_bytes(proposed)
    existed = ledger_path.exists() or ledger_path.is_symlink()
    if existed:
        _current, current_bytes = _load_attempt_ledger_entry(ledger_path)
        if current_bytes != proposed_bytes:
            raise RuntimeError(
                "canonical certification ledger already binds a different artifact root "
                "or frozen candidate inputs"
            )
    else:
        try:
            _write_create_only_private(ledger_path, proposed_bytes)
        except RuntimeError as error:
            raise RuntimeError(
                "canonical certification ledger already binds a different artifact root "
                "or frozen candidate inputs"
            ) from error
        _current, current_bytes = _load_attempt_ledger_entry(ledger_path)
        if current_bytes != proposed_bytes:
            raise RuntimeError("canonical certification ledger changed during reservation")
    return ledger_path.resolve(), str(proposed["ledger_entry_sha256"]), existed


def _load_attempt_file(path: Path) -> tuple[dict[str, object], bytes]:
    payload, raw = _load_canonical_json(path, label="one-shot certification attempt")
    if set(payload) != {
        "schema",
        "status",
        "binding",
        "truth_manifest_sha256",
        "certification_inputs_sha256",
        "selection_rule",
        "acceptance_gates",
        "attempt_sha256",
    } or (payload.get("schema"), payload.get("status")) != (
        "triage-location-certification-attempt-v1",
        "frozen-before-reveal",
    ):
        raise RuntimeError("one-shot certification attempt violates its exact schema")
    _verify_self_digest(payload, field="attempt_sha256", label="one-shot certification attempt")
    if (
        payload.get("selection_rule") != _SELECTION_RULE
        or payload.get("acceptance_gates") != _ACCEPTANCE_GATES
    ):
        raise RuntimeError("one-shot certification policy differs from the frozen gate")
    return payload, raw


def _attempt_allowed_files(root: Path) -> set[str]:
    allowed = {
        "attempt.json",
        "reveal.json",
        "cascade-curves.json",
        "certification-metrics.json",
        "decision.json",
        "result.json",
    }
    names = {path.name for path in root.iterdir()}
    if not names <= allowed:
        raise RuntimeError("one-shot certification attempt has unexpected evidence")
    return names


def _validate_attempt_handle(
    attempt: CertificationAttempt,
) -> tuple[Path, dict[str, object]]:
    root = _require_private_directory(attempt.root, label="one-shot certification attempt")
    _attempt_allowed_files(root)
    payload, _ = _load_attempt_file(root / "attempt.json")
    if (
        payload["attempt_sha256"] != attempt.attempt_sha256
        or payload["truth_manifest_sha256"] != attempt.truth_manifest_sha256
        or payload["certification_inputs_sha256"] != attempt.certification_inputs_sha256
        or payload["binding"] != attempt.binding
    ):
        raise RuntimeError("certification attempt handle differs from its frozen record")
    _require_private_directory(
        attempt.ledger_path.parent,
        label="canonical certification attempt ledger",
    )
    ledger_payload, ledger_raw = _load_attempt_ledger_entry(attempt.ledger_path)
    expected_ledger = _attempt_ledger_payload(
        attempt_payload=payload,
        canonical_attempt_path=root.resolve(),
    )
    if (
        ledger_raw != _canonical_bytes(expected_ledger)
        or ledger_payload["ledger_entry_sha256"] != attempt.ledger_entry_sha256
    ):
        raise RuntimeError("canonical certification ledger differs from the attempt handle")
    return root, payload


def _load_reveal_file(
    path: Path,
    *,
    expected_attempt_sha256: str,
) -> tuple[dict[str, object], bytes]:
    payload, raw = _load_canonical_json(path, label="certification truth reveal")
    if set(payload) != {
        "schema",
        "status",
        "attempt_sha256",
        "truth_manifest_sha256",
        "certification_inputs_sha256",
        "reveal_sha256",
    } or (payload.get("schema"), payload.get("status")) != (
        "triage-location-certification-reveal-v1",
        "truth-reveal-committed",
    ):
        raise RuntimeError("certification truth reveal violates its exact schema")
    _verify_self_digest(payload, field="reveal_sha256", label="certification truth reveal")
    if payload.get("attempt_sha256") != expected_attempt_sha256:
        raise RuntimeError("certification truth reveal belongs to a different attempt")
    return payload, raw


def commit_certification_truth_reveal(
    attempt: CertificationAttempt,
) -> dict[str, object]:
    """Irrevocably consume the one shot immediately before any labels are opened."""
    root, attempt_payload = _validate_attempt_handle(attempt)
    for name in (
        "reveal.json",
        "certification-metrics.json",
        "cascade-curves.json",
        "decision.json",
        "result.json",
    ):
        path = root / name
        if path.exists() or path.is_symlink():
            raise RuntimeError("one-shot certification attempt is already revealed")
    reveal: dict[str, object] = {
        "schema": "triage-location-certification-reveal-v1",
        "status": "truth-reveal-committed",
        "attempt_sha256": attempt.attempt_sha256,
        "truth_manifest_sha256": attempt_payload["truth_manifest_sha256"],
        "certification_inputs_sha256": attempt_payload["certification_inputs_sha256"],
    }
    reveal = _self_seal(reveal, "reveal_sha256")
    try:
        _write_new_private(root / "reveal.json", _canonical_bytes(reveal))
    except FileExistsError as error:
        raise RuntimeError("one-shot certification attempt is already revealed") from error
    _fsync_directory(root)
    return reveal


def open_certification_attempt(
    *,
    artifact_dir: Path,
    binding: FreshTruthBinding,
    truth_manifest_sha256: str,
    certification_inputs_sha256: str,
    _test_ledger_dir: Path | None = None,
) -> tuple[CertificationAttempt, CertificationDecisionSnapshot | None]:
    """Open a pre-reveal attempt or a truth-free publication resume."""
    if _test_ledger_dir is not None and "PYTEST_CURRENT_TEST" not in os.environ:
        raise ValueError("certification ledger override is test-only")
    proposed = _attempt_payload(
        binding=binding,
        truth_manifest_sha256=truth_manifest_sha256,
        certification_inputs_sha256=certification_inputs_sha256,
    )
    proposed_bytes = _canonical_bytes(proposed)
    artifacts = _require_private_directory(artifact_dir, label="triage artifact directory")
    attempts = artifacts / "certification-attempts"
    if attempts.exists() or attempts.is_symlink():
        attempts = _require_private_directory(attempts, label="certification attempts")
    else:
        _private_mkdir(attempts)
        _fsync_directory(artifacts)
    binding_manifest = proposed["binding"]
    if not isinstance(binding_manifest, Mapping):  # pragma: no cover - constructed above
        raise AssertionError("certification attempt binding is malformed")
    root = attempts / _attempt_directory_name(binding_manifest)
    ledger_path, ledger_entry_sha256, ledger_existed = _reserve_attempt_ledger(
        attempt_payload=proposed,
        attempt_root=root,
        ledger_dir=(
            DEFAULT_CERTIFICATION_LEDGER_DIR if _test_ledger_dir is None else _test_ledger_dir
        ),
    )
    resumed = ledger_existed
    if not root.exists() and not root.is_symlink():
        with tempfile.TemporaryDirectory(
            prefix=f".{root.name}.staging-", dir=attempts
        ) as temporary_name:
            staging = Path(temporary_name)
            staging.chmod(0o700)
            _write_new_private(staging / "attempt.json", proposed_bytes)
            _fsync_directory(staging)
            _publish_staged_directory(staging, root)
    else:
        resumed = True
    root = _require_private_directory(root, label="one-shot certification attempt")
    names = _attempt_allowed_files(root)
    current, current_bytes = _load_attempt_file(root / "attempt.json")
    if current_bytes != proposed_bytes:
        raise RuntimeError("one-shot certification attempt already binds different frozen inputs")
    attempt = CertificationAttempt(
        root=root,
        ledger_path=ledger_path,
        ledger_entry_sha256=ledger_entry_sha256,
        attempt_sha256=str(current["attempt_sha256"]),
        truth_manifest_sha256=str(current["truth_manifest_sha256"]),
        certification_inputs_sha256=str(current["certification_inputs_sha256"]),
        binding=dict(current["binding"]),  # type: ignore[arg-type]
        resumed=resumed,
    )
    if "result.json" in names:
        _load_certification_result(attempt)
        raise RuntimeError("one-shot certification attempt is already completed")
    if "reveal.json" in names:
        _load_reveal_file(
            root / "reveal.json",
            expected_attempt_sha256=str(current["attempt_sha256"]),
        )
        decision_files = {
            "certification-metrics.json",
            "cascade-curves.json",
            "decision.json",
        }
        present = names & decision_files
        if present == decision_files:
            return attempt, _load_certification_decision(attempt)
        if present:
            raise RuntimeError(
                "one-shot certification attempt crashed before its scored decision sealed"
            )
        raise RuntimeError(
            "one-shot certification attempt revealed truth before its scored decision sealed"
        )
    if names & {
        "certification-metrics.json",
        "cascade-curves.json",
        "decision.json",
        "result.json",
    }:
        raise RuntimeError("one-shot certification attempt has result evidence before reveal")
    return attempt, None


def begin_certification_attempt(
    *,
    artifact_dir: Path,
    binding: FreshTruthBinding,
    truth_manifest_sha256: str,
    certification_inputs_sha256: str,
    _test_ledger_dir: Path | None = None,
) -> CertificationAttempt:
    """Freeze the sole fresh400 attempt, allowing only identical pre-reveal resume."""
    attempt, decision = open_certification_attempt(
        artifact_dir=artifact_dir,
        binding=binding,
        truth_manifest_sha256=truth_manifest_sha256,
        certification_inputs_sha256=certification_inputs_sha256,
        _test_ledger_dir=_test_ledger_dir,
    )
    if decision is not None:
        raise RuntimeError("certification publication is pending sealed-decision resume")
    return attempt


def _semantic_json_sha256(payload: object) -> str:
    return _sha256(_canonical_bytes(payload)[:-1])


def _validate_terminal_report(
    *,
    attempt_payload: Mapping[str, object],
    report: Mapping[str, object],
    curves: Mapping[str, object],
    promoted: bool,
) -> None:
    acceptance = report.get("acceptance")
    honesty = report.get("abstention_honesty")
    if (
        report.get("head_version") != "location-v1"
        or report.get("training_run_sha256") != attempt_payload["binding"]["training_run_sha256"]  # type: ignore[index]
        or report.get("certification_inputs_sha256")
        != attempt_payload["certification_inputs_sha256"]
        or report.get("certification_attempt_sha256") != attempt_payload["attempt_sha256"]
        or report.get("selection_rule") != attempt_payload["selection_rule"]
        or report.get("usable_certification_labels") != FRESH_TRUTH_COUNT
        or report.get("missing_certification_images") != 0
        or report.get("selected_candidate") not in {"linear", "mlp"}
        or not isinstance(acceptance, Mapping)
        or acceptance.get("accuracy_target") != _ACCEPTANCE_GATES["accuracy_on_covered_minimum"]
        or acceptance.get("coverage_target") != _ACCEPTANCE_GATES["coverage_minimum"]
        or type(acceptance.get("passed")) is not bool
        or report.get("promotion_authorized") is not acceptance.get("passed")
        or report.get("promoted") is not promoted
        or (promoted and acceptance.get("passed") is not True)
        or not isinstance(honesty, Mapping)
        or honesty.get("ece_target") != _ACCEPTANCE_GATES["ece_covered_maximum"]
    ):
        raise ValueError("certification report differs from its frozen attempt or gates")
    if set(curves) != {"linear", "mlp"} or report.get(
        "cascade_curves_sha256"
    ) != _semantic_json_sha256(dict(curves)):
        raise ValueError("certification curves differ from the report commitment")
    evidence_sha256 = report.get("certification_evidence_sha256")
    evidence = dict(report)
    evidence.pop("certification_evidence_sha256", None)
    evidence.pop("promoted", None)
    if not _is_sha256(evidence_sha256) or evidence_sha256 != _semantic_json_sha256(evidence):
        raise ValueError("certification report evidence digest does not reproduce")


def _load_certification_decision(
    attempt: CertificationAttempt,
) -> CertificationDecisionSnapshot:
    root, attempt_payload = _validate_attempt_handle(attempt)
    reveal, _ = _load_reveal_file(
        root / "reveal.json",
        expected_attempt_sha256=str(attempt_payload["attempt_sha256"]),
    )
    if (
        reveal["truth_manifest_sha256"] != attempt_payload["truth_manifest_sha256"]
        or reveal["certification_inputs_sha256"] != attempt_payload["certification_inputs_sha256"]
    ):
        raise RuntimeError("certification truth reveal differs from its frozen attempt")
    report, report_raw = _load_canonical_json(
        root / "certification-metrics.json", label="certification metrics"
    )
    curves, curves_raw = _load_canonical_json(
        root / "cascade-curves.json", label="certification curves"
    )
    decision, _decision_raw = _load_canonical_json(
        root / "decision.json", label="sealed certification decision"
    )
    if (
        set(decision)
        != {
            "schema",
            "status",
            "attempt_sha256",
            "certification_metrics_sha256",
            "cascade_curves_sha256",
            "acceptance_passed",
            "promoted",
            "selected_candidate",
            "candidate_sha256",
            "training_run_sha256",
            "facts",
            "decision_sha256",
        }
        or decision.get("schema") != "triage-location-certification-decision-v1"
    ):
        raise RuntimeError("sealed certification decision violates its exact schema")
    _verify_self_digest(
        decision,
        field="decision_sha256",
        label="sealed certification decision",
    )
    passed = decision.get("acceptance_passed")
    promoted = decision.get("promoted")
    selected = decision.get("selected_candidate")
    candidates = attempt.binding.get("candidate_sha256")
    facts = decision.get("facts")
    if (
        type(passed) is not bool
        or type(promoted) is not bool
        or passed is not promoted
        or decision.get("status")
        != ("scored-promotion-authorized" if passed else "scored-rejected")
        or decision.get("attempt_sha256") != attempt.attempt_sha256
        or decision.get("certification_metrics_sha256") != _sha256(report_raw)
        or decision.get("cascade_curves_sha256") != _sha256(curves_raw)
        or selected not in {"linear", "mlp"}
        or not isinstance(candidates, Mapping)
        or decision.get("candidate_sha256") != candidates.get(selected)
        or decision.get("training_run_sha256") != attempt.binding.get("training_run_sha256")
        or not isinstance(facts, list)
    ):
        raise RuntimeError("sealed certification decision binding does not reproduce")
    expected_fact_count = 0
    seen_assets: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {
            "asset_id",
            "label",
            "confidence",
            "covered",
        }:
            raise RuntimeError("sealed certification decision facts violate their exact schema")
        asset_id = fact.get("asset_id")
        confidence = fact.get("confidence")
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or asset_id in seen_assets
            or fact.get("label") not in LOCATION_CLASSES
            or type(confidence) not in {int, float}
            or not 0.0 <= float(confidence) <= 1.0
            or type(fact.get("covered")) is not bool
        ):
            raise RuntimeError("sealed certification decision contains invalid facts")
        seen_assets.add(asset_id)
    if len(facts) != expected_fact_count:
        raise RuntimeError("sealed certification decision has the wrong fact count")
    _validate_terminal_report(
        attempt_payload=attempt_payload,
        report=report,
        curves=curves,
        promoted=bool(promoted),
    )
    if report["acceptance"]["passed"] is not passed:  # type: ignore[index]
        raise RuntimeError("sealed certification decision differs from its terminal report")
    return CertificationDecisionSnapshot(
        attempt=attempt,
        report=report,
        curves=curves,
        decision=decision,
    )


def _load_certification_result(
    attempt: CertificationAttempt,
) -> tuple[dict[str, object], bytes]:
    root, attempt_payload = _validate_attempt_handle(attempt)
    sealed_decision = _load_certification_decision(attempt)
    report = sealed_decision.report
    decision = sealed_decision.decision
    result, result_raw = _load_canonical_json(
        root / "result.json", label="certification attempt result"
    )
    if (
        set(result)
        != {
            "schema",
            "status",
            "attempt_sha256",
            "certification_metrics_sha256",
            "cascade_curves_sha256",
            "decision_sha256",
            "acceptance_passed",
            "promoted",
            "result_sha256",
        }
        or result.get("schema") != "triage-location-certification-result-v1"
    ):
        raise RuntimeError("certification attempt result violates its exact schema")
    _verify_self_digest(result, field="result_sha256", label="certification attempt result")
    passed = result.get("acceptance_passed")
    promoted = result.get("promoted")
    if (
        type(passed) is not bool
        or type(promoted) is not bool
        or result.get("status") != ("revealed-passed" if passed else "revealed-failed")
        or passed is not decision["acceptance_passed"]
        or promoted is not decision["promoted"]
        or result.get("attempt_sha256") != attempt_payload["attempt_sha256"]
        or result.get("certification_metrics_sha256") != decision["certification_metrics_sha256"]
        or result.get("cascade_curves_sha256") != decision["cascade_curves_sha256"]
        or result.get("decision_sha256") != decision["decision_sha256"]
    ):
        raise RuntimeError("certification attempt result digest closure does not reproduce")
    if report["acceptance"]["passed"] is not passed:  # type: ignore[index]
        raise RuntimeError("certification result differs from its terminal report")
    return result, result_raw


def seal_certification_decision(
    *,
    attempt: CertificationAttempt,
    report: Mapping[str, object],
    curves: Mapping[str, object],
    facts: Sequence[Mapping[str, object]],
) -> CertificationDecisionSnapshot:
    """Commit scored evidence before any publication side effect."""
    root, attempt_payload = _validate_attempt_handle(attempt)
    result_path = root / "result.json"
    if result_path.exists() or result_path.is_symlink():
        _load_certification_result(attempt)
        raise RuntimeError("one-shot certification attempt is already completed")
    reveal_path = root / "reveal.json"
    if not reveal_path.exists() and not reveal_path.is_symlink():
        raise RuntimeError("certification truth was not durably revealed before scoring")
    reveal, _ = _load_reveal_file(
        reveal_path,
        expected_attempt_sha256=attempt.attempt_sha256,
    )
    if (
        reveal["truth_manifest_sha256"] != attempt.truth_manifest_sha256
        or reveal["certification_inputs_sha256"] != attempt.certification_inputs_sha256
    ):
        raise RuntimeError("certification truth reveal differs from its frozen attempt")
    acceptance = report.get("acceptance")
    if not isinstance(acceptance, Mapping) or type(acceptance.get("passed")) is not bool:
        raise ValueError("certification report lacks its terminal acceptance decision")
    promoted = bool(acceptance["passed"])
    _validate_terminal_report(
        attempt_payload=attempt_payload,
        report=report,
        curves=curves,
        promoted=promoted,
    )
    report_bytes = _canonical_bytes(dict(report))
    curves_bytes = _canonical_bytes(dict(curves))
    normalized_facts = [dict(fact) for fact in facts]
    expected_fact_count = 0
    seen_assets: set[str] = set()
    for fact in normalized_facts:
        asset_id = fact.get("asset_id")
        confidence = fact.get("confidence")
        if (
            set(fact) != {"asset_id", "label", "confidence", "covered"}
            or not isinstance(asset_id, str)
            or not asset_id
            or asset_id in seen_assets
            or fact.get("label") not in LOCATION_CLASSES
            or type(confidence) not in {int, float}
            or not 0.0 <= float(confidence) <= 1.0
            or type(fact.get("covered")) is not bool
        ):
            raise ValueError("certification publication facts are invalid")
        seen_assets.add(asset_id)
    if len(normalized_facts) != expected_fact_count:
        raise ValueError("certification publication facts have the wrong cardinality")
    selected = str(report["selected_candidate"])
    candidates = attempt.binding["candidate_sha256"]
    decision: dict[str, object] = {
        "schema": "triage-location-certification-decision-v1",
        "status": "scored-promotion-authorized" if promoted else "scored-rejected",
        "attempt_sha256": attempt.attempt_sha256,
        "certification_metrics_sha256": _sha256(report_bytes),
        "cascade_curves_sha256": _sha256(curves_bytes),
        "acceptance_passed": promoted,
        "promoted": promoted,
        "selected_candidate": selected,
        "candidate_sha256": candidates[selected],  # type: ignore[index]
        "training_run_sha256": attempt.binding["training_run_sha256"],
        "facts": normalized_facts,
    }
    decision = _self_seal(decision, "decision_sha256")
    _write_create_only_private(root / "certification-metrics.json", report_bytes)
    _write_create_only_private(root / "cascade-curves.json", curves_bytes)
    _write_create_only_private(root / "decision.json", _canonical_bytes(decision))
    _fsync_directory(root)
    return _load_certification_decision(attempt)


def finalize_certification_attempt(
    *,
    sealed_decision: CertificationDecisionSnapshot,
) -> dict[str, object]:
    """Commit the terminal result after publication has completed."""
    attempt = sealed_decision.attempt
    root, _attempt_payload = _validate_attempt_handle(attempt)
    current = _load_certification_decision(attempt)
    if current.decision != sealed_decision.decision:
        raise RuntimeError("sealed certification decision changed before finalization")
    result_path = root / "result.json"
    if result_path.exists() or result_path.is_symlink():
        _load_certification_result(attempt)
        raise RuntimeError("one-shot certification attempt is already completed")
    decision = current.decision
    passed = bool(decision["acceptance_passed"])
    promoted = bool(decision["promoted"])
    result: dict[str, object] = {
        "schema": "triage-location-certification-result-v1",
        "status": "revealed-passed" if passed else "revealed-failed",
        "attempt_sha256": attempt.attempt_sha256,
        "certification_metrics_sha256": decision["certification_metrics_sha256"],
        "cascade_curves_sha256": decision["cascade_curves_sha256"],
        "decision_sha256": decision["decision_sha256"],
        "acceptance_passed": passed,
        "promoted": promoted,
    }
    result = _self_seal(result, "result_sha256")
    _write_create_only_private(result_path, _canonical_bytes(result))
    _fsync_directory(root)
    sealed, _ = _load_certification_result(attempt)
    return sealed


def seal_certification_attempt(
    *,
    attempt: CertificationAttempt,
    report: Mapping[str, object],
    curves: Mapping[str, object],
    promoted: bool,
) -> dict[str, object]:
    """Compatibility wrapper for a rejected decision with no publication work."""
    acceptance = report.get("acceptance")
    if not isinstance(acceptance, Mapping) or promoted is not bool(acceptance.get("passed")):
        raise ValueError("certification promotion differs from its scored decision")
    sealed = seal_certification_decision(
        attempt=attempt,
        report=report,
        curves=curves,
        facts=(),
    )
    return finalize_certification_attempt(sealed_decision=sealed)


def _add_memory_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-working-set-gib",
        type=float,
        default=8.0,
        help="this offline process's RSS ceiling (hard maximum: 8 GiB)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare", help="freeze exact400 opaque pixel tasks")
    prepare.add_argument("--artifact-dir", type=Path, required=True)
    prepare.add_argument("--approval-dir", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--pixel-judge-model", default="claude-opus-5")
    prepare.add_argument("--conclusion-judge-model", default="claude-opus-5")
    prepare.add_argument("--audit-judge-model", default="claude-fable-5")
    prepare.add_argument("--teacher-model", default=TEACHER_MODEL)
    _add_memory_argument(prepare)

    conclusions = subcommands.add_parser(
        "prepare-conclusions", help="freeze blinded text and audit tasks"
    )
    conclusions.add_argument("--prepared-dir", type=Path, required=True)
    conclusions.add_argument("--pixel-cards", type=Path, required=True)
    conclusions.add_argument("--teacher-cards", type=Path, required=True)
    conclusions.add_argument("--output-dir", type=Path, required=True)
    _add_memory_argument(conclusions)

    finalize = subcommands.add_parser("finalize", help="seal external judge outputs")
    finalize.add_argument("--prepared-dir", type=Path, required=True)
    finalize.add_argument("--conclusion-work-dir", type=Path, required=True)
    finalize.add_argument("--pixel-cards", type=Path, required=True)
    finalize.add_argument("--teacher-cards", type=Path, required=True)
    finalize.add_argument("--conclusions", type=Path, required=True)
    finalize.add_argument("--audit", type=Path, required=True)
    finalize.add_argument("--adjudications", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    _add_memory_argument(finalize)

    validate = subcommands.add_parser("validate", help="authenticate a sealed package")
    validate.add_argument("--truth-dir", type=Path, required=True)
    _add_memory_argument(validate)
    return parser


def _live_sources_and_binding(
    *,
    artifact_dir: Path,
    approval_dir: Path,
    memory_guard: object,
) -> tuple[tuple[FreshTruthSource, ...], FreshTruthBinding]:
    from .calibrate_certify import (
        load_fresh_certification_snapshot,
        require_active_certification_source,
    )
    from .train import snapshot_training_bundle

    guard = memory_guard
    if not callable(guard):  # pragma: no cover - internal misuse
        raise TypeError("memory guard must be callable")
    training = snapshot_training_bundle(artifact_dir)
    source = require_active_certification_source(training.report)
    approved = load_fresh_certification_snapshot(
        approval_dir,
        expected_selection_sha256=source["fresh_truth_selection_sha256"],
        expected_approval_private_sha256=source["fresh_truth_approval_private_sha256"],
        expected_approval_public_sha256=source["fresh_truth_approval_public_sha256"],
        memory_guard=guard,
    )
    candidate_sha256 = {
        name: _sha256(training.component_bytes[f"{name}_candidate"]) for name in ("linear", "mlp")
    }
    binding = FreshTruthBinding(
        head_version="location-v1",
        training_run_sha256=training.training_run_sha256,
        candidate_sha256=candidate_sha256,
        inventory_sha256=approved.inventory_sha256,
        selection_sha256=approved.selection_sha256,
        certification_index_sha256=approved.certification_index_sha256,
        approval_private_sha256=approved.approval_private_sha256,
        approval_public_sha256=approved.approval_public_sha256,
        image_set_sha256=approved.image_set_sha256,
        pixel_snapshot_sha256=approved.pixel_snapshot_sha256,
    )
    sources = tuple(
        FreshTruthSource(
            asset_id=row.asset_id,
            final_audit_id=row.final_audit_id,
            preview_sha256=row.preview_sha256,
            pixel_bytes=approved.pixel_bytes[row.asset_id],
        )
        for row in approved.rows
    )
    guard()
    return sources, binding


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from .memory import (
        acquire_recovery_pipeline_lock,
        ensure_offline_process_memory,
        validate_offline_working_set_gib,
    )

    try:
        maximum = validate_offline_working_set_gib(args.max_working_set_gib)
    except ValueError as error:
        raise SystemExit(f"STOP: {error}") from error

    def memory_guard() -> int:
        return ensure_offline_process_memory(max_working_set_gib=maximum)

    memory_guard()
    if args.command == "validate":
        package = load_fresh_truth_package(args.truth_dir)
        memory_guard()
        print(
            "fresh truth valid: "
            f"truth={len(package.truth_by_asset)} teacher={len(package.teacher_by_asset)}",
            flush=True,
        )
        return 0

    lock = acquire_recovery_pipeline_lock()
    try:
        if args.command == "prepare":
            sources, binding = _live_sources_and_binding(
                artifact_dir=args.artifact_dir,
                approval_dir=args.approval_dir,
                memory_guard=memory_guard,
            )
            manifest = prepare_fresh_truth_tasks(
                sources=sources,
                binding=binding,
                output_dir=args.output_dir,
                pixel_judge_model=args.pixel_judge_model,
                conclusion_judge_model=args.conclusion_judge_model,
                audit_judge_model=args.audit_judge_model,
                teacher_model=args.teacher_model,
            )
            identity = manifest["prepare_sha256"]
        elif args.command == "prepare-conclusions":
            manifest = prepare_blinded_conclusion_tasks(
                prepared_dir=args.prepared_dir,
                pixel_cards_path=args.pixel_cards,
                teacher_cards_path=args.teacher_cards,
                output_dir=args.output_dir,
            )
            identity = manifest["conclusion_work_sha256"]
        else:
            manifest = finalize_fresh_truth_package(
                prepared_dir=args.prepared_dir,
                conclusion_work_dir=args.conclusion_work_dir,
                pixel_cards_path=args.pixel_cards,
                teacher_cards_path=args.teacher_cards,
                conclusions_path=args.conclusions,
                audit_path=args.audit,
                adjudications_path=args.adjudications,
                output_dir=args.output_dir,
            )
            identity = manifest["truth_manifest_sha256"]
        memory_guard()
        print(f"fresh truth {args.command}: {identity}", flush=True)
        return 0
    finally:
        lock.close()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
