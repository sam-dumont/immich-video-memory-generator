"""Head facts are decided once per asset, and only ever on the encoder they were trained on."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.triage.encoder import (
    DINOV2_SMALL_ID,
    PACK_DIM,
    PATCH_GRID,
    TOKEN_DIM,
    DinoEncoder,
    encoder_key,
    pool_token_pack,
)
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import (
    BUNDLE_SCHEMA,
    HeadBundle,
    HeadFact,
    HeadWeights,
    PcaWeights,
)
from immich_memories.triage.preprocess import CROP_SIZE, preprocess_image_bytes

PACK_KEY = encoder_key(DINOV2_SMALL_ID, "a" * 64)
FEATURES = 8


def jpeg(size=(320, 240)) -> bytes:
    output = BytesIO()
    pixels = np.random.RandomState(7).randint(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(pixels).save(output, "JPEG")
    return output.getvalue()


def bundle(*, key=PACK_KEY, classes=("no", "yes")) -> HeadBundle:
    generator = np.random.RandomState(3)
    return HeadBundle(
        encoder_key=key,
        pca=PcaWeights(
            mean=np.zeros(PACK_DIM, dtype=np.float32),
            components=generator.normal(size=(FEATURES, PACK_DIM)).astype(np.float32),
        ),
        heads=(
            HeadWeights(
                name="people",
                version="public-v1",
                classes=classes,
                coef=np.eye(len(classes), FEATURES, dtype=np.float32),
                intercept=np.zeros(len(classes), dtype=np.float32),
            ),
        ),
    )


class Encoder:
    """Stands in for the ONNX graph: a deterministic pack per image."""

    def __init__(self, key=PACK_KEY):
        self.key = key
        self.batches: list[int] = []

    def embed(self, batch: np.ndarray) -> np.ndarray:
        self.batches.append(len(batch))
        return np.stack([np.full(PACK_DIM, float(row + 1)) for row in range(len(batch))]).astype(
            np.float32
        )


def test_a_preview_becomes_the_exact_tensor_the_pinned_transform_expects():
    landscape = preprocess_image_bytes(jpeg((320, 240)))
    portrait = preprocess_image_bytes(jpeg((240, 320)))

    assert landscape.shape == portrait.shape == (3, CROP_SIZE, CROP_SIZE)
    assert landscape.dtype == np.float32
    # ImageNet normalisation, so the pixel range straddles zero rather than [0, 1].
    assert landscape.min() < 0 < landscape.max()


def test_tokens_pool_into_the_layout_the_heads_were_trained_on():
    tokens = np.zeros((2, 1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM), dtype=np.float32)
    tokens[:, 0, :] = 5.0
    patches = tokens[:, 1:, :].reshape(2, PATCH_GRID, PATCH_GRID, TOKEN_DIM)
    patches[:, : PATCH_GRID // 2, : PATCH_GRID // 2, :] = 4.0

    packs = pool_token_pack(tokens)

    assert packs.shape == (2, PACK_DIM)
    assert packs[0, 0] == 5.0
    assert packs[0, TOKEN_DIM] == pytest.approx(1.0)
    assert packs[0, 2 * TOKEN_DIM] == pytest.approx(4.0)
    assert packs[0, 3 * TOKEN_DIM] == pytest.approx(0.0)


def test_tokens_of_another_shape_are_refused_rather_than_reshaped():
    with pytest.raises(ValueError, match="expected"):
        pool_token_pack(np.zeros((2, 10, TOKEN_DIM), dtype=np.float32))


def test_the_encoder_key_changes_with_every_thing_it_binds():
    original = encoder_key(DINOV2_SMALL_ID, "a" * 64)

    assert encoder_key("another/model", "a" * 64) != original
    assert encoder_key(DINOV2_SMALL_ID, "b" * 64) != original
    assert encoder_key(DINOV2_SMALL_ID, "a" * 64, preprocess_version="next") != original
    assert encoder_key(DINOV2_SMALL_ID, "a" * 64, layout_version="next") != original


def test_pixels_reach_the_graph_and_come_back_pooled():
    tokens = np.ones((2, 1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM), dtype=np.float32)
    seen: list[str] = []

    class Session:
        """Stands in for the ONNX InferenceSession; the graph itself is the boundary."""

        def get_inputs(self):
            return [SimpleNamespace(name="pixel_values")]

        def run(self, _output_names, input_feed):
            seen.extend(input_feed)
            return [np.zeros((2, 3)), tokens]

    packs = DinoEncoder(session=Session(), key=PACK_KEY).embed(np.zeros((2, 3, 224, 224)))

    assert seen == ["pixel_values"]
    assert packs.shape == (2, PACK_DIM)


def test_a_graph_that_answers_with_other_shapes_is_refused():
    class Session:
        """Stands in for an ONNX export that is not the pinned DINOv2 graph."""

        def get_inputs(self):
            return [SimpleNamespace(name="pixel_values")]

        def run(self, _output_names, _input_feed):
            return [np.zeros((2, 3))]

    with pytest.raises(ValueError, match="did not return"):
        DinoEncoder(session=Session(), key=PACK_KEY).embed(np.zeros((2, 3, 224, 224)))


def test_any_export_but_the_pinned_one_is_refused_before_a_session_is_built(tmp_path):
    impostor = tmp_path / "dinov2.onnx"
    impostor.write_bytes(b"not the pinned graph")

    with pytest.raises(RuntimeError, match="is not the pinned"):
        DinoEncoder.open(impostor)


def test_a_bundle_survives_a_round_trip_through_its_own_file(tmp_path):
    path = tmp_path / "heads.npz"
    original = bundle()
    original.save(path)

    loaded = HeadBundle.load(path)

    assert loaded.encoder_key == original.encoder_key
    assert [head.name for head in loaded.heads] == ["people"]
    assert loaded.heads[0].classes == ("no", "yes")
    packs = np.full((1, PACK_DIM), 2.0, dtype=np.float32)
    assert loaded.decide(packs)["people"] == original.decide(packs)["people"]


def test_a_file_that_is_not_a_bundle_is_refused_by_name(tmp_path):
    path = tmp_path / "other.npz"
    np.savez_compressed(path, meta=np.asarray('{"schema": "something-else"}'))

    with pytest.raises(ValueError, match=BUNDLE_SCHEMA):
        HeadBundle.load(path)


def test_packs_of_the_wrong_width_never_reach_a_head():
    with pytest.raises(ValueError, match="PCA expected"):
        bundle().decide(np.zeros((1, 4), dtype=np.float32))


def test_each_head_answers_with_its_own_label_and_a_real_probability():
    facts = bundle(classes=("no", "yes", "unclear")).decide(
        np.full((2, PACK_DIM), 2.0, dtype=np.float32)
    )

    assert set(facts) == {"people"}
    assert [fact.head for fact in facts["people"]] == ["people", "people"]
    assert all(fact.label in {"no", "yes", "unclear"} for fact in facts["people"])
    assert all(0 < fact.confidence <= 1 for fact in facts["people"])
    assert all(fact.version == "public-v1" for fact in facts["people"])


def test_a_bundle_never_runs_on_features_it_was_not_trained_on(tmp_path):
    store = HeadFactStore(tmp_path / "triage.db")

    with pytest.raises(ValueError, match="another encoder"):
        TriageEngine(encoder=Encoder(), bundle=bundle(key="c" * 64), store=store)

    store.close()


def test_every_asset_with_a_preview_is_decided_once_and_banked(tmp_path):
    store = HeadFactStore(tmp_path / "triage.db")
    encoder = Encoder()
    engine = TriageEngine(encoder=encoder, bundle=bundle(), store=store)
    previews = {"one": jpeg(), "two": jpeg()}

    first = engine.run(["one", "two"], previews.get)

    assert (first.requested, first.decided, first.banked, first.missing) == (2, 2, 0, 0)
    assert first.ms_per_decided > 0
    assert set(store.facts_for(["one", "two"], head="people", version="public-v1")) == {
        "one",
        "two",
    }

    second = engine.run(["one", "two"], previews.get)

    assert (second.decided, second.banked) == (0, 2)
    assert encoder.batches == [2]
    store.close()


def test_an_asset_without_a_preview_is_counted_rather_than_decided(tmp_path):
    store = HeadFactStore(tmp_path / "triage.db")
    engine = TriageEngine(encoder=Encoder(), bundle=bundle(), store=store)

    run = engine.run(["seen", "no-preview"], {"seen": jpeg()}.get)

    assert (run.decided, run.missing, run.banked) == (1, 1, 0)
    assert run.ms_per_decided > 0
    assert set(store.facts_for(["seen", "no-preview"], head="people", version="public-v1")) == {
        "seen"
    }
    store.close()


def test_a_run_with_nothing_to_decide_reports_no_rate(tmp_path):
    store = HeadFactStore(tmp_path / "triage.db")
    engine = TriageEngine(encoder=Encoder(), bundle=bundle(), store=store)

    run = engine.run([], {}.get)

    assert (run.requested, run.decided, run.ms_per_decided) == (0, 0, 0.0)
    store.close()


def test_previews_are_encoded_in_bounded_batches(tmp_path):
    store = HeadFactStore(tmp_path / "triage.db")
    encoder = Encoder()
    engine = TriageEngine(encoder=encoder, bundle=bundle(), store=store)
    previews = {f"asset-{n}": jpeg() for n in range(5)}

    run = engine.run(sorted(previews), previews.get, batch_size=2)

    assert run.decided == 5
    assert encoder.batches == [2, 2, 1]
    store.close()


def test_a_new_head_version_reopens_an_already_banked_asset(tmp_path):
    store = HeadFactStore(tmp_path / "triage.db")
    store.remember_facts("one", [HeadFact("people", "yes", 0.9, "public-v0")], encoder_key=PACK_KEY)
    encoder = Encoder()
    engine = TriageEngine(encoder=encoder, bundle=bundle(), store=store)

    run = engine.run(["one"], {"one": jpeg()}.get)

    assert (run.decided, run.banked) == (1, 0)
    assert store.facts_for(["one"], head="people", version="public-v0")["one"].label == "yes"
    store.close()
