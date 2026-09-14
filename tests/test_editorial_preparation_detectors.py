"""Detector portability, refusal reporting and cancellation, on synthetic probabilities."""

import logging
import signal
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories.operations.cancellation import PipelineCancelled
from immich_memories.store.editorial_preparation import initialize


class FakeDocling:
    head = "doc_docling"
    version = detectors.DOCLING_VERSION
    encoder_key = detectors.DOCLING_REPO
    classes = ("photograph", "signature")

    def __init__(self, **_):
        pass

    def batch(self, images):
        return np.array([[0.8, 0.2] for _ in images])


def _job(tmp_path, pending):
    path = tmp_path / "preview.jpg"
    Image.new("RGB", (80, 60), "white").save(path)
    database = tmp_path / "store.sqlite"
    with sqlite3.connect(database) as connection:
        initialize(connection)
    return database, {
        "store_path": str(database),
        "pending": pending,
        "previews": {"a": str(path)},
        "batch_size": 16,
        "allow_downloads": False,
        "cache_dir": None,
        "marqo_onnx": str(tmp_path / "absent-nsfw-marqo-384.onnx"),
    }


def test_one_missing_detector_preserves_other_completed_facts(monkeypatch, tmp_path):
    database, job = _job(tmp_path, {"nsfw_marqo": ["a"], "doc_docling": ["a"]})
    # WHY: the real Docling seat downloads a pinned snapshot from Hugging Face.
    monkeypatch.setattr(detectors, "Docling", FakeDocling)

    errors = detectors._worker(job)

    assert "doc_docling" not in errors
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT head,version,label,confidence,encoder_key FROM head_facts"
        ).fetchall() == [
            ("doc_docling", "det-v2", "photograph", 0.8, detectors.DOCLING_REPO),
        ]


def test_the_torch_free_transform_produces_the_tensor_the_export_expects():
    """The numpy transform is what lets this seat drop timm and torchvision.

    Short side to 384, centre crop, and 0.5/0.5 normalisation -- the data config
    the pinned checkpoint carries. `export_marqo_onnx.py --verify-pixels` proves
    it against timm's own pipeline; this keeps its shape and range honest.
    """
    wide = Image.new("RGB", (1000, 500), "white")
    tall = Image.new("RGB", (500, 1000), "black")

    pixels = detectors.marqo_pixels([wide, tall])

    assert pixels.shape == (2, 3, detectors.MARQO_SIDE, detectors.MARQO_SIDE)
    assert pixels.dtype == np.float32
    assert pixels[0].min() == pytest.approx(1.0) and pixels[0].max() == pytest.approx(1.0)
    assert pixels[1].min() == pytest.approx(-1.0) and pixels[1].max() == pytest.approx(-1.0)


def test_the_document_detector_without_its_snapshot_names_the_repository(monkeypatch):
    class ColdCache(ModuleType):
        @staticmethod
        def hf_hub_download(*_args, **_kwargs):
            raise OSError("offline and not in the local cache")

    # WHY: hf_hub_download is the Hugging Face boundary; huggingface-hub itself
    # is an `editorial`-extra dependency the unit environment does not install.
    monkeypatch.setitem(sys.modules, "huggingface_hub", ColdCache("huggingface_hub"))

    with pytest.raises(detectors.DetectorModelUnavailable) as raised:
        detectors.Docling(allow_downloads=False, cache_dir="/configured/cache")

    assert detectors.DOCLING_REPO in str(raised.value)
    assert "/configured/cache" in str(raised.value)
    assert "models fetch" in str(raised.value)


def test_a_producer_without_its_model_names_the_model_and_the_fix(tmp_path):
    _database, job = _job(tmp_path, {"nsfw_marqo": ["a"]})

    reason = detectors._worker(job)["nsfw_marqo"]

    assert detectors.MARQO_ONNX_ID in reason
    assert "absent-nsfw-marqo-384.onnx" in reason
    assert "models fetch" in reason


