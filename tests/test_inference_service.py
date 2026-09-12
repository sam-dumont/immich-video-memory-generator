"""The service answers, says what it is holding, and drops weights without dying."""

from __future__ import annotations

import base64
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories_inference import __main__ as entrypoint
from immich_memories_inference.app import create_app, default_loaders, would_use_provider
from immich_memories_inference.producers import (
    DOC_DOCLING,
    HEADS,
    NSFW_MARQO,
    DetectorProducer,
    Fact,
    ProducerFacts,
)
from immich_memories_inference.runtime import (
    ProducerRuntime,
    ProducerUnavailable,
    UnknownProducer,
)
from immich_memories_inference.settings import InferenceSettings


def photograph() -> bytes:
    pixels = np.random.RandomState(5).randint(0, 256, (240, 320, 3), dtype=np.uint8)
    buffer = BytesIO()
    Image.fromarray(pixels).save(buffer, "JPEG")
    return buffer.getvalue()


class CountingProducer:
    """A producer whose weights are a counter, so loads and unloads are visible."""

    name = HEADS

    def __init__(self, loads: list[int]) -> None:
        loads.append(1)
        self.generation = len(loads)

    @property
    def encoder_key(self) -> str:
        return "a" * 64

    @property
    def versions(self) -> dict[str, str]:
        return {"people": "public-v1"}

    def decide(self, image: bytes) -> ProducerFacts:
        return ProducerFacts(
            producer=self.name,
            encoder_key=self.encoder_key,
            facts=(Fact(head="people", version="public-v1", label="yes", confidence=0.75),),
        )


class StubDetector:
    """# WHY: stands in for the Marqo/Docling weights, which are ~37 MB of
    downloads and a torch import. The rule under test is the decision, which
    the detector module owns and this service must not restate."""

    encoder_key = detectors.MARQO_REPO
    classes = ("NSFW", "SFW")

    def __init__(self, nsfw: float) -> None:
        self._nsfw = nsfw

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        return np.array([[self._nsfw, 1.0 - self._nsfw]] * len(images), dtype=np.float32)


def service(runtime: ProducerRuntime, **settings: object) -> TestClient:
    return TestClient(create_app(InferenceSettings(**settings), runtime=runtime))


def test_ping_answers_without_loading_anything():
    loads: list[int] = []
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer(loads)})

    with service(runtime) as client:
        assert client.get("/ping").json() == "pong"

    assert loads == []


def test_health_names_the_provider_a_session_would_open_on():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])})

    with service(runtime, provider="cpu") as client:
        body = client.get("/health").json()

    assert body["provider"] == "CPUExecutionProvider"
    assert body["producers"][HEADS] == {"loaded": False, "versions": None, "encoder_key": None}
    assert body["encoder_key"] is None


def test_health_reports_what_is_loaded_once_a_picture_has_been_decided():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])})

    with service(runtime) as client:
        client.post("/facts", json={"image": base64.b64encode(photograph()).decode()})
        body = client.get("/health").json()

    assert body["producers"][HEADS]["loaded"] is True
    assert body["producers"][HEADS]["versions"] == {"people": "public-v1"}
    assert body["encoder_key"] == "a" * 64


def test_facts_asks_only_the_producers_the_client_named():
    runtime = ProducerRuntime(
        {
            HEADS: lambda: CountingProducer([]),
            NSFW_MARQO: lambda: DetectorProducer(NSFW_MARQO, StubDetector(0.9)),
        }
    )

    with service(runtime) as client:
        body = client.post(
            "/facts",
            json={"image": base64.b64encode(photograph()).decode(), "producers": [NSFW_MARQO]},
        ).json()

    assert list(body["producers"]) == [NSFW_MARQO]


def test_a_detector_answers_at_the_version_and_key_the_bank_stores():
    runtime = ProducerRuntime({NSFW_MARQO: lambda: DetectorProducer(NSFW_MARQO, StubDetector(0.9))})

    with service(runtime) as client:
        body = client.post("/facts", json={"image": base64.b64encode(photograph()).decode()}).json()

    assert body["producers"][NSFW_MARQO] == {
        "encoder_key": detectors.MARQO_REPO,
        "facts": [
            {
                "head": NSFW_MARQO,
                "version": detectors.VERSION,
                "label": "yes",
                "confidence": 0.9,
            }
        ],
    }


def test_a_detector_below_its_threshold_says_no():
    runtime = ProducerRuntime({NSFW_MARQO: lambda: DetectorProducer(NSFW_MARQO, StubDetector(0.2))})

    with service(runtime) as client:
        body = client.post("/facts", json={"image": base64.b64encode(photograph()).decode()}).json()

    assert body["producers"][NSFW_MARQO]["facts"][0]["label"] == "no"


def test_a_producer_this_service_does_not_serve_is_refused_by_name():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])})

    with service(runtime) as client:
        response = client.post(
            "/facts",
            json={"image": base64.b64encode(photograph()).decode(), "producers": ["captions"]},
        )

    assert response.status_code == 400
    assert HEADS in response.json()["detail"]


