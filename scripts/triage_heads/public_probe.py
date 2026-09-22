#!/usr/bin/env python3
"""Four-cell transfer probe for the triage heads (probe branch, not a product path).

The question: can the owner-trained heads ship as-is, or does the product need
heads trained on the public corpus?  Per head, four cells of teacher agreement:

    owner-trained  x  owner test split    (the sealed train-metrics number)
    owner-trained  x  OI-3,000            (does the owner library transfer out?)
    public-trained x  OI-400 cert rows    (public heads on their own holdout)
    public-trained x  owner test split    (does the public default transfer in?)

Stages, each resumable and each a prerequisite of the next:

    label  the 30B teacher labels the sealed public-corpus-v1 rows (needs :9999)
    embed  DINOv2-small CPU packs into a probe-private cache
    score  fit public heads (author-grouped split, public PCA) and fill the table
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from scripts.triage_heads.embed import (
    AssetInput,
    EmbeddingCache,
    EncoderSpec,
    create_onnx_session,
    embed_assets,
    verify_onnx_artifact,
)
from scripts.triage_heads.generate_labels import (
    LabelAsset,
    _resolve_local_endpoint,
    _wal_rows,
    ensure_process_tree_memory,
    label_assets_multihead,
)
from scripts.triage_heads.head_specs import ACTIVE_HEAD_SPECS, LOCATION, HeadSpec
from scripts.triage_heads.train import (
    PCAArtifact,
    fit_linear_candidate,
    fit_mlp_candidate,
    fit_pca,
    load_linear_artifact,
    load_mlp_artifact,
    load_pca_artifact,
    project_deployment_features,
    resolve_training_bundle,
)

MATRIX_ROOT = Path.home() / ".immich-memories-matrix" / "triage-heads"
DEFAULT_PARTITION = MATRIX_ROOT / "public-corpus-v1" / "private" / "partition.jsonl"
DEFAULT_PROBE_DIR = MATRIX_ROOT / "public-probe-v1"
DEFAULT_ONNX = MATRIX_ROOT / "dinov2-small-ed25f3a" / "model.onnx"
DEFAULT_EMBED_META = MATRIX_ROOT / "embed-meta-cpu.json"
DEFAULT_OWNER_CACHE = MATRIX_ROOT / "embeddings.db"
# The chain log line after which the motion teacher no longer needs :9999.
DEFAULT_IDLE_MARKER = "motion: train plain"
DEFAULT_CHAIN_LOG = (
    Path.home() / ".immich-memories-matrix" / "motion-describer" / "slice1" / "night-chain.log"
)


@dataclass(frozen=True)
class PublicRow:
    image_id: str
    local_path: Path
    content_sha256: str
    retrieved_at: str
    role: str
    author_key: str


def load_partition(path: Path) -> list[PublicRow]:
    rows: list[PublicRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        # One photographer's pictures look alike; keep them on one side of any split.
        author_key = hashlib.sha256(str(row["author"]).encode("utf-8")).hexdigest()[:16]
        rows.append(
            PublicRow(
                image_id=str(row["image_id"]),
                local_path=Path(row["local_path"]),
                content_sha256=str(row["content_sha256"]),
                retrieved_at=str(row["retrieved_at"]),
                role=str(row["role"]),
                author_key=f"author:{author_key}",
            )
        )
    if len({row.image_id for row in rows}) != len(rows):
        raise ValueError("partition image ids must be unique")
    return rows


# ---- stage: label -----------------------------------------------------------------


def _teacher_busy() -> bool:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["/usr/bin/pgrep", "-f", "motion_teacher_label.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def wait_for_teacher_idle(chain_log: Path, marker: str, *, poll_seconds: float = 60.0) -> None:
    """Block until the motion chain has passed its last :9999 stage."""
    while True:
        seen = chain_log.exists() and marker in chain_log.read_text(encoding="utf-8")
        if seen and not _teacher_busy():
            return
        state = "marker seen, labeler still running" if seen else f"waiting for '{marker}'"
        print(f"{time.strftime('%H:%M:%S')} teacher gate: {state}", flush=True)
        time.sleep(poll_seconds)


async def _label(rows: list[PublicRow], wal_path: Path, *, concurrency: int) -> dict[str, int]:
    endpoint = _resolve_local_endpoint()
    assets = [
        LabelAsset(
            asset_id=row.image_id,
            image_path=row.local_path,
            group_key=row.author_key,
            source_updated=row.retrieved_at,
            preview_sha256=row.content_sha256,
        )
        for row in rows
    ]
    totals = Counter()
    async with httpx.AsyncClient() as client:
        # Two passes: the second only re-asks rows the first banked as errors.
        for attempt in (1, 2):
            run = await label_assets_multihead(
                assets,
                endpoint=endpoint,
                client=client,
                wal_path=wal_path,
                concurrency=concurrency,
            )
            print(
                f"label pass {attempt}: requested={run.requested} labeled={run.labeled} "
                f"cached={run.cached} errors={run.errors} elapsed={run.elapsed_seconds:.0f}s",
                flush=True,
            )
            totals["labeled"] += run.labeled
            totals["errors"] = run.errors
            if run.errors == 0:
                break
    return dict(totals)


def stage_label(args: argparse.Namespace, rows: list[PublicRow]) -> None:
    if not args.no_wait:
        wait_for_teacher_idle(args.chain_log, args.idle_marker)
    wal_path = args.probe_dir / "public-labels.jsonl"
    totals = asyncio.run(_label(rows, wal_path, concurrency=args.concurrency))
    ok = sum(1 for row in _wal_rows(wal_path) if row.get("status") == "ok")
    print(f"label stage done: ok rows={ok} {totals}", flush=True)


# ---- stage: embed -----------------------------------------------------------------


def owner_encoder_spec(meta_path: Path) -> tuple[EncoderSpec, str]:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    spec = EncoderSpec(
        encoder_id=str(meta["encoder_id"]),
        weights_sha256=str(meta["weights_sha256"]),
        preprocess_version=str(meta["preprocess_version"]),
    )
    staging_key = str(meta["staging_encoder_key"])
    if spec.key != staging_key:
        raise ValueError("probe encoder spec does not reproduce the owner staging key")
    return spec, staging_key


def stage_embed(args: argparse.Namespace, rows: list[PublicRow]) -> None:
    spec, _ = owner_encoder_spec(args.embed_meta)
    if verify_onnx_artifact(args.onnx) != spec.weights_sha256:
        raise ValueError("ONNX weights differ from the owner embed run")
    session = create_onnx_session(args.onnx, "cpu")
    cache = EmbeddingCache(args.probe_dir / "embeddings.db")
    assets = [
        AssetInput(
            asset_id=row.image_id,
            image_path=row.local_path,
            source_updated=row.retrieved_at,
            preview_sha256=row.content_sha256,
        )
        for row in rows
    ]
    run = embed_assets(
        assets,
        spec=spec,
        cache=cache,
        session=session,
        batch_size=32,
        memory_guard=ensure_process_tree_memory,
    )
    print(
        f"embed stage done: requested={run.requested} computed={run.computed} "
        f"cached={run.cached} elapsed={run.elapsed_seconds:.0f}s",
        flush=True,
    )


# ---- stage: score -----------------------------------------------------------------


@dataclass(frozen=True)
class LabeledPacks:
    ids: list[str]
    packs: np.ndarray
    labels: np.ndarray
    groups: np.ndarray


def _owner_bundle_dir(head: HeadSpec) -> Path:
    return MATRIX_ROOT if head is LOCATION else MATRIX_ROOT / "heads" / head.name


def _owner_test_split(head: HeadSpec, cache: EmbeddingCache, staging_key: str) -> LabeledPacks:
    _, components = resolve_training_bundle(_owner_bundle_dir(head), head=head)
    manifest = components["training_manifest"].read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in manifest if line.strip()]
    test = [row for row in rows if row["split"] == "test"]
    ids = [str(row["asset_id"]) for row in test]
    snapshot = cache.staging_snapshot(ids, staging_key)
    missing = [asset_id for asset_id in ids if asset_id not in snapshot]
    if missing:
        raise ValueError(f"{len(missing)} owner test packs missing from the owner cache")
    return LabeledPacks(
        ids=ids,
        packs=np.stack([snapshot[asset_id][0] for asset_id in ids]),
        labels=np.asarray([str(row[head.name]) for row in test], dtype=str),
        groups=np.asarray([str(row["group_key"]) for row in test], dtype=str),
    )


def _public_packs(
    head: HeadSpec,
    rows: list[PublicRow],
    wal_rows: list[dict[str, Any]],
    cache: EmbeddingCache,
    staging_key: str,
    *,
    role: str | None,
) -> LabeledPacks:
    labels = {
        str(row["asset_id"]): str(row[head.name]) for row in wal_rows if row.get("status") == "ok"
    }
    chosen = [row for row in rows if row.image_id in labels and (role is None or row.role == role)]
    ids = [row.image_id for row in chosen]
    snapshot = cache.staging_snapshot(ids, staging_key)
    missing = [asset_id for asset_id in ids if asset_id not in snapshot]
    if missing:
        raise ValueError(f"{len(missing)} public packs missing; run the embed stage")
    return LabeledPacks(
        ids=ids,
        packs=np.stack([snapshot[asset_id][0] for asset_id in ids]),
        labels=np.asarray([labels[row.image_id] for row in chosen], dtype=str),
        groups=np.asarray([row.author_key for row in chosen], dtype=str),
    )


def _cell(
    head: HeadSpec,
    pca: PCAArtifact,
    candidates: dict[str, Any],
    data: LabeledPacks,
) -> dict[str, Any]:
    features = project_deployment_features(pca, data.packs)
    counts = Counter(data.labels.tolist())
    out: dict[str, Any] = {
        "n": len(data.ids),
        "majority_floor": max(counts.values()) / len(data.ids),
        "label_counts": dict(sorted(counts.items())),
    }
    for name, artifact in candidates.items():
        predicted = artifact.predict(features)
        recall = {
            label: float(np.mean(predicted[data.labels == label] == label))
            for label in head.classes
            if counts.get(label)
        }
        out[name] = {
            "accuracy": float(np.mean(predicted == data.labels)),
            "escape_rate": float(np.mean(predicted == head.escape_class)),
            "per_class_recall": recall,
            "balanced_accuracy": float(np.mean(list(recall.values()))),
        }
    return out


def _fit_public(
    head: HeadSpec, train: LabeledPacks, *, mlp_epochs: int
) -> tuple[PCAArtifact, dict[str, Any]]:
    pca = fit_pca(train.packs, components=min(256, len(train.ids) - 1))
    features = project_deployment_features(pca, train.packs)
    linear, _ = fit_linear_candidate(features, train.labels, train.groups)
    mlp = fit_mlp_candidate(features, train.labels, epochs=mlp_epochs)
    return pca, {"linear": linear, "mlp": mlp}


def score_head(
    head: HeadSpec,
    rows: list[PublicRow],
    wal_rows: list[dict[str, Any]],
    *,
    owner_cache: EmbeddingCache,
    public_cache: EmbeddingCache,
    staging_key: str,
    mlp_epochs: int,
) -> dict[str, Any]:
    _, components = resolve_training_bundle(_owner_bundle_dir(head), head=head)
    owner_pca = load_pca_artifact(components["pca_artifact"])
    owner_heads = {
        "linear": load_linear_artifact(components["linear_candidate"]),
        "mlp": load_mlp_artifact(components["mlp_candidate"]),
    }
    owner_test = _owner_test_split(head, owner_cache, staging_key)
    public_all = _public_packs(head, rows, wal_rows, public_cache, staging_key, role=None)
    public_train = _public_packs(head, rows, wal_rows, public_cache, staging_key, role="training")
    public_cert = _public_packs(
        head, rows, wal_rows, public_cache, staging_key, role="certification"
    )
    public_pca, public_heads = _fit_public(head, public_train, mlp_epochs=mlp_epochs)
    return {
        "owner_on_owner_test": _cell(head, owner_pca, owner_heads, owner_test),
        "owner_on_public_all": _cell(head, owner_pca, owner_heads, public_all),
        "owner_on_public_cert": _cell(head, owner_pca, owner_heads, public_cert),
        "public_on_public_cert": _cell(head, public_pca, public_heads, public_cert),
        "public_on_owner_test": _cell(head, public_pca, public_heads, owner_test),
        "public_train_n": len(public_train.ids),
    }


def _print_table(report: dict[str, Any]) -> None:
    cells = (
        ("owner_on_owner_test", "owner→owner test"),
        ("owner_on_public_cert", "owner→OI-400"),
        ("owner_on_public_all", "owner→OI-3000"),
        ("public_on_public_cert", "public→OI-400"),
        ("public_on_owner_test", "public→owner test"),
    )
    for head_name, result in report["heads"].items():
        print(f"\n== {head_name} (public train n={result['public_train_n']})")
        print(f"{'cell':22}{'n':>6}{'floor':>7}{'linear':>8}{'mlp':>8}{'bal-mlp':>9}{'esc-mlp':>9}")
        for key, label in cells:
            cell = result[key]
            print(
                f"{label:22}{cell['n']:>6}{cell['majority_floor']:>7.3f}"
                f"{cell['linear']['accuracy']:>8.3f}{cell['mlp']['accuracy']:>8.3f}"
                f"{cell['mlp']['balanced_accuracy']:>9.3f}{cell['mlp']['escape_rate']:>9.3f}"
            )


def stage_score(args: argparse.Namespace, rows: list[PublicRow]) -> None:
    _, staging_key = owner_encoder_spec(args.embed_meta)
    wal_rows = _wal_rows(args.probe_dir / "public-labels.jsonl")
    ok = sum(1 for row in wal_rows if row.get("status") == "ok")
    if ok < 1000:
        raise SystemExit(f"STOP: only {ok} public labels banked; run the label stage")
    owner_cache = EmbeddingCache(args.owner_cache)
    public_cache = EmbeddingCache(args.probe_dir / "embeddings.db")
    report: dict[str, Any] = {
        "schema_version": "triage-public-probe-v1",
        "public_label_rows_ok": ok,
        "mlp_epochs": args.mlp_epochs,
        "heads": {},
    }
    for head in ACTIVE_HEAD_SPECS:
        if args.heads and head.name not in args.heads:
            continue
        print(f"scoring {head.name}", flush=True)
        report["heads"][head.name] = score_head(
            head,
            rows,
            wal_rows,
            owner_cache=owner_cache,
            public_cache=public_cache,
            staging_key=staging_key,
            mlp_epochs=args.mlp_epochs,
        )
    out = args.probe_dir / "report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    _print_table(report)
    print(f"\nreport: {out}")


# ---- cli --------------------------------------------------------------------------


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stages", nargs="+", choices=("label", "embed", "score"))
    parser.add_argument("--partition", type=Path, default=DEFAULT_PARTITION)
    parser.add_argument("--probe-dir", type=Path, default=DEFAULT_PROBE_DIR)
    parser.add_argument("--onnx", type=Path, default=DEFAULT_ONNX)
    parser.add_argument("--embed-meta", type=Path, default=DEFAULT_EMBED_META)
    parser.add_argument("--owner-cache", type=Path, default=DEFAULT_OWNER_CACHE)
    parser.add_argument("--chain-log", type=Path, default=DEFAULT_CHAIN_LOG)
    parser.add_argument("--idle-marker", default=DEFAULT_IDLE_MARKER)
    parser.add_argument("--no-wait", action="store_true", help="skip the :9999 idle gate")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--mlp-epochs", type=int, default=100)
    parser.add_argument("--heads", nargs="*", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    args.probe_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    rows = load_partition(args.partition)
    print(f"partition rows={len(rows)} roles={dict(Counter(row.role for row in rows))}")
    stages = {"label": stage_label, "embed": stage_embed, "score": stage_score}
    for stage in args.stages:
        print(f"== {time.strftime('%H:%M:%S')} stage {stage}", flush=True)
        stages[stage](args, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
