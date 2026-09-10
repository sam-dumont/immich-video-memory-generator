"""Bounded public-artifact attestation for the reference editorial text model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

EXPECTED_MODEL_ID = "Qwen3-VL-30B-A3B-Instruct-4bit"
EXPECTED_REPO_ID = "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
EXPECTED_REVISION = "0555d34cb1ed80c0e61a5635194c70027b4c2ff3"
EXPECTED_MODEL_ROOT = Path.home() / ".omlx/models/mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
EXPECTED_TREE_SHA256 = "508aee86f5b4687f9409752a98159f3b0f2712db40cb0f33b10b44c17fc554fa"
EXPECTED_CONFIG_SHA256 = "a359e1f7b90a5ea1db52063e03c652a0ab1df76bd87c40bd0e0ed4d0cc91a43c"
EXPECTED_README_SHA256 = "6fece97888d2e5b294f1c27f732d8fba449af820a7b1a8faae3c5e3e0dc15db0"
EXPECTED_SHARDS = {
    "model-00001-of-00004.safetensors": (
        5_345_270_475,
        "6c5bf3700d411abeee819ec2398da2548726ef3eceda910b06a26d9af1d47415",
    ),
    "model-00002-of-00004.safetensors": (
        5_364_684_988,
        "8fc999f5fdcada0a7c8b20064467fa3c7a92ff9cd5625a81d985e349de00e7b5",
    ),
    "model-00003-of-00004.safetensors": (
        5_274_796_907,
        "70c39c70350f6e7f40c717c99fdc186743d2867975c00e42ccaf605a22cd1d83",
    ),
    "model-00004-of-00004.safetensors": (
        2_267_351_303,
        "cde81617b0edc52eb2c24c02316840c4bc8fefe1239508c9457cd2c10d39d228",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    prefix = f"blob {len(data)}\0".encode()
    return hashlib.sha1(prefix + data, usedforsecurity=False).hexdigest()


def _live_binding(payload: object) -> Mapping[str, object]:
    rows = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("oMLX model status has the wrong shape")
    exact = [row for row in rows if isinstance(row, Mapping) and row.get("id") == EXPECTED_MODEL_ID]
    aliases = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("model_alias") == EXPECTED_MODEL_ID
        and row.get("id") != EXPECTED_MODEL_ID
    ]
    if len(exact) != 1 or aliases:
        raise ValueError("live public text model binding is missing or ambiguous")
    row = exact[0]
    required = {
        "loaded": True,
        "is_loading": False,
        "engine_type": "vlm",
        "model_type": "vlm",
        "config_model_type": "qwen3_vl_moe",
    }
    if any(row.get(name) != value for name, value in required.items()):
        raise ValueError("live public text model is not fully loaded with the pinned type")
    return row


def _artifact_files(root: Path) -> tuple[Mapping[str, object], Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("public text model physical artifact root is unavailable")
    tree_path = root / ".cache/huggingface/trees" / f"{EXPECTED_REVISION}.json"
    if (
        tree_path.is_symlink()
        or not tree_path.is_file()
        or _sha256(tree_path) != EXPECTED_TREE_SHA256
    ):
        raise ValueError("public text model artifact revision manifest drifted")
    try:
        payload = json.loads(tree_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("public text model artifact revision manifest is unreadable") from exc
    files = payload.get("files") if isinstance(payload, Mapping) else None
    if not isinstance(files, Mapping):
        raise ValueError("public text model artifact revision manifest has the wrong shape")
    return files, tree_path


def _verify_small_files(root: Path, files: Mapping[str, object]) -> None:
    actual_names = {path.name for path in root.iterdir() if path.is_file()}
    if actual_names != set(files):
        raise ValueError("public text model artifact file set drifted")
    for name, raw_entry in files.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or not isinstance(raw_entry, Mapping):
            raise ValueError("public text model artifact contains an unsafe file")
        if path.stat().st_size != raw_entry.get("size"):
            raise ValueError("public text model artifact file size drifted")
        if name.endswith(".safetensors"):
            continue
        expected_digest = raw_entry.get("lfs_sha256")
        actual_digest = _sha256(path) if expected_digest else _git_blob_sha1(path)
        if actual_digest != (expected_digest or raw_entry.get("blob_id")):
            raise ValueError("public text model artifact small-file digest drifted")


def _verify_shard_declarations(files: Mapping[str, object]) -> None:
    shard_entries: dict[str, dict[str, object]] = {
        name: entry
        for name, entry in files.items()
        if isinstance(entry, dict) and name.endswith(".safetensors")
    }
    if set(shard_entries) != set(EXPECTED_SHARDS):
        raise ValueError("public text model artifact shard set drifted")
    for name, (size, digest) in EXPECTED_SHARDS.items():
        entry = shard_entries[name]
        if (
            entry.get("size") != size
            or entry.get("lfs_size") != size
            or entry.get("lfs_sha256") != digest
        ):
            raise ValueError("public text model artifact shard declaration drifted")


def _verify_semantic_files(root: Path) -> None:
    index = json.loads((root / "model.safetensors.index.json").read_text(encoding="utf-8"))
    weight_map = index.get("weight_map") if isinstance(index, Mapping) else None
    referenced = set(weight_map.values()) if isinstance(weight_map, Mapping) else set()
    if len(referenced) != 13 or referenced == set(EXPECTED_SHARDS):
        raise ValueError("public text model artifact has an unknown weight-index state")
    if (
        _sha256(root / "config.json") != EXPECTED_CONFIG_SHA256
        or _sha256(root / "README.md") != EXPECTED_README_SHA256
    ):
        raise ValueError("public text model artifact semantic files drifted")


def _verify_artifact(root: Path) -> dict[str, object]:
    files, tree_path = _artifact_files(root)
    _verify_small_files(root, files)
    _verify_shard_declarations(files)
    _verify_semantic_files(root)
    return {
        "repo_id": EXPECTED_REPO_ID,
        "revision": EXPECTED_REVISION,
        "tree_manifest_sha256": _sha256(tree_path),
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "readme_sha256": EXPECTED_README_SHA256,
        "declared_shard_bytes": sum(size for size, _digest in EXPECTED_SHARDS.values()),
        "declared_lfs_sha256": [digest for _size, digest in EXPECTED_SHARDS.values()],
        "weight_content_rehashed": False,
        "index_anomaly": {
            "status": "upstream-stale",
            "index_referenced_shards": 13,
            "revision_tree_shards": len(EXPECTED_SHARDS),
        },
    }


def attest_public_text_model(status_payload: object) -> dict[str, object]:
    """Bind a live oMLX row to the pinned local public-repository artifact."""
    row = _live_binding(status_payload)
    model_path = row.get("model_path")
    if (
        not isinstance(model_path, str)
        or Path(model_path).resolve() != EXPECTED_MODEL_ROOT.resolve()
    ):
        raise ValueError("live public text model is bound to a different physical artifact")
    artifact = _verify_artifact(EXPECTED_MODEL_ROOT)
    return {
        "attestation_level": "live-binding-plus-local-hf-provenance",
        "requested_model_id": EXPECTED_MODEL_ID,
        "live_binding": {
            "model_path": "$OMLX_HOME/models/mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit",
            "loaded": row["loaded"],
            "engine_type": row["engine_type"],
            "config_model_type": row["config_model_type"],
            "source_type": row.get("source_type"),
            "source_repo_id": row.get("source_repo_id"),
        },
        "artifact": artifact,
        "claim": (
            "The live oMLX engine maps to the pinned local public-repository artifact; "
            "weight bytes were not fully rehashed."
        ),
    }