def test_an_absent_encoder_is_unavailable_rather_than_an_error(tmp_path):
    settings = InferenceSettings(cache_dir=tmp_path)
    runtime = ProducerRuntime({HEADS: default_loaders(settings)[HEADS]})

    with TestClient(create_app(settings, runtime=runtime)) as client:
        response = client.post(
            "/facts",
            json={"image": base64.b64encode(photograph()).decode(), "producers": [HEADS]},
        )

    assert response.status_code == 503
    assert str(settings.encoder_path) in response.json()["detail"]


def test_something_that_is_not_a_picture_is_refused():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])})

    with service(runtime) as client:
        assert client.post("/facts", json={"image": "not base64!"}).status_code == 400
        assert client.post("/facts", json={"image": ""}).status_code == 400


def test_an_image_over_the_ceiling_is_refused_before_it_is_decoded():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])})

    with service(runtime, max_image_bytes=1024) as client:
        oversized = base64.b64encode(b"\xff" * 2048).decode()
        assert client.post("/facts", json={"image": oversized}).status_code == 413


def test_an_idle_producer_is_unloaded_and_the_next_request_reloads_it():
    loads: list[int] = []
    clock = iter(range(0, 10_000, 100))
    runtime = ProducerRuntime(
        {HEADS: lambda: CountingProducer(loads)},
        idle_unload_seconds=50,
        clock=lambda: next(clock),
    )

    first = runtime.decide(HEADS, b"")
    assert runtime.unload_idle() == (HEADS,)
    assert runtime.status()[HEADS].loaded is False

    second = runtime.decide(HEADS, b"")

    assert loads == [1, 1]
    assert second == first


def test_preload_holds_the_weights_before_the_first_request():
    loads: list[int] = []
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer(loads)})

    with service(runtime, preload=True) as client:
        assert client.get("/health").json()["producers"][HEADS]["loaded"] is True

    assert loads == [1]


def test_preload_does_not_stop_the_service_starting_when_weights_are_absent(tmp_path):
    settings = InferenceSettings(cache_dir=tmp_path, preload=True)
    runtime = ProducerRuntime({HEADS: default_loaders(settings)[HEADS]})

    with TestClient(create_app(settings, runtime=runtime)) as client:
        assert client.get("/ping").json() == "pong"
        assert client.get("/health").json()["producers"][HEADS]["loaded"] is False


def test_a_producer_still_inside_its_idle_window_is_kept():
    runtime = ProducerRuntime(
        {HEADS: lambda: CountingProducer([])}, idle_unload_seconds=300, clock=lambda: 1000.0
    )
    runtime.decide(HEADS, b"")

    assert runtime.unload_idle() == ()
    assert runtime.status()[HEADS].loaded is True


def test_a_zero_idle_period_holds_every_producer():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])}, idle_unload_seconds=0)
    runtime.decide(HEADS, b"")

    assert runtime.unload_idle() == ()
    assert runtime.status()[HEADS].loaded is True


def test_a_name_this_runtime_never_heard_of_is_not_a_missing_key():
    runtime = ProducerRuntime({HEADS: lambda: CountingProducer([])})

    with pytest.raises(UnknownProducer, match="captions"):
        runtime.decide("captions", b"")


def test_a_producer_that_cannot_load_says_which_one():
    def refuse() -> CountingProducer:
        raise FileNotFoundError("/cache/dinov2-small.onnx")

    runtime = ProducerRuntime({HEADS: refuse})

    with pytest.raises(ProducerUnavailable, match="heads: FileNotFoundError"):
        runtime.decide(HEADS, b"")


def test_the_entrypoint_serves_one_worker_on_the_configured_port(monkeypatch):
    served = {}

    # WHY: uvicorn.run blocks forever serving HTTP. The boundary replaced is the
    # ASGI server; what is under test is what the entrypoint asks it for.
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kwargs: served.update(kwargs, app=app is not None)
    )
    monkeypatch.setenv("IMMICH_MEMORIES_INFERENCE_PORT", "9099")

    entrypoint.main()

    assert served == {"app": True, "host": "127.0.0.1", "port": 9099, "workers": 1}


def test_the_default_loaders_serve_every_producer_the_client_asks_for():
    assert set(default_loaders(InferenceSettings())) == {HEADS, NSFW_MARQO, DOC_DOCLING}


@pytest.mark.parametrize(
    ("choice", "available", "expected"),
    [
        ("auto", ("CUDAExecutionProvider", "CPUExecutionProvider"), "CUDAExecutionProvider"),
        ("auto", ("CoreMLExecutionProvider", "CPUExecutionProvider"), "CPUExecutionProvider"),
        ("cuda", ("CPUExecutionProvider",), None),
    ],
)
def test_the_advertised_provider_is_the_one_a_session_would_take(choice, available, expected):
    assert would_use_provider(choice, available) == expected