def test_a_graph_that_is_not_the_pinned_export_is_refused_by_digest(tmp_path):
    _database, job = _job(tmp_path, {"nsfw_marqo": ["a"]})
    present = tmp_path / "nsfw-marqo-384.onnx"
    present.write_bytes(b"not the pinned graph")
    job["marqo_onnx"] = str(present)

    reason = detectors._worker(job)["nsfw_marqo"]

    assert detectors.MARQO_ONNX_SHA256[:12] in reason
    assert "models fetch --force" in reason


def test_a_refusal_is_published_before_the_other_producer_runs(monkeypatch, tmp_path):
    """The worker loads every producer first, so its refusal is readable at once."""
    database, job = _job(tmp_path, {"nsfw_marqo": ["a"], "doc_docling": ["a"]})
    progress_path = tmp_path / "progress.json"
    job["progress_path"] = str(progress_path)
    published = []

    class WatchingDocling(FakeDocling):
        def batch(self, images):
            published.append(progress_path.read_text())
            return super().batch(images)

    # WHY: the real Docling seat downloads a pinned snapshot from Hugging Face.
    monkeypatch.setattr(detectors, "Docling", WatchingDocling)

    detectors._worker(job)

    assert published, "the surviving producer never ran"
    assert detectors.MARQO_ONNX_ID in published[0]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM head_facts").fetchone() == (1,)


def test_the_parent_logs_each_refusal_once_while_the_worker_is_still_running(tmp_path, caplog):
    progress_path = tmp_path / "progress.json"
    progress = detectors._WorkerProgress(str(progress_path), total=2)
    progress.refuse("nsfw_marqo", "nsfw_marqo has no model: run models fetch")
    progress.record(1)
    watch = detectors._WorkerWatch(progress_path, lambda *_: None)

    with caplog.at_level(logging.WARNING):
        watch.poll()
        watch.poll()

    assert [record.getMessage() for record in caplog.records] == [
        "nsfw_marqo has no model: run models fetch"
    ]


def test_cancellation_terminates_detector_process_group(monkeypatch, tmp_path):
    killed = []
    captured = []

    class Process:
        pid = 12345
        returncode = None

        def poll(self):
            return None

        def wait(self, **_):
            return 0

    def popen(argv, **kwargs):
        captured.append((argv, kwargs))
        return Process()

    checks = []

    def check():
        checks.append(True)
        if len(checks) == 2:
            raise PipelineCancelled()

    # WHY: both replace the detector subprocess boundary itself.
    monkeypatch.setattr(detectors.subprocess, "Popen", popen)
    monkeypatch.setattr(detectors.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(PipelineCancelled):
        detectors.prepare_detectors(
            pending={"nsfw_marqo": ["a"]},
            store_path=tmp_path / "store",
            preview_paths={"a": tmp_path / "preview"},
            python="/configured/python",
            cache_dir="/configured/cache",
            marqo_onnx=tmp_path / "nsfw-marqo-384.onnx",
            allow_downloads=False,
            batch_size=16,
            check_cancelled=check,
            progress=lambda *_: None,
        )
    assert killed == [(12345, signal.SIGTERM)]
    assert captured[0][0][0] == "/configured/python"
    assert captured[0][0][1].endswith("editorial_preparation_detectors.py")
    assert captured[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert captured[0][1]["env"]["HF_HUB_CACHE"] == "/configured/cache"


class FakeSession:
    """An ORT session that remembers what it was opened with."""

    def __init__(self, model_path, options, providers, running=None):
        self.model_path = model_path
        self.options = options
        self.providers = list(providers)
        self._running = list(providers if running is None else running)

    def get_inputs(self):
        return [SimpleNamespace(name="input")]

    def get_providers(self):
        return list(self._running)

    def run(self, _outputs, _feed):
        return [np.zeros((1, len(detectors.DOCLING_LABELS)), dtype=np.float32)]


def _fake_onnxruntime(monkeypatch, available, opened, *, refuse=(), running=None):
    """# WHY: ONNX Runtime is the boundary under test -- which execution providers
    a session is opened with, and what it answers when one turns the graph down.
    No CUDA host exists on Apple Silicon, so the runtime is what has to be stood in for."""

    def session(path, options, providers):
        opened.append(list(providers))
        if providers[0] in refuse:
            raise RuntimeError(f"{providers[0]} could not be initialised")
        return FakeSession(path, options, providers, running)

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: list(available),
            SessionOptions=lambda: SimpleNamespace(
                intra_op_num_threads=0, graph_optimization_level=None
            ),
            GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_EXTENDED="extended"),
            InferenceSession=session,
        ),
    )


