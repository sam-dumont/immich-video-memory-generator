"""Public context heads are required evidence: preparation stops rather than skipping them."""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS, prepare_heads
from immich_memories.triage.heads import HeadBundle, HeadWeights, PcaWeights

PACK_DIM = 6 * 384


def bundle_at(path, versions):
    HeadBundle(
        encoder_key="a" * 64,
        pca=PcaWeights(
            mean=np.zeros(PACK_DIM, dtype=np.float32),
            components=np.zeros((4, PACK_DIM), dtype=np.float32),
        ),
        heads=tuple(
            HeadWeights(
                name=name,
                version=version,
                classes=("no", "yes"),
                coef=np.zeros((2, 4), dtype=np.float32),
                intercept=np.zeros(2, dtype=np.float32),
            )
            for name, version in versions.items()
        ),
    ).save(path)
    return path


def prepare(tmp_path, versions, *, encoder=None):
    return prepare_heads(
        asset_ids=["one"],
        store_path=tmp_path / "triage.db",
        bundle_path=bundle_at(tmp_path / "heads.npz", versions),
        encoder_path=encoder if encoder is not None else tmp_path / "absent.onnx",
        head_versions=PUBLIC_HEAD_VERSIONS,
        preview_for=lambda _asset_id: b"",
        batch_size=8,
        check_cancelled=lambda: None,
        progress=lambda *_args: None,
    )


def test_a_bundle_missing_a_required_head_stops_preparation(tmp_path):
    incomplete = dict(PUBLIC_HEAD_VERSIONS)
    incomplete.pop("children")

    with pytest.raises(ValueError, match="required public head versions"):
        prepare(tmp_path, incomplete)


def test_a_bundle_at_another_head_version_stops_preparation(tmp_path):
    stale = dict(PUBLIC_HEAD_VERSIONS) | {"venue": "oi-v2"}

    with pytest.raises(ValueError, match="required public head versions"):
        prepare(tmp_path, stale)


def test_a_missing_encoder_stops_preparation_instead_of_silently_skipping_the_facts(tmp_path):
    with pytest.raises(FileNotFoundError, match="set triage.encoder"):
        prepare(tmp_path, dict(PUBLIC_HEAD_VERSIONS))
