"""A fact's identity is the artifact that produced it, never the machine it ran on.

The service must hand back the labels, versions and `encoder_key` the in-process
preparation path banks, or a library prepared against a GPU box and the same
library prepared on a laptop would occupy two sets of bank rows for one picture.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.triage.encoder import PATCH_GRID, TOKEN_DIM, DinoEncoder
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import HeadBundle
from immich_memories_inference.app import create_app
from immich_memories_inference.producers import HEADS, HeadsProducer
from immich_memories_inference.runtime import ProducerRuntime
from immich_memories_inference.settings import InferenceSettings

BUNDLED_HEADS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "immich_memories"
    / "triage"
    / "bundled_heads"
    / "public-6heads-v3.npz"
)
TOKENS = 1 + PATCH_GRID * PATCH_GRID


class PinnedGraph:
    """Stands in for the pinned DINOv2 export.

    # WHY: the 88 MB ONNX graph is fetched into the model cache at runtime and
    # is not in the repository, so it cannot be loaded in a unit test. What the
    # test needs from it is that both paths see the same tokens for the same
    # pixels — which a deterministic function of the batch gives exactly.
    """

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="pixel_values")]

    def run(self, _outputs: object, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        batch = np.ascontiguousarray(next(iter(feed.values())), dtype=np.float32)
        seed = int(hashlib.sha256(batch.tobytes()).hexdigest()[:8], 16)
        generator = np.random.RandomState(seed % (2**31 - 1))
        return [generator.normal(size=(len(batch), TOKENS, TOKEN_DIM)).astype(np.float32)]


def photograph(seed: int = 11) -> bytes:
    pixels = np.random.RandomState(seed).randint(0, 256, (480, 640, 3), dtype=np.uint8)
    buffer = BytesIO()
    Image.fromarray(pixels).save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def bundle() -> HeadBundle:
    return HeadBundle.load(BUNDLED_HEADS)


def banked_in_process(bundle: HeadBundle, encoder: DinoEncoder, image: bytes, store_path: Path):
    """The rows preparation writes to the bank for this picture, read back."""
    store = HeadFactStore(store_path)
    try:
        TriageEngine(encoder=encoder, bundle=bundle, store=store).run(
            ["asset"], lambda _asset_id: image
        )
    finally:
        store.close()
    with closing(sqlite3.connect(store_path)) as connection:
        rows = connection.execute(
            "SELECT head, version, label, confidence, encoder_key FROM head_facts"
            " WHERE asset_id = ?",
            ("asset",),
        ).fetchall()
    order = [head.name for head in bundle.heads]
    return sorted(rows, key=lambda row: order.index(row[0]))


def served(bundle: HeadBundle, encoder: DinoEncoder, image: bytes):
    runtime = ProducerRuntime({HEADS: lambda: HeadsProducer(encoder, bundle)})
    with TestClient(create_app(InferenceSettings(), runtime=runtime)) as client:
        response = client.post(
            "/facts",
            json={"image": base64.b64encode(image).decode(), "producers": [HEADS]},
        )
    response.raise_for_status()
    body = response.json()["producers"][HEADS]
    return [
        (fact["head"], fact["version"], fact["label"], fact["confidence"], body["encoder_key"])
        for fact in body["facts"]
    ]


def test_the_service_returns_the_rows_the_in_process_path_banks(tmp_path, bundle):
    image = photograph()
    encoder = DinoEncoder(session=PinnedGraph(), key=bundle.encoder_key)

    assert served(bundle, encoder, image) == banked_in_process(
        bundle, encoder, image, tmp_path / "triage.db"
    )


def test_every_head_in_the_bundle_answers_at_its_own_version(bundle):
    encoder = DinoEncoder(session=PinnedGraph(), key=bundle.encoder_key)

    facts = served(bundle, encoder, photograph())

    assert [(fact[0], fact[1]) for fact in facts] == [
        (head.name, head.version) for head in bundle.heads
    ]
    assert {fact[4] for fact in facts} == {bundle.encoder_key}


def test_two_pictures_are_not_answered_with_one_set_of_facts(bundle):
    encoder = DinoEncoder(session=PinnedGraph(), key=bundle.encoder_key)

    first = served(bundle, encoder, photograph(1))
    second = served(bundle, encoder, photograph(2))

    assert [fact[2:4] for fact in first] != [fact[2:4] for fact in second]