def _fake_snapshot(monkeypatch, tmp_path):
    """# WHY: hf_hub_download is the Hugging Face boundary; the pinned Docling
    snapshot is 16.8 MB and this test never reads a byte of it."""
    model = tmp_path / "model.onnx"
    model.write_bytes(b"not read: the session is stood in for too")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(hf_hub_download=lambda *_args, **_kwargs: str(model)),
    )


@pytest.mark.parametrize(
    ("choice", "available", "expected"),
    [
        (
            "auto",
            ("CUDAExecutionProvider", "CPUExecutionProvider"),
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        ),
        ("auto", ("CPUExecutionProvider",), ["CPUExecutionProvider"]),
        (
            "cuda",
            ("CUDAExecutionProvider", "CPUExecutionProvider"),
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        ),
        ("cpu", ("CUDAExecutionProvider", "CPUExecutionProvider"), ["CPUExecutionProvider"]),
    ],
)
def test_a_detector_session_is_opened_on_the_provider_the_deployment_chose(
    monkeypatch, tmp_path, choice, available, expected
):
    opened: list[list[str]] = []
    _fake_onnxruntime(monkeypatch, available, opened)
    _fake_snapshot(monkeypatch, tmp_path)

    detectors.Docling(allow_downloads=False, cache_dir=None, provider=choice)

    assert opened == [expected]


def test_naming_a_provider_the_runtime_was_not_built_with_is_refused(monkeypatch, tmp_path):
    _fake_onnxruntime(monkeypatch, ("CPUExecutionProvider",), [])
    _fake_snapshot(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="CUDAExecutionProvider is unavailable"):
        detectors.Docling(allow_downloads=False, cache_dir=None, provider="cuda")


def test_a_card_that_refuses_the_graph_is_said_once_and_the_cpu_decides(
    monkeypatch, tmp_path, caplog
):
    opened: list[list[str]] = []
    monkeypatch.setattr(detectors, "_ANNOUNCED", set())
    _fake_onnxruntime(
        monkeypatch,
        ("CUDAExecutionProvider", "CPUExecutionProvider"),
        opened,
        refuse=("CUDAExecutionProvider",),
    )
    _fake_snapshot(monkeypatch, tmp_path)

    with caplog.at_level(logging.WARNING, logger=detectors.__name__):
        detectors.Docling(allow_downloads=False, cache_dir=None, provider="cuda")
        detectors.Docling(allow_downloads=False, cache_dir=None, provider="cuda")

    assert opened[0] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert opened[1] == ["CPUExecutionProvider"]
    said = [record.getMessage() for record in caplog.records]
    assert len(said) == 1, said
    assert "doc_docling" in said[0]
    assert "deciding on CPUExecutionProvider" in said[0]


def test_a_session_the_card_never_joined_names_what_is_running(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(detectors, "_ANNOUNCED", set())
    _fake_onnxruntime(
        monkeypatch,
        ("CUDAExecutionProvider", "CPUExecutionProvider"),
        [],
        running=["CPUExecutionProvider"],
    )
    _fake_snapshot(monkeypatch, tmp_path)

    with caplog.at_level(logging.WARNING, logger=detectors.__name__):
        detectors.Docling(allow_downloads=False, cache_dir=None, provider="cuda")

    assert [record.getMessage() for record in caplog.records] == [
        "doc_docling: asked for CUDAExecutionProvider, running on CPUExecutionProvider"
    ]


def test_the_in_process_worker_takes_the_card_where_there_is_one(monkeypatch, tmp_path):
    """No knob: the worker runs on whatever the host it was configured for has."""
    opened: list[list[str]] = []
    _fake_onnxruntime(monkeypatch, ("CUDAExecutionProvider", "CPUExecutionProvider"), opened)
    _fake_snapshot(monkeypatch, tmp_path)
    _database, job = _job(tmp_path, {"doc_docling": ["a"]})

    detectors._open_detector("doc_docling", job)

    assert opened == [["CUDAExecutionProvider", "CPUExecutionProvider"]]
