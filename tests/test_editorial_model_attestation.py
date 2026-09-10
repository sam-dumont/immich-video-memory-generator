"""Public-artifact attestation for the one 2022 text-model wall."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from immich_memories.analysis import editorial_model_attestation as attestation


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()  # noqa: S324


def _public_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    root = tmp_path / "Qwen3-VL-30B-A3B-Instruct-4bit"
    root.mkdir()
    (root / "config.json").write_text('{"model_type":"qwen3_vl_moe"}\n')
    (root / "README.md").write_text("license: apache-2.0\n")
    index = {
        "weight_map": {
            f"tensor-{number}": f"stale-{number:02d}.safetensors" for number in range(13)
        }
    }
    (root / "model.safetensors.index.json").write_text(json.dumps(index))
    shards = {}
    files = {}
    for path in (
        root / "config.json",
        root / "README.md",
        root / "model.safetensors.index.json",
    ):
        files[path.name] = {"size": path.stat().st_size, "blob_id": _git_blob(path)}
    for number in range(1, 5):
        name = f"model-{number:05d}-of-00004.safetensors"
        path = root / name
        path.write_bytes(bytes((number,)) * number)
        digest = hashlib.sha256(f"declared-{number}".encode()).hexdigest()
        shards[name] = (number, digest)
        files[name] = {
            "size": number,
            "lfs_size": number,
            "lfs_sha256": digest,
        }
    tree = root / ".cache" / "huggingface" / "trees" / (attestation.EXPECTED_REVISION + ".json")
    tree.parent.mkdir(parents=True)
    tree.write_text(json.dumps({"format_version": 1, "files": files}))
    monkeypatch.setattr(attestation, "EXPECTED_MODEL_ROOT", root)
    monkeypatch.setattr(attestation, "EXPECTED_TREE_SHA256", _sha256(tree))
    monkeypatch.setattr(attestation, "EXPECTED_CONFIG_SHA256", _sha256(root / "config.json"))
    monkeypatch.setattr(attestation, "EXPECTED_README_SHA256", _sha256(root / "README.md"))
    monkeypatch.setattr(attestation, "EXPECTED_SHARDS", shards)
    status = {
        "models": [
            {
                "id": attestation.EXPECTED_MODEL_ID,
                "model_path": str(root),
                "loaded": True,
                "is_loading": False,
                "engine_type": "vlm",
                "model_type": "vlm",
                "config_model_type": "qwen3_vl_moe",
                "source_type": "local",
                "source_repo_id": None,
            }
        ]
    }
    return root, status


def test_attestation_binds_the_live_id_to_the_pinned_public_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, status = _public_artifact(tmp_path, monkeypatch)

    evidence = attestation.attest_public_text_model(status)

    assert evidence["attestation_level"] == "live-binding-plus-local-hf-provenance"
    assert evidence["requested_model_id"] == attestation.EXPECTED_MODEL_ID
    assert evidence["live_binding"]["loaded"] is True
    assert evidence["artifact"]["revision"] == attestation.EXPECTED_REVISION
    assert evidence["artifact"]["weight_content_rehashed"] is False
    assert evidence["artifact"]["declared_shard_bytes"] == 10
    assert evidence["artifact"]["index_anomaly"] == {
        "status": "upstream-stale",
        "index_referenced_shards": 13,
        "revision_tree_shards": 4,
    }
    assert "/Users/" not in json.dumps(evidence)


def test_attestation_rejects_a_different_or_unloaded_live_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, status = _public_artifact(tmp_path, monkeypatch)
    status["models"][0]["loaded"] = False
    with pytest.raises(ValueError, match="live public text model"):
        attestation.attest_public_text_model(status)

    status["models"][0]["loaded"] = True
    status["models"][0]["model_path"] = str(root / "different")
    with pytest.raises(ValueError, match="physical artifact"):
        attestation.attest_public_text_model(status)


def test_attestation_rejects_any_shard_or_manifest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, status = _public_artifact(tmp_path, monkeypatch)
    (root / "model-00004-of-00004.safetensors").write_bytes(b"wrong size")

    with pytest.raises(ValueError, match="public text model artifact"):
        attestation.attest_public_text_model(status)
