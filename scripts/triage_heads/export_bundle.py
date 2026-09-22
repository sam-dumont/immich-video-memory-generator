#!/usr/bin/env python3
"""Export a shippable head bundle from training artifacts.

    uv run python scripts/triage_heads/export_bundle.py public --heads location \
        --out ~/.immich-memories-matrix/triage-heads/bundles/public-location-v1.npz

``public`` fits PCA + linear heads on the public corpus exactly as public_probe
does (author-grouped split, public PCA), so nothing from the owner's library is
in the file. ``owner`` packages the promoted owner training run instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.triage_heads.head_specs import ACTIVE_HEAD_SPECS, HeadSpec
from scripts.triage_heads.public_probe import (
    DEFAULT_EMBED_META,
    DEFAULT_PARTITION,
    DEFAULT_PROBE_DIR,
    _fit_public,
    _public_packs,
    _wal_rows,
    load_partition,
    owner_encoder_spec,
)
from scripts.triage_heads.train import LinearArtifact, PCAArtifact

from immich_memories.triage.heads import HeadBundle, HeadWeights, PcaWeights


def build_bundle(
    *, encoder_key: str, pca: PCAArtifact, heads: dict[str, tuple[LinearArtifact, str]]
) -> HeadBundle:
    return HeadBundle(
        encoder_key=encoder_key,
        pca=PcaWeights(mean=pca.mean, components=pca.components),
        heads=tuple(
            HeadWeights(
                name=name,
                version=version,
                classes=tuple(artifact.classes),
                coef=artifact.coef,
                intercept=artifact.intercept,
            )
            for name, (artifact, version) in heads.items()
        ),
    )


def _fit_public_heads(
    specs: list[HeadSpec], *, version: str
) -> tuple[PCAArtifact, dict[str, tuple[LinearArtifact, str]], dict[str, dict[str, int]]]:
    from scripts.triage_heads.embed import EmbeddingCache

    _, staging_key = owner_encoder_spec(DEFAULT_EMBED_META)
    rows = load_partition(DEFAULT_PARTITION)
    wal = _wal_rows(DEFAULT_PROBE_DIR / "public-labels.jsonl")
    cache = EmbeddingCache(DEFAULT_PROBE_DIR / "embeddings.db")
    # One PCA for every head: fit on the first head's training packs (same
    # public images for all heads, so the projection is identical either way).
    pca: PCAArtifact | None = None
    heads: dict[str, tuple[LinearArtifact, str]] = {}
    counts: dict[str, dict[str, int]] = {}
    for spec in specs:
        train = _public_packs(spec, rows, wal, cache, staging_key, role="training")
        fitted_pca, candidates = _fit_public(spec, train, mlp_epochs=1)
        pca = pca or fitted_pca
        heads[spec.name] = (candidates["linear"], version)
        counts[spec.name] = dict(sorted(Counter(train.labels.tolist()).items()))
    assert pca is not None
    return pca, heads, counts


def _owner_training_packs(spec: HeadSpec, cache, staging_key: str):
    """The promoted owner run's non-test rows, read from the owner embedding cache."""
    import numpy as np
    from scripts.triage_heads.public_probe import LabeledPacks, _owner_bundle_dir
    from scripts.triage_heads.train import resolve_training_bundle

    _, components = resolve_training_bundle(_owner_bundle_dir(spec), head=spec)
    manifest = components["training_manifest"].read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in manifest if line.strip()]
    train = [row for row in rows if row["split"] != "test"]
    ids = [str(row["asset_id"]) for row in train]
    snapshot = cache.staging_snapshot(ids, staging_key)
    kept = [row for row in train if str(row["asset_id"]) in snapshot]
    return LabeledPacks(
        ids=[str(row["asset_id"]) for row in kept],
        packs=np.stack([snapshot[str(row["asset_id"])][0] for row in kept]),
        labels=np.asarray([str(row[spec.name]) for row in kept], dtype=str),
        groups=np.asarray([str(row["group_key"]) for row in kept], dtype=str),
    )


def _fit_owner_heads(
    specs: list[HeadSpec], *, version: str
) -> tuple[PCAArtifact, dict[str, tuple[LinearArtifact, str]], dict[str, dict[str, int]]]:
    from scripts.triage_heads.embed import EmbeddingCache
    from scripts.triage_heads.public_probe import DEFAULT_OWNER_CACHE

    _, staging_key = owner_encoder_spec(DEFAULT_EMBED_META)
    cache = EmbeddingCache(DEFAULT_OWNER_CACHE)
    pca: PCAArtifact | None = None
    heads: dict[str, tuple[LinearArtifact, str]] = {}
    counts: dict[str, dict[str, int]] = {}
    for spec in specs:
        train = _owner_training_packs(spec, cache, staging_key)
        fitted_pca, candidates = _fit_public(spec, train, mlp_epochs=1)
        # The owner heads were promoted with one PCA each; a bundle carries one
        # projection, so every head is refit on the first head's projection.
        pca = pca or fitted_pca
        if pca is not fitted_pca:
            from scripts.triage_heads.train import fit_linear_candidate, project_deployment_features

            features = project_deployment_features(pca, train.packs)
            linear, _ = fit_linear_candidate(features, train.labels, train.groups)
            candidates = {"linear": linear}
        heads[spec.name] = (candidates["linear"], version)
        counts[spec.name] = dict(sorted(Counter(train.labels.tolist()).items()))
    assert pca is not None
    return pca, heads, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", choices=("public", "owner"))
    parser.add_argument("--heads", nargs="+", default=["location"])
    parser.add_argument("--version", default="public-v1")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    specs = [spec for spec in ACTIVE_HEAD_SPECS if spec.name in args.heads]
    if len(specs) != len(args.heads):
        parser.error(f"unknown head in {args.heads}; active: {[s.name for s in ACTIVE_HEAD_SPECS]}")
    _, staging_key = owner_encoder_spec(DEFAULT_EMBED_META)
    fit = _fit_owner_heads if args.source == "owner" else _fit_public_heads
    pca, heads, counts = fit(specs, version=args.version)
    bundle = build_bundle(encoder_key=staging_key, pca=pca, heads=heads)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    bundle.save(args.out)
    print(json.dumps({"out": str(args.out), "encoder_key": staging_key, "train_counts": counts}))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
