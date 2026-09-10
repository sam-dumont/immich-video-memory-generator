"""The shipped triage package: bundle → encoder → engine, serving what training built.

The head-training scripts stay on the probe branch.

The head-training scripts stay on the probe branch.

The head-training script checks stay on the probe branch.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from immich_memories.triage.heads import HeadBundle, HeadFact, HeadWeights, PcaWeights


def _tiny_bundle() -> HeadBundle:
    # Pack dim 4 → 2 PCA dims; the head fires "outdoor" on the first component.
    pca = PcaWeights(
        mean=np.zeros(4, dtype=np.float32),
        components=np.asarray([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32),
    )
    location = HeadWeights(
        name="location",
        version="test-v1",
        classes=("indoor", "outdoor", "undetermined"),
        coef=np.asarray([[-4, 0], [4, 0], [0, 0]], dtype=np.float32),
        intercept=np.zeros(3, dtype=np.float32),
    )
    return HeadBundle(encoder_key="k" * 64, pca=pca, heads=(location,))


class TestHeadBundle:
    def test_round_trips_through_npz_and_decides_labels(self, tmp_path) -> None:
        path = tmp_path / "heads.npz"
        _tiny_bundle().save(path)

        bundle = HeadBundle.load(path)
        packs = np.asarray([[2, 0, 0, 0], [-2, 0, 0, 0], [0, 0, 0, 0]], dtype=np.float32)
        facts = bundle.decide(packs)

        assert bundle.encoder_key == "k" * 64
        assert [fact.label for fact in facts["location"]] == ["outdoor", "indoor", "indoor"]
        assert facts["location"][0].confidence == pytest.approx(0.9997, abs=1e-3)
        assert facts["location"][0].version == "test-v1"


class TestHeadFactStore:
    def test_facts_are_keyed_by_asset_head_and_version(self, tmp_path) -> None:
        from immich_memories.cache.embedding_cache import HeadFactStore

        store = HeadFactStore(tmp_path / "triage.db")
        store.remember_facts(
            "asset-1", [HeadFact("location", "outdoor", 0.98, "v1")], encoder_key="k" * 64
        )
        store.remember_facts(
            "asset-2", [HeadFact("location", "indoor", 0.71, "v1")], encoder_key="k" * 64
        )

        banked = store.facts_for(["asset-1", "asset-2", "asset-3"], head="location", version="v1")

        assert banked["asset-1"].label == "outdoor"
        assert banked["asset-2"].confidence == pytest.approx(0.71)
        assert "asset-3" not in banked
        assert store.facts_for(["asset-1"], head="location", version="v2") == {}


class TestOpenTriage:
    """Triage never blocks a run: no engine when it is off or its weights are absent."""

    def test_fails_open_when_disabled_or_encoder_missing(self, tmp_path, caplog) -> None:
        from immich_memories.config_models_triage import TriageConfig
        from immich_memories.triage.runtime import open_triage

        off = TriageConfig(enabled=False, encoder=str(tmp_path / "absent.onnx"))
        assert open_triage(off, store_path=tmp_path / "triage.db") is None

        on = TriageConfig(enabled=True, encoder=str(tmp_path / "absent.onnx"))
        with caplog.at_level("WARNING"):
            assert open_triage(on, store_path=tmp_path / "triage.db") is None
        assert "absent.onnx" in caplog.text
        assert not (tmp_path / "triage.db").exists()

    def test_refuses_a_foreign_bundle_without_raising(self, tmp_path, caplog) -> None:
        from immich_memories.config_models_triage import TriageConfig
        from immich_memories.triage.runtime import open_triage

        encoder = TriageConfig().encoder_path
        if not encoder.is_file():
            pytest.skip("pinned DINOv2 export not on this machine")
        foreign = tmp_path / "foreign.npz"
        _tiny_bundle().save(foreign)  # encoder_key "kkk…" ≠ the real encoder's
        config = TriageConfig(enabled=True, bundle=str(foreign))

        with caplog.at_level("WARNING"):
            assert open_triage(config, store_path=tmp_path / "triage.db") is None
        assert "another encoder" in caplog.text

    def test_opens_the_shipped_public_bundle_on_the_pinned_encoder(self, tmp_path) -> None:
        from immich_memories.config_models_triage import TriageConfig
        from immich_memories.triage.runtime import open_triage

        config = TriageConfig(enabled=True)
        if not config.encoder_path.is_file():
            pytest.skip("pinned DINOv2 export not on this machine")

        engine = open_triage(config, store_path=tmp_path / "triage.db")

        assert engine is not None
        run = engine.run(["bright", "dark"], {"bright": _jpeg(250), "dark": _jpeg(5)}.get)
        assert (run.decided, run.missing) == (2, 0)


def _jpeg(shade: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _StubEncoder:
    """Stands in for the ORT session: a pack whose first dim is the mean pixel value.

    WHY: the real encoder is an 88 MB ONNX graph; the engine's contract (batching,
    banking, skipping) does not depend on what the features mean.
    """

    key = "k" * 64

    def __init__(self) -> None:
        self.batches: list[int] = []

    def embed(self, batch: np.ndarray) -> np.ndarray:
        self.batches.append(len(batch))
        packs = np.zeros((len(batch), 4), dtype=np.float32)
        packs[:, 0] = batch.mean(axis=(1, 2, 3))
        return packs


class TestTriageEngine:
    def test_decides_unbanked_assets_and_skips_the_rest(self, tmp_path) -> None:
        from immich_memories.cache.embedding_cache import HeadFactStore
        from immich_memories.triage.engine import TriageEngine

        store = HeadFactStore(tmp_path / "triage.db")
        store.remember_facts(
            "banked", [HeadFact("location", "indoor", 0.9, "test-v1")], encoder_key="k" * 64
        )
        previews = {"bright": _jpeg(250), "dark": _jpeg(5), "banked": _jpeg(128)}
        encoder = _StubEncoder()
        engine = TriageEngine(encoder=encoder, bundle=_tiny_bundle(), store=store)

        run = engine.run(
            ["bright", "dark", "banked", "missing"], lambda asset_id: previews.get(asset_id)
        )

        facts = store.facts_for(["bright", "dark", "banked"], head="location", version="test-v1")
        assert facts["bright"].label == "outdoor"
        assert facts["dark"].label == "indoor"
        assert facts["banked"].confidence == pytest.approx(0.9)
        assert (run.requested, run.decided, run.banked, run.missing) == (4, 2, 1, 1)
        assert encoder.batches == [2]

    def test_refuses_a_bundle_trained_on_another_encoder(self, tmp_path) -> None:
        from immich_memories.cache.embedding_cache import HeadFactStore
        from immich_memories.triage.engine import TriageEngine

        bundle = HeadBundle(
            encoder_key="x" * 64, pca=_tiny_bundle().pca, heads=_tiny_bundle().heads
        )
        with pytest.raises(ValueError, match="encoder"):
            TriageEngine(
                encoder=_StubEncoder(), bundle=bundle, store=HeadFactStore(tmp_path / "t.db")
            )
