"""Portable detector producers, isolated in a cancellable configured Python worker.

The worker needs huggingface-hub, onnxruntime, numpy and Pillow. Both producers
are ONNX graphs on the CPU provider; neither needs the torch family. Model
acquisition is opt-in: the pinned local export and the cached pinned snapshot
are sufficient by default.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from operator import itemgetter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

MARQO_REPO = "Marqo/nsfw-image-detection-384"
MARQO_REVISION = "0c26ec22111b83f106d72a55f611ec35962bcb65"
# The ONNX export of that checkpoint, written by scripts/export_marqo_onnx.py.
# It decides the same labels as the timm/torch path it replaces, but it is a
# different artifact, so it is its own producer: its own digest, its own id and
# its own fact version. The digest is checked on load, the way the encoder's is.
MARQO_ONNX_SHA256 = "924658f1ac638d96e9126ecb29de047dc8d31c9c9defcab77a26a5c96ed69e11"
MARQO_ONNX_ID = f"{MARQO_REPO}@{MARQO_REVISION[:8]}/onnx-384"
MARQO_CLASSES = ("NSFW", "SFW")
MARQO_SIDE = 384
MARQO_VERSION = "det-v2"
DOCLING_REPO = "docling-project/DocumentFigureClassifier-v2.0"
DOCLING_REVISION = "2a12e02668b98ca40216eab41cdf19530577cba4"
DOCLING_FILE = "model.onnx"
DOCLING_VERSION = "det-v2"
DETECTOR_VERSIONS = {"nsfw_marqo": MARQO_VERSION, "doc_docling": DOCLING_VERSION}
# Everything a cold Hugging Face cache needs before `allow_model_downloads:
# false` can mean what it says. The Marqo seat is no longer here: it reads one
# digest-pinned file `models fetch` downloads, and never contacts the Hub.
DETECTOR_SNAPSHOTS = ((DOCLING_REPO, DOCLING_REVISION, (DOCLING_FILE,)),)
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


class DetectorModelUnavailable(RuntimeError):
    """A producer's pinned model is absent, unreadable, or not the pinned artifact.

    Raised at load, before a single picture is read, and its message names the
    model and the command that would supply it. A producer that cannot load used
    to contribute nothing and say nothing, and the run only noticed thousands of
    pictures later, as a count of missing facts.
    """


class Marqo:
    head = "nsfw_marqo"
    version = MARQO_VERSION
    encoder_key = MARQO_ONNX_ID
    classes = MARQO_CLASSES

    def __init__(self, *, model_path: Path) -> None:
        _refuse_unpinned_marqo(model_path)
        self.session, self.input_name = _cpu_session(model_path)

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        return _softmax(self.session.run(None, {self.input_name: marqo_pixels(images)})[0])


class Docling:
    head = "doc_docling"
    version = DOCLING_VERSION
    encoder_key = DOCLING_REPO
    classes = DOCLING_LABELS

    def __init__(self, *, allow_downloads: bool, cache_dir: str | None) -> None:
        self.session, self.input_name = _cpu_session(
            _docling_snapshot(allow_downloads=allow_downloads, cache_dir=cache_dir),
            disable_layout_optimizer=True,
        )

    def batch(self, images: list[Image.Image]) -> np.ndarray:
        return _softmax(self.session.run(None, {self.input_name: docling_pixels(images)})[0])


def _cpu_session(model_path: Path, *, disable_layout_optimizer: bool = False) -> tuple[Any, str]:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 6
    if disable_layout_optimizer:
        # Docling collapses to near-constant table predictions with the NCHWc
        # layout fusion on J4125. Extended matches the unfused graph on x86/arm64.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
    session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    return session, session.get_inputs()[0].name


def _refuse_unpinned_marqo(model_path: Path) -> None:
    if not model_path.is_file():
        raise DetectorModelUnavailable(
            f"nsfw_marqo has no model: {MARQO_ONNX_ID} is not at {model_path}. "
            "Run `immich-memories models fetch` to download it, or point "
            "advanced.editorial.preparation.marqo_onnx at your copy of the export."
        )
    with model_path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != MARQO_ONNX_SHA256:
        raise DetectorModelUnavailable(
            f"nsfw_marqo refuses {model_path}: digest {digest[:12]} is not the pinned "
            f"{MARQO_ONNX_SHA256[:12]} export of {MARQO_ONNX_ID}. "
            "Run `immich-memories models fetch --force` to replace it."
        )


def _docling_snapshot(*, allow_downloads: bool, cache_dir: str | None) -> Path:
    from huggingface_hub import hf_hub_download

    try:
        return Path(
            hf_hub_download(
                DOCLING_REPO,
                DOCLING_FILE,
                revision=DOCLING_REVISION,
                cache_dir=cache_dir,
                local_files_only=not allow_downloads,
            )
        )
    except (OSError, ValueError) as exc:
        where = cache_dir or "the Hugging Face cache"
        raise DetectorModelUnavailable(
            f"doc_docling has no model: {DOCLING_REPO}@{DOCLING_REVISION[:8]} "
            f"({DOCLING_FILE}) is not in {where}. Run `immich-memories models fetch` to "
            "warm it, or set advanced.editorial.preparation.allow_model_downloads to true "
            f"so the worker may acquire it itself ({type(exc).__name__}: {exc})"
        ) from exc


def _softmax(logits: np.ndarray) -> np.ndarray:
    exp = np.exp(logits - logits.max(-1, keepdims=True))
    return exp / exp.sum(-1, keepdims=True)


def marqo_pixels(images: Sequence[Image.Image]) -> np.ndarray:
    """timm's resolved transform for the pinned checkpoint, without timm or torch.

    Resize the short side to 384 bicubic, centre crop, scale to [0,1] and
    normalise by 0.5/0.5 -- the data config the checkpoint carries. Written in
    numpy so this seat drops the torch family entirely; `export_marqo_onnx.py
    --verify-pixels` is what proves it against the timm pipeline, and measured
    the two at 1.01e-3 of probability with every label agreeing.
    """
    tensors = []
    for image in images:
        width, height = image.size
        scale = MARQO_SIDE / min(width, height)
        resized = image.resize(
            (max(MARQO_SIDE, round(width * scale)), max(MARQO_SIDE, round(height * scale))),
            Image.Resampling.BICUBIC,
        )
        left = (resized.width - MARQO_SIDE) // 2
        top = (resized.height - MARQO_SIDE) // 2
        cropped = resized.crop((left, top, left + MARQO_SIDE, top + MARQO_SIDE))
        pixels = np.asarray(cropped, dtype=np.float32) / 255.0
        tensors.append(((pixels - 0.5) / 0.5).transpose(2, 0, 1))
    return np.ascontiguousarray(np.stack(tensors), dtype=np.float32)


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
    refusals: dict[str, str] = field(default_factory=dict)

    def record(self, rows: int) -> None:
        self.done += rows
        self._publish()

    def refuse(self, head: str, reason: str) -> None:
        """Publish a producer's refusal now, so the parent can say it while the run continues."""
        self.refusals[head] = reason
        self._publish()

    def _publish(self) -> None:
        if not self.path:
            return
        path = Path(self.path)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"done": self.done, "total": self.total, "refusals": self.refusals})
        )
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


