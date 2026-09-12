"""Detector portability and cancellation, using synthetic probabilities only."""

import signal
import sqlite3

import numpy as np
import pytest
from PIL import Image

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories.operations.cancellation import PipelineCancelled
from immich_memories.store.editorial_preparation import initialize


def test_one_missing_detector_preserves_other_completed_facts(monkeypatch, tmp_path):
    path = tmp_path / "preview.jpg"
    Image.new("RGB", (80, 60), "white").save(path)
    database = tmp_path / "store.sqlite"
    with sqlite3.connect(database) as connection:
        initialize(connection)

    def missing(**_):
        raise ModuleNotFoundError("timm")

    class FakeDocling:
        encoder_key = detectors.DOCLING_REPO
        classes = ["photograph", "signature"]

        def __init__(self, **_):
            pass

        def batch(self, images):
            return np.array([[0.8, 0.2] for _ in images])

    monkeypatch.setattr(detectors, "Marqo", missing)
    monkeypatch.setattr(detectors, "Docling", FakeDocling)
    errors = detectors._worker(
        {
            "store_path": str(database),
            "pending": {"nsfw_marqo": ["a"], "doc_docling": ["a"]},
            "previews": {"a": str(path)},
            "batch_size": 16,
            "allow_downloads": False,
            "cache_dir": None,
        }
    )
    assert "timm" in errors["nsfw_marqo"]
    assert "doc_docling" not in errors
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT head,version,label,confidence,encoder_key FROM head_facts"
        ).fetchall() == [("doc_docling", "det-v2", "photograph", 0.8, detectors.DOCLING_REPO)]


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

    monkeypatch.setattr(detectors.subprocess, "Popen", popen)
    monkeypatch.setattr(detectors.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(PipelineCancelled):
        detectors.prepare_detectors(
            pending={"nsfw_marqo": ["a"]},
            store_path=tmp_path / "store",
            preview_paths={"a": tmp_path / "preview"},
            python="/configured/python",
            cache_dir="/configured/cache",
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
