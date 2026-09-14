"""The pixel producers, each answering with the row preparation would bank.

Every producer here runs the application's own code — the triage engine for the
heads, the detector module's own classes and its `decide` for the two detectors —
so a fact computed in this service cannot drift from one computed in process.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories.pinned_models import ENCODER, MARQO_ONNX
from immich_memories.triage.encoder import DinoEncoder
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import HeadBundle, HeadFact
from immich_memories_inference.seeding import seed

HEADS = "heads"
NSFW_MARQO = "nsfw_marqo"
DOC_DOCLING = "doc_docling"

# One request is one picture, and the engine keys its work by asset id.
_REQUEST_ASSET = "request"
ENCODER_HINT = "fetch it into the model cache or point IMMICH_MEMORIES_INFERENCE_ENCODER at it"


@dataclass(frozen=True)
class Fact:
    """One bank row without its asset: head, version, label, confidence."""

    head: str
    version: str
    label: str
    confidence: float


@dataclass(frozen=True)
class ProducerFacts:
    producer: str
    encoder_key: str
    facts: tuple[Fact, ...]


class Producer(Protocol):
    name: str

    @property
    def encoder_key(self) -> str: ...

    @property
    def versions(self) -> Mapping[str, str]: ...

    def decide(self, image: bytes) -> ProducerFacts: ...


class _CaptureStore:
    """A fact store that hands the facts back rather than banking them.

    The service is stateless; the client owns the bank. Implementing the store
    seam is what lets the heads run through the same TriageEngine preparation
    uses, instead of a second copy of the pooling, PCA and argmax.
    """

    def __init__(self) -> None:
        self.facts: list[HeadFact] = []
        self.encoder_key = ""

    def remember_facts(self, asset_id: str, facts: Sequence[HeadFact], *, encoder_key: str) -> None:
        self.facts = list(facts)
        self.encoder_key = encoder_key

    def facts_for(
        self, asset_ids: Sequence[str], *, head: str, version: str
    ) -> dict[str, HeadFact]:
        return {}


class HeadsProducer:
    """Encoder + head bundle, decided by the engine preparation runs."""

    name = HEADS

    def __init__(self, encoder: DinoEncoder, bundle: HeadBundle) -> None:
        # The engine refuses a bundle trained on another encoder. Building one
        # here makes that refusal happen at load rather than on a request; each
        # decide builds its own against a fresh capture store, so two requests
        # can never see each other's facts.
        TriageEngine(encoder=encoder, bundle=bundle, store=_CaptureStore())
        self._encoder = encoder
        self._bundle = bundle

    @property
    def encoder_key(self) -> str:
        return self._encoder.key

    @property
    def versions(self) -> Mapping[str, str]:
        return {head.name: head.version for head in self._bundle.heads}

    @property
    def session_providers(self) -> tuple[str, ...]:
        return running_providers(self._encoder.session)

    def decide(self, image: bytes) -> ProducerFacts:
        store = _CaptureStore()
        engine = TriageEngine(encoder=self._encoder, bundle=self._bundle, store=store)
        engine.run([_REQUEST_ASSET], lambda _asset_id: image, batch_size=1)
        return ProducerFacts(
            producer=self.name,
            encoder_key=store.encoder_key,
            facts=tuple(
                Fact(
                    head=fact.head,
                    version=fact.version,
                    label=fact.label,
                    confidence=fact.confidence,
                )
                for fact in store.facts
            ),
        )


def running_providers(session: object) -> tuple[str, ...]:
    """What ONNX Runtime actually took the graph, for a producer that holds a session.

    A stubbed model in a test holds none, and a producer that is not an ORT seat
    at all need not grow one: both answer with nothing rather than an error.
    """
    get_providers = getattr(session, "get_providers", None)
    return tuple(get_providers()) if get_providers else ()


class DetectorModel(Protocol):
    """The slice of the detector classes this service uses."""

    @property
    def classes(self) -> Sequence[str]: ...

    @property
    def encoder_key(self) -> str: ...

    @property
    def version(self) -> str: ...

    def batch(self, images: list[Image.Image]) -> np.ndarray: ...


class DetectorProducer:
    """One versioned detector, deciding with the detector module's own rule."""

    def __init__(self, name: str, detector: DetectorModel) -> None:
        self.name = name
        self._detector = detector

    @property
    def encoder_key(self) -> str:
        return self._detector.encoder_key

    @property
    def versions(self) -> Mapping[str, str]:
        return {self.name: self._detector.version}

    @property
    def session_providers(self) -> tuple[str, ...]:
        return running_providers(getattr(self._detector, "session", None))

    def decide(self, image: bytes) -> ProducerFacts:
        with Image.open(BytesIO(image)) as handle:
            probabilities = self._detector.batch([handle.convert("RGB")])[0]
        scores = dict(zip(self._detector.classes, map(float, probabilities), strict=True))
        # The worker refuses to bank a broken model's output; so does the wire.
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in scores.values()):
            raise ValueError(f"{self.name} returned invalid probabilities")
        label, confidence = detectors.decide(self.name, scores)
        return ProducerFacts(
            producer=self.name,
            encoder_key=self._detector.encoder_key,
            facts=(
                Fact(
                    head=self.name,
                    version=self._detector.version,
                    label=label,
                    confidence=confidence,
                ),
            ),
        )


def heads_loader(
    encoder_path: Path, bundle_path: Path, *, provider: str, allow_downloads: bool = False
) -> Callable[[], Producer]:
    def load() -> Producer:
        seed(ENCODER, encoder_path, allow_downloads=allow_downloads)
        if not encoder_path.is_file():
            raise FileNotFoundError(
                f"the pinned DINOv2 ONNX export is not at {encoder_path}; {ENCODER_HINT}"
            )
        return HeadsProducer(
            DinoEncoder.open(encoder_path, provider=provider), HeadBundle.load(bundle_path)
        )

    return load


def detector_loader(
    name: str, *, provider: str, allow_downloads: bool, cache_dir: str | None, marqo_onnx: Path
) -> Callable[[], Producer]:
    if name not in {NSFW_MARQO, DOC_DOCLING}:
        raise KeyError(name)

    def load() -> Producer:
        if name == NSFW_MARQO:
            seed(MARQO_ONNX, marqo_onnx, allow_downloads=allow_downloads)
            return DetectorProducer(name, detectors.Marqo(model_path=marqo_onnx, provider=provider))
        # Docling is a Hugging Face snapshot rather than a release asset, so the
        # hub does its own fetching under the same flag.
        return DetectorProducer(
            name,
            detectors.Docling(
                allow_downloads=allow_downloads, cache_dir=cache_dir, provider=provider
            ),
        )

    return load