def _decided_rows(detector: Marqo | Docling, keep: Sequence[str], probabilities) -> list:
    rows = []
    for asset_id, values in zip(keep, probabilities, strict=True):
        scores = dict(zip(detector.classes, map(float, values), strict=True))
        if not all(np.isfinite(p) and 0 <= p <= 1 for p in scores.values()):
            raise ValueError("detector returned invalid probabilities")
        label, confidence = decide(detector.head, scores)
        rows.append(
            (
                asset_id,
                detector.head,
                detector.version,
                label,
                confidence,
                detector.encoder_key,
                _now(),
            )
        )
    return rows


def _open_detector(head: str, job: dict) -> Marqo | Docling:
    # Resolved through the module rather than a table built at import, so an
    # installed-dependency stub can replace either producer.
    if head == "nsfw_marqo":
        return Marqo(model_path=Path(job["marqo_onnx"]).expanduser())
    return Docling(allow_downloads=job["allow_downloads"], cache_dir=job["cache_dir"])


def _run_head(
    job: dict,
    connection: sqlite3.Connection,
    detector: Marqo | Docling,
    asset_ids: Sequence[str],
    failures: dict[str, str],
    progress: _WorkerProgress,
) -> None:
    for start in range(0, len(asset_ids), job["batch_size"]):
        images, keep = _open_previews(
            job["previews"], asset_ids[start : start + job["batch_size"]], detector.head, failures
        )
        if not images:
            continue
        rows = _decided_rows(detector, keep, detector.batch(images))
        connection.executemany(_INSERT_FACT, rows)
        connection.commit()
        progress.record(len(rows))


