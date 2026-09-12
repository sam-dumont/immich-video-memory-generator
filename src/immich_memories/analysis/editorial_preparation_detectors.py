"""Portable detector producers, isolated in a cancellable configured Python worker.

The worker needs timm, torch, huggingface-hub, onnxruntime, numpy and Pillow.
Model acquisition is opt-in; cached pinned snapshots are sufficient by default.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from operator import itemgetter
from pathlib import Path

import numpy as np
from PIL import Image

DETECTOR_HEAD_VERSIONS = {"nsfw_marqo": "det-v1", "doc_docling": "det-v2"}
MARQO_REPO = "Marqo/nsfw-image-detection-384"
MARQO_REVISION = "0c26ec22111b83f106d72a55f611ec35962bcb65"
MARQO_FILES = ("config.json", "model.safetensors")
DOCLING_REPO = "docling-project/DocumentFigureClassifier-v2.0"
DOCLING_REVISION = "2a12e02668b98ca40216eab41cdf19530577cba4"
DOCLING_FILE = "model.onnx"
# Everything a cold cache needs before `allow_model_downloads: false` can mean what it says.
DETECTOR_SNAPSHOTS = (
    (MARQO_REPO, MARQO_REVISION, MARQO_FILES),
    (DOCLING_REPO, DOCLING_REVISION, (DOCLING_FILE,)),
)
DOCLING_LABELS = (
    "logo",
    "photograph",
    "icon",
    "engineering_drawing",
    "line_chart",
    "bar_chart",
    "other",
    "table",
    "flow_chart",
    "screenshot_from_computer",
    "signature",
    "screenshot_from_manual",
    "geographical_map",
    "pie_chart",
    "page_thumbnail",
    "stamp",
    "music",
    "calendar",
    "qr_code",
    "bar_code",
    "full_page_image",
    "scatter_plot",
    "chemistry_structure",
    "topographical_map",
    "crossword_puzzle",
    "box_plot",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Marqo:
    head = "nsfw_marqo"
    encoder_key = MARQO_REPO

    def __init__(self, *, allow_downloads: bool, cache_dir: str | None) -> None:
        import torch
        from huggingface_hub import hf_hub_download

        timm = import_module("timm")
        for filename in MARQO_FILES:
            hf_hub_download(
                MARQO_REPO,
                filename,
                revision=MARQO_REVISION,
                cache_dir=cache_dir,
                local_files_only=not allow_downloads,
            )
        torch.set_num_threads(6)
        self.torch = torch
        self.model = timm.create_model(
            f"hf_hub:{MARQO_REPO}@{MARQO_REVISION}", pretrained=True
        ).eval()
        config = timm.data.resolve_data_config({}, model=self.model)
        self.transform = timm.data.create_transform(**config, is_training=False)
        self.classes = self.model.pretrained_cfg["label_names"]

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        pixels = self.torch.stack([self.transform(image) for image in images])
        with self.torch.no_grad():
            return self.model(pixels).softmax(-1).numpy()


class Docling:
    head = "doc_docling"
    encoder_key = DOCLING_REPO
    classes = DOCLING_LABELS

    def __init__(self, *, allow_downloads: bool, cache_dir: str | None) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            DOCLING_REPO,
            DOCLING_FILE,
            revision=DOCLING_REVISION,
            cache_dir=cache_dir,
            local_files_only=not allow_downloads,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 6
        # ORT 1.28's layout-optimized graph collapses to near-constant "table"
        # predictions on J4125. Extended agrees with the unfused graph on both
        # J4125 and arm64, retaining the basic/extended fusions without NCHWc.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        self.session = ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        pixels = docling_pixels(images)
        logits = self.session.run(None, {self.input_name: pixels})[0]
        exp = np.exp(logits - logits.max(-1, keepdims=True))
        return exp / exp.sum(-1, keepdims=True)


def docling_pixels(images: Sequence[Image.Image]) -> np.ndarray:
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
    std = np.array([0.47853944, 0.4732864, 0.47434163], dtype=np.float32).reshape(3, 1, 1)
    return np.stack(
        [
            (
                np.asarray(
                    image.resize((224, 224), Image.Resampling.BILINEAR), dtype=np.float32
                ).transpose(2, 0, 1)
                / 255.0
                - mean
            )
            / std
            for image in images
        ]
    )


def decide(head: str, scores: Mapping[str, float]) -> tuple[str, float]:
    if head == "nsfw_marqo":
        probability = scores["NSFW"]
        return ("yes" if probability >= 0.5 else "no"), round(probability, 5)
    if head == "doc_docling":
        label, probability = max(scores.items(), key=itemgetter(1))
        return label, round(probability, 5)
    raise ValueError(f"unsupported detector head: {head}")


_INSERT_FACT = (
    "INSERT OR REPLACE INTO head_facts "
    "(asset_id,head,version,label,confidence,encoder_key,decided_at) VALUES (?,?,?,?,?,?,?)"
)


@dataclass
class _WorkerProgress:
    """The worker reports through a file the parent polls; a partial write is never read."""

    path: str | None
    total: int
    done: int = 0

    def record(self, rows: int) -> None:
        self.done += rows
        if not self.path:
            return
        path = Path(self.path)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"done": self.done, "total": self.total}))
        temporary.replace(path)


def _open_previews(
    previews: Mapping[str, str], asset_ids: Sequence[str], head: str, failures: dict[str, str]
) -> tuple[list[Image.Image], list[str]]:
    images: list[Image.Image] = []
    keep: list[str] = []
    for asset_id in asset_ids:
        try:
            with Image.open(previews[asset_id]) as image:
                images.append(image.convert("RGB"))
            keep.append(asset_id)
        except (OSError, ValueError) as exc:
            failures[f"{head}:{asset_id}"] = str(exc)
    return images, keep


def _decided_rows(head: str, detector: Marqo | Docling, keep: Sequence[str], probabilities) -> list:
    rows = []
    for asset_id, values in zip(keep, probabilities, strict=True):
        scores = dict(zip(detector.classes, map(float, values), strict=True))
        if not all(np.isfinite(p) and 0 <= p <= 1 for p in scores.values()):
            raise ValueError("detector returned invalid probabilities")
        label, confidence = decide(head, scores)
        rows.append(
            (
                asset_id,
                head,
                DETECTOR_HEAD_VERSIONS[head],
                label,
                confidence,
                detector.encoder_key,
                _now(),
            )
        )
    return rows


def _run_head(
    job: dict,
    connection: sqlite3.Connection,
    head: str,
    asset_ids: Sequence[str],
    failures: dict[str, str],
    progress: _WorkerProgress,
) -> None:
    # Read the classes here so an installed-dependency stub can replace either producer.
    models: dict[str, type[Marqo] | type[Docling]] = {"nsfw_marqo": Marqo, "doc_docling": Docling}
    detector = models[head](allow_downloads=job["allow_downloads"], cache_dir=job["cache_dir"])
    for start in range(0, len(asset_ids), job["batch_size"]):
        images, keep = _open_previews(
            job["previews"], asset_ids[start : start + job["batch_size"]], head, failures
        )
        if not images:
            continue
        rows = _decided_rows(head, detector, keep, detector.batch(images))
        connection.executemany(_INSERT_FACT, rows)
        connection.commit()
        progress.record(len(rows))


def _worker(job: dict) -> dict[str, str]:
    failures: dict[str, str] = {}
    progress = _WorkerProgress(job.get("progress_path"), sum(map(len, job["pending"].values())))
    with closing(sqlite3.connect(job["store_path"], timeout=60)) as connection:
        for head, asset_ids in job["pending"].items():
            try:
                _run_head(job, connection, head, asset_ids, failures, progress)
            except Exception as exc:
                failures[head] = (
                    f"{type(exc).__name__}: {exc}. Required detector setup: timm, torch, "
                    "huggingface-hub, onnxruntime, numpy, Pillow and the pinned cached models; "
                    "configure detector_python / detector_cache_dir or allow_model_downloads."
                )
    return failures


def prepare_detectors(
    *,
    pending: Mapping[str, Sequence[str]],
    store_path: Path,
    preview_paths: Mapping[str, Path],
    python: str,
    cache_dir: str,
    allow_downloads: bool,
    batch_size: int,
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
) -> dict[str, str]:
    if not pending:
        return {}
    check_cancelled()
    with tempfile.TemporaryDirectory(prefix="immich-editorial-detectors-") as directory:
        job_path = Path(directory) / "job.json"
        result_path = Path(directory) / "result.json"
        progress_path = Path(directory) / "progress.json"
        job_path.write_text(
            json.dumps(
                {
                    "pending": pending,
                    "store_path": str(store_path.resolve()),
                    "previews": {key: str(path.resolve()) for key, path in preview_paths.items()},
                    "cache_dir": str(Path(cache_dir).expanduser()) if cache_dir else None,
                    "allow_downloads": allow_downloads,
                    "batch_size": batch_size,
                    "progress_path": str(progress_path),
                }
            )
        )
        interpreter = str(Path(python).expanduser()) if python else sys.executable
        with (Path(directory) / "worker.log").open("w+") as log:
            process = subprocess.Popen(  # noqa: S603 — configured executable, no shell
                # Run this packaged file directly so a detector-only environment
                # does not need unrelated application/UI configuration dependencies.
                [interpreter, str(Path(__file__).resolve()), str(job_path), str(result_path)],
                env=_worker_env(cache_dir, allow_downloads),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                previous = _await_worker(process, progress_path, check_cancelled, progress)
                check_cancelled()
                if process.returncode or not result_path.is_file():
                    log.seek(0)
                    raise RuntimeError(
                        f"detector worker failed; install required detector dependencies in {interpreter}: {log.read()[-3000:]}"
                    )
                result = json.loads(result_path.read_text())
                _report_progress(progress_path, previous, progress)
                return result
            finally:
                _stop_worker(process)


def _worker_env(cache_dir: str, allow_downloads: bool) -> dict[str, str]:
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "6"
    env["TOKENIZERS_PARALLELISM"] = "false"
    if cache_dir:
        env["HF_HUB_CACHE"] = str(Path(cache_dir).expanduser())
    if not allow_downloads:
        env["HF_HUB_OFFLINE"] = "1"
    return env


def _report_progress(
    progress_path: Path, previous: int, progress: Callable[[str, int, int], None]
) -> int:
    if not progress_path.is_file():
        return previous
    counts = json.loads(progress_path.read_text())
    if counts["done"] == previous:
        return previous
    progress("detectors", counts["done"], counts["total"])
    return counts["done"]


def _await_worker(
    process: subprocess.Popen,
    progress_path: Path,
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
) -> int:
    previous = 0
    while process.poll() is None:
        check_cancelled()
        previous = _report_progress(progress_path, previous, progress)
        time.sleep(0.2)
    return previous


def _stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(json.dumps(_worker(json.loads(Path(sys.argv[1]).read_text()))))
