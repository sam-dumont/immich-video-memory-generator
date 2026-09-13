"""What the client banks from the real service is the row the local producers would bank.

The service runs the application's own producers, so the comparison here holds the
model outputs fixed (stub encoders, stub detector probabilities) and checks that the
whole path from ``POST /facts`` to the SQLite row lands on the same head, version,
label, confidence and encoder key as the in-process banking code does for those outputs.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories.analysis.editorial_preparation_detectors import _INSERT_FACT, _decided_rows
from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS
from immich_memories.analysis.editorial_preparation_remote import prepare_remote_facts
from immich_memories.analysis.remote_facts import RemoteFactsClient
from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.config_models_editorial import EditorialConfig
from immich_memories.config_models_inference import InferenceConfig
from immich_memories.triage.heads import HeadFact
from immich_memories_inference.app import create_app
from immich_memories_inference.producers import (
    DOC_DOCLING,
    HEADS,
    NSFW_MARQO,
    DetectorProducer,
    Fact,
    ProducerFacts,
)
from immich_memories_inference.runtime import ProducerRuntime
from immich_memories_inference.settings import InferenceSettings

ENCODER_KEY = "b" * 64
ASSETS = ("aa1", "bb2")


def picture() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(buffer, "JPEG")
    return buffer.getvalue()


class FixedHeads:
    """# WHY: stands in for the DINOv2 encoder and the head bundle (88 MB of weights).
    The rule under test is the banking path, not the classifier."""

    name = HEADS

    @property
    def encoder_key(self) -> str:
        return ENCODER_KEY

    @property
    def versions(self) -> dict[str, str]:
        return dict(PUBLIC_HEAD_VERSIONS)

    def decide(self, image: bytes) -> ProducerFacts:
        return ProducerFacts(
            producer=self.name,
            encoder_key=self.encoder_key,
            facts=tuple(
                Fact(head=head, version=version, label="other", confidence=0.61)
                for head, version in PUBLIC_HEAD_VERSIONS.items()
            ),
        )


class StubMarqo:
    encoder_key = detectors.MARQO_ONNX_ID
    version = detectors.MARQO_VERSION
    head = NSFW_MARQO
    classes = detectors.MARQO_CLASSES

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        return np.array([[0.83, 0.17]] * len(images), dtype=np.float32)


class StubDocling:
    encoder_key = detectors.DOCLING_REPO
    version = detectors.DOCLING_VERSION
    head = DOC_DOCLING
    classes = detectors.DOCLING_LABELS

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        scores = np.full(len(detectors.DOCLING_LABELS), 0.01, dtype=np.float32)
        scores[-1] = 1.0 - 0.01 * (len(detectors.DOCLING_LABELS) - 1)
        return np.array([scores] * len(images), dtype=np.float32)


def rows(path: Path) -> set[tuple]:
    with closing(sqlite3.connect(path)) as connection:
        return set(
            connection.execute(
                "SELECT asset_id, head, version, label, confidence, encoder_key FROM head_facts"
            ).fetchall()
        )


def bank_locally(path: Path) -> None:
    """The in-process path: the engine's store call for heads, the worker's rows for detectors."""
    with closing(HeadFactStore(path)) as store:
        for asset_id in ASSETS:
            store.remember_facts(
                asset_id,
                [
                    HeadFact(head=head, label="other", confidence=0.61, version=version)
                    for head, version in PUBLIC_HEAD_VERSIONS.items()
                ],
                encoder_key=ENCODER_KEY,
            )
    with closing(sqlite3.connect(path)) as connection:
        for detector in (StubMarqo(), StubDocling()):
            probabilities = detector.batch([Image.open(BytesIO(picture()))] * len(ASSETS))
            connection.executemany(_INSERT_FACT, _decided_rows(detector, ASSETS, probabilities))
        connection.commit()


def test_the_client_banks_exactly_what_the_local_producers_would(tmp_path: Path) -> None:
    runtime = ProducerRuntime(
        {
            HEADS: FixedHeads,
            NSFW_MARQO: lambda: DetectorProducer(NSFW_MARQO, StubMarqo()),
            DOC_DOCLING: lambda: DetectorProducer(DOC_DOCLING, StubDocling()),
        }
    )
    app = create_app(InferenceSettings(cache_dir=tmp_path), runtime=runtime)
    config = InferenceConfig(facts_base_url="http://inference.test:8092")
    remote_store = tmp_path / "remote.sqlite"
    local_store = tmp_path / "local.sqlite"
    versions = EditorialConfig().head_versions

    with (
        TestClient(app, base_url=config.facts_base_url) as http,
        RemoteFactsClient(config, client=http) as client,
    ):
        prepare_remote_facts(
            pending={asset_id: dict(versions) for asset_id in ASSETS},
            store_path=remote_store,
            client=client,
            preview_for=lambda _asset_id: picture(),
            check_cancelled=lambda: None,
            progress=lambda *_: None,
            on_asset=lambda _asset_id: None,
        )
    bank_locally(local_store)

    banked = rows(remote_store)
    assert banked == rows(local_store)
    assert {(head, version) for _, head, version, *_ in banked} == set(versions.items())
    assert {label for _, head, _, label, *_ in banked if head == NSFW_MARQO} == {"yes"}