def _failure_text(head: str, exc: Exception) -> str:
    if isinstance(exc, DetectorModelUnavailable):
        return str(exc)
    return (
        f"{head}: {type(exc).__name__}: {exc}. The detector interpreter needs onnxruntime, "
        "huggingface-hub, numpy and Pillow, plus the pinned model artifacts; configure "
        "detector_python / detector_cache_dir or allow_model_downloads."
    )


def _worker(job: dict) -> dict[str, str]:
    failures: dict[str, str] = {}
    progress = _WorkerProgress(job.get("progress_path"), sum(map(len, job["pending"].values())))
    # Every requested producer is loaded before the first picture, and a
    # refusal is published the moment it happens. A missing model used to be
    # discovered only when the whole worker had finished, which on a slow box
    # meant half an hour of the other producer's work before anything said so.
    loaded: dict[str, Marqo | Docling] = {}
    for head in job["pending"]:
        try:
            loaded[head] = _open_detector(head, job)
        except Exception as exc:
            progress.refuse(head, _failure_text(head, exc))
    failures.update(progress.refusals)
    with closing(sqlite3.connect(job["store_path"], timeout=60)) as connection:
        for head, asset_ids in job["pending"].items():
            if head not in loaded:
                continue
            try:
                _run_head(job, connection, loaded[head], asset_ids, failures, progress)
            except Exception as exc:
                failures[head] = _failure_text(head, exc)
    return failures


def prepare_detectors(
    *,
    pending: Mapping[str, Sequence[str]],
    store_path: Path,
    preview_paths: Mapping[str, Path],
    python: str,
    cache_dir: str,
    marqo_onnx: Path,
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
                    "marqo_onnx": str(Path(marqo_onnx).expanduser()),
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
            watch = _WorkerWatch(progress_path, progress)
            try:
                _await_worker(process, watch, check_cancelled)
                check_cancelled()
                if process.returncode or not result_path.is_file():
                    log.seek(0)
                    raise RuntimeError(
                        f"detector worker failed; install required detector dependencies in {interpreter}: {log.read()[-3000:]}"
                    )
                result = json.loads(result_path.read_text())
                watch.poll()
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


@dataclass
class _WorkerWatch:
    """Read what the worker publishes; announce each producer's refusal exactly once."""

    path: Path
    progress: Callable[[str, int, int], None]
    done: int = 0
    announced: set[str] = field(default_factory=set)

    def poll(self) -> None:
        if not self.path.is_file():
            return
        published = json.loads(self.path.read_text())
        for head, reason in published.get("refusals", {}).items():
            if head not in self.announced:
                self.announced.add(head)
                logger.warning("%s", reason)
        if published["done"] != self.done:
            self.done = published["done"]
            self.progress("detectors", self.done, published["total"])


def _await_worker(
    process: subprocess.Popen,
    watch: _WorkerWatch,
    check_cancelled: Callable[[], None],
) -> None:
    while process.poll() is None:
        check_cancelled()
        watch.poll()
        time.sleep(0.2)


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
