#!/usr/bin/env python3
"""Time every producer `prepare` runs, per picture, on whatever machine this is.

Each stage calls the product's own function on real JPEG bytes, so a number here
is a number the pipeline actually pays. Model load is timed apart from steady
state because a service loads once and a CLI run loads once per invocation.

    uv run python scripts/benchmark_preparation.py --repeat 5
    uv run python scripts/benchmark_preparation.py --stages caption \
        --caption-base-url http://localhost:8092/v1

`PYTHONPATH=src` is enough to run it against a checkout with no install, which is
how it runs inside a container.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGES = REPO_ROOT / "tests" / "fixtures" / "hdr_samples"
# The same place `TriageConfig.encoder` looks; `--encoder` points at any other copy.
DEFAULT_ENCODER = "~/.immich-memories/models/triage/dinov2-small.onnx"
DEFAULT_BUNDLE = REPO_ROOT / "src" / "immich_memories" / "triage" / "bundled_heads"
ALL_STAGES = (
    "preview_decode",
    "jpeg_decode",
    "pixel_facts",
    "thumbnail_hash",
    "caption_tile",
    "dino_preprocess",
    "dino_embed",
    "heads",
    "nsfw_marqo",
    "nsfw_marqo_onnx",
    "doc_docling",
    "caption",
)
# Not a producer: it measures work the producers each repeat, so it never joins the total.
DIAGNOSTIC_STAGES = ("jpeg_decode",)
# A caption server caches image embeddings, so sending the same pictures again
# measures its cache, not the model. For these the cold pass is the only number.
COLD_ONLY_STAGES = ("caption",)
Preview = tuple[str, bytes]


class Unavailable(RuntimeError):
    """This machine cannot run this stage; report it, do not fail the run."""


@dataclass
class Stage:
    """One producer's cost, split into what a service pays once and what every picture pays."""

    name: str
    load_seconds: float = 0.0
    samples: list[float] = field(default_factory=list)
    note: str = ""
    unavailable: str = ""
    rss_mb: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def seconds_per_picture(self) -> float:
        return statistics.mean(self.samples) if self.samples else 0.0

    @property
    def p90_seconds(self) -> float:
        if not self.samples:
            return 0.0
        return sorted(self.samples)[min(len(self.samples) - 1, int(0.9 * len(self.samples)))]

    def row(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "pictures": len(self.samples),
            "load_seconds": round(self.load_seconds, 3),
            "seconds_per_picture": round(self.seconds_per_picture, 4),
            "p90_seconds": round(self.p90_seconds, 4),
            "rss_mb_high_water": round(self.rss_mb, 1),
            "note": self.note,
            "unavailable": self.unavailable,
            "calls": self.calls or None,
        }


@contextmanager
def timed(sink: list[float], divisor: int = 1) -> Iterator[None]:
    """Charge a batch's wall clock evenly to each picture in it."""
    started = time.perf_counter()
    yield
    sink.extend([(time.perf_counter() - started) / divisor] * divisor)


@contextmanager
def pretend_cores(count: int) -> Iterator[None]:
    """`triage/encoder.py` reads `os.cpu_count()` and has no threads setting to turn.

    Until W2 gives it one, the only way to measure the thread knob on the real
    session is to answer that question differently.
    """
    real = os.cpu_count
    os.cpu_count = lambda: count + 1  # type: ignore[assignment]
    try:
        yield
    finally:
        os.cpu_count = real  # type: ignore[assignment]


def load_previews(directory: Path) -> list[Preview]:
    """Read distinct JPEGs once, so no stage is charged for I/O or a cache it would not hit.

    Two fixtures with the same bytes look like two pictures to a loop and like
    one to any endpoint that caches image embeddings, which quietly halves a
    caption measurement. Identical payloads are dropped here instead.
    """
    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    previews: dict[str, Preview] = {}
    for path in paths:
        payload = path.read_bytes()
        previews.setdefault(hashlib.sha256(payload).hexdigest(), (path.name, payload))
    if not previews:
        raise SystemExit(f"no images in {directory}")
    if len(previews) < len(paths):
        print(f"  ({len(paths) - len(previews)} duplicate image(s) dropped)", flush=True)
    return list(previews.values())


def batches(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def pil_images(previews: Sequence[Preview]) -> list[Any]:
    from PIL import Image

    images = []
    for _name, payload in previews:
        with Image.open(io.BytesIO(payload)) as handle:
            images.append(handle.convert("RGB"))
    return images


def run_preview_decode(stage: Stage, previews: Sequence[Preview], _: Any) -> None:
    """What a rerun pays to prove a cached preview is still a readable JPEG."""
    from PIL import Image

    for _name, payload in previews:
        with timed(stage.samples), Image.open(io.BytesIO(payload)) as image:
            image.verify()


def run_jpeg_decode(stage: Stage, previews: Sequence[Preview], _: Any) -> None:
    """One decode to RGB — the work six later stages each repeat from the same bytes."""
    from PIL import Image

    for _name, payload in previews:
        with timed(stage.samples), Image.open(io.BytesIO(payload)) as handle:
            handle.convert("RGB")


def run_pixel_facts(stage: Stage, previews: Sequence[Preview], _: Any) -> None:
    from immich_memories.analysis.editorial_preparation_pixels import pixel_facts

    for _name, payload in previews:
        with timed(stage.samples):
            pixel_facts(payload)


def run_thumbnail_hash(stage: Stage, previews: Sequence[Preview], _: Any) -> None:
    try:
        from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash
    except ImportError as exc:
        raise Unavailable(f"opencv missing: {exc}")
    for _name, payload in previews:
        with timed(stage.samples):
            compute_thumbnail_hash(payload, 8)


def run_caption_tile(stage: Stage, previews: Sequence[Preview], _: Any) -> None:
    """The 400 px q90 tile the caption wire sends; the captioner never sees the preview."""
    from immich_memories.analysis.editorial_description_wire import tile_preview

    sizes = []
    for _name, payload in previews:
        with timed(stage.samples):
            tile = tile_preview(payload)
        sizes.append(len(tile))
    stage.note = f"tile median {int(statistics.median(sizes))} B"


def run_dino_preprocess(stage: Stage, previews: Sequence[Preview], _: Any) -> None:
    from immich_memories.triage.preprocess import preprocess_image_bytes

    for _name, payload in previews:
        with timed(stage.samples):
            preprocess_image_bytes(payload)


def open_encoder(stage: Stage, options: argparse.Namespace) -> Any:
    from immich_memories.triage.encoder import DinoEncoder

    path = Path(options.encoder).expanduser()
    if not path.is_file():
        raise Unavailable(f"pinned DINOv2 export not at {path}")
    started = time.perf_counter()
    with pretend_cores(options.ort_threads):
        encoder = DinoEncoder.open(path, provider=options.provider)
    stage.load_seconds = time.perf_counter() - started
    return encoder


def run_dino_embed(stage: Stage, previews: Sequence[Preview], options: argparse.Namespace) -> None:
    import numpy as np

    from immich_memories.triage.preprocess import preprocess_image_bytes

    encoder = open_encoder(stage, options)
    pixels = [preprocess_image_bytes(payload) for _name, payload in previews]
    for chunk in batches(pixels, options.batch_size):
        with timed(stage.samples, len(chunk)):
            encoder.embed(np.stack(chunk))
    stage.note = f"batch {options.batch_size}, {options.provider}, {options.ort_threads} threads"


def run_heads(stage: Stage, previews: Sequence[Preview], options: argparse.Namespace) -> None:
    import numpy as np

    from immich_memories.triage.heads import HeadBundle
    from immich_memories.triage.preprocess import preprocess_image_bytes

    started = time.perf_counter()
    bundle = HeadBundle.load(Path(options.bundle).expanduser())
    stage.load_seconds = time.perf_counter() - started
    encoder = open_encoder(Stage(name="scratch"), options)
    packs = encoder.embed(
        np.stack([preprocess_image_bytes(payload) for _name, payload in previews])
    )
    for chunk in batches(packs, options.batch_size):
        with timed(stage.samples, len(chunk)):
            bundle.decide(np.stack(chunk))
    stage.note = f"{len(bundle.heads)} heads, batch {options.batch_size}"


def run_nsfw_marqo(stage: Stage, previews: Sequence[Preview], options: argparse.Namespace) -> None:
    try:
        from immich_memories.analysis.editorial_preparation_detectors import Marqo
    except ImportError as exc:
        raise Unavailable(f"detector dependencies missing: {exc}")
    try:
        started = time.perf_counter()
        detector = Marqo(allow_downloads=options.allow_downloads, cache_dir=options.detector_cache)
    except Exception as exc:
        raise Unavailable(f"{type(exc).__name__}: {exc}")
    stage.load_seconds = time.perf_counter() - started
    # The producer hardcodes six threads; this is the only lever a benchmark has.
    detector.torch.set_num_threads(options.torch_threads)
    images = pil_images(previews)
    for chunk in batches(images, options.batch_size):
        with timed(stage.samples, len(chunk)):
            detector.batch(list(chunk))
    stage.note = f"timm torch, {options.torch_threads} threads, batch {options.batch_size}"


def run_nsfw_marqo_onnx(
    stage: Stage, previews: Sequence[Preview], options: argparse.Namespace
) -> None:
    """The same detector through ORT, which is the version that needs no torch at all."""
    if not options.marqo_onnx:
        raise Unavailable("no --marqo-onnx export given; scripts/export_marqo_onnx.py writes one")
    path = Path(options.marqo_onnx).expanduser()
    if not path.is_file():
        raise Unavailable(f"no Marqo export at {path}")
    import onnxruntime as ort
    from export_marqo_onnx import marqo_pixels

    settings = ort.SessionOptions()
    settings.intra_op_num_threads = options.ort_threads
    settings.inter_op_num_threads = 1
    started = time.perf_counter()
    session = ort.InferenceSession(str(path), settings, providers=["CPUExecutionProvider"])
    stage.load_seconds = time.perf_counter() - started
    name = session.get_inputs()[0].name
    images = pil_images(previews)
    for chunk in batches(images, options.batch_size):
        with timed(stage.samples, len(chunk)):
            session.run(None, {name: marqo_pixels(list(chunk))})
    stage.note = f"onnxruntime, {options.ort_threads} threads, batch {options.batch_size}"


def run_doc_docling(stage: Stage, previews: Sequence[Preview], options: argparse.Namespace) -> None:
    try:
        from immich_memories.analysis.editorial_preparation_detectors import Docling
    except ImportError as exc:
        raise Unavailable(f"detector dependencies missing: {exc}")
    try:
        started = time.perf_counter()
        detector = Docling(
            allow_downloads=options.allow_downloads, cache_dir=options.detector_cache
        )
    except Exception as exc:
        raise Unavailable(f"{type(exc).__name__}: {exc}")
    stage.load_seconds = time.perf_counter() - started
    images = pil_images(previews)
    for chunk in batches(images, options.batch_size):
        with timed(stage.samples, len(chunk)):
            detector.batch(list(chunk))
    stage.note = f"onnxruntime, producer's own 6 threads, batch {options.batch_size}"


def caption_once(base_url: str, tile: bytes, timeout: float) -> dict[str, Any]:
    from immich_memories.analysis.editorial_description_contract import validate_envelope
    from immich_memories.analysis.editorial_description_wire import request_bytes

    wire = request_bytes(tile)
    request = urllib.request.Request(  # noqa: S310 — operator-supplied benchmark endpoint
        f"{base_url}/chat/completions", data=wire, headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = json.loads(response.read())
    elapsed = time.perf_counter() - started
    choice = body["choices"][0]
    content = choice["message"]["content"]
    usage = body.get("usage") or {}
    try:
        envelope = validate_envelope(json.loads(content))
        text = f"{envelope.description} | {envelope.setting}"
        valid = True
    except Exception as exc:
        text, valid = f"INVALID {type(exc).__name__}: {content[:200]}", False
    return {
        "seconds": round(elapsed, 3),
        "request_bytes": len(wire),
        "finish_reason": choice.get("finish_reason"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "valid_envelope": valid,
        "text": text,
    }


def run_caption(stage: Stage, previews: Sequence[Preview], options: argparse.Namespace) -> None:
    from immich_memories.analysis.editorial_description_wire import tile_preview

    if not options.caption_base_url:
        raise Unavailable("no --caption-base-url given")
    base_url = options.caption_base_url.rstrip("/")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=60) as response:  # noqa: S310
            models = [row.get("id") for row in json.loads(response.read()).get("data", [])]
    except Exception as exc:
        raise Unavailable(f"{type(exc).__name__} on {base_url}/models: {exc}")
    stage.load_seconds = time.perf_counter() - started
    tiles = [(name, tile_preview(payload)) for name, payload in previews]
    concurrency = max(1, options.caption_concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for wave in batches(tiles, concurrency):
            started = time.perf_counter()
            futures = [
                pool.submit(caption_once, base_url, tile, options.caption_timeout)
                for _name, tile in wave
            ]
            outcomes = []
            for (name, _tile), future in zip(wave, futures, strict=True):
                try:
                    outcomes.append({"image": name, **future.result()})
                except Exception as exc:
                    outcomes.append({"image": name, "error": f"{type(exc).__name__}: {exc}"})
            # Concurrency buys throughput, not latency: charge the wave's wall clock.
            per_picture = (time.perf_counter() - started) / len(wave)
            stage.samples.extend([per_picture] * sum("error" not in o for o in outcomes))
            stage.calls.extend(outcomes)
    served = ", ".join(str(m) for m in models)
    stage.note = f"serving {served}; concurrency {concurrency}"


RUNNERS: dict[str, Callable[[Stage, Sequence[Preview], Any], None]] = {
    "preview_decode": run_preview_decode,
    "jpeg_decode": run_jpeg_decode,
    "pixel_facts": run_pixel_facts,
    "thumbnail_hash": run_thumbnail_hash,
    "caption_tile": run_caption_tile,
    "dino_preprocess": run_dino_preprocess,
    "dino_embed": run_dino_embed,
    "heads": run_heads,
    "nsfw_marqo": run_nsfw_marqo,
    "nsfw_marqo_onnx": run_nsfw_marqo_onnx,
    "doc_docling": run_doc_docling,
    "caption": run_caption,
}


def high_water_rss_mb() -> float:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, Darwin bytes.
    return peak / 1024 if sys.platform.startswith("linux") else peak / (1024 * 1024)


def machine() -> dict[str, Any]:
    facts: dict[str, Any] = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    if sys.platform == "darwin":
        with suppress(Exception):
            facts["processor"] = (
                subprocess.run(  # noqa: S603
                    ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                or facts["processor"]
            )
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(errors="ignore").splitlines():
            if line.startswith("model name"):
                facts["processor"] = line.split(":", 1)[1].strip()
            if line.startswith("flags"):
                have = set(line.split(":", 1)[1].split())
                facts["simd"] = " ".join(
                    flag for flag in ("sse4_2", "avx", "avx2", "f16c", "avx512f") if flag in have
                )
                break
    return facts


def apply_knobs(options: argparse.Namespace) -> None:
    """Thread and cache environment must be set before the first session or torch op."""
    os.environ["OMP_NUM_THREADS"] = str(options.torch_threads)
    if options.detector_cache:
        os.environ["HF_HUB_CACHE"] = str(Path(options.detector_cache).expanduser())
    if not options.allow_downloads:
        os.environ["HF_HUB_OFFLINE"] = "1"


def measure(options: argparse.Namespace, previews: Sequence[Preview]) -> list[Stage]:
    results = []
    for name in options.stages:
        cold, stage = Stage(name=name), Stage(name=name)
        try:
            RUNNERS[name](cold, previews, options)
            for _cycle in range(options.repeat):
                RUNNERS[name](stage, previews, options)
        except Unavailable as exc:
            stage.unavailable = str(exc)
        except Exception as exc:  # one broken producer must not void the other nine
            stage.unavailable = f"{type(exc).__name__}: {exc}"
        stage.load_seconds = stage.load_seconds or cold.load_seconds
        stage.note = stage.note or cold.note
        if cold.samples and stage.samples and name in COLD_ONLY_STAGES:
            repeat = statistics.mean(stage.samples)
            stage.note = f"{stage.note}; repeat pass {repeat:.2f} s/pic IS THE ENDPOINT CACHE"
            stage.samples, stage.calls = cold.samples, cold.calls
        elif cold.samples and stage.samples:
            stage.note = f"{stage.note}; cold pass {statistics.mean(cold.samples):.4f} s/pic"
            stage.calls = stage.calls or cold.calls
        else:
            stage.calls = stage.calls or cold.calls
        stage.rss_mb = high_water_rss_mb()
        results.append(stage)
        print(
            f"  {name:<16} {stage.seconds_per_picture:8.4f} s/pic  {stage.unavailable}", flush=True
        )
    return results


def producer_total(stages: Sequence[Stage]) -> float:
    return sum(
        s.seconds_per_picture
        for s in stages
        if not s.unavailable and s.name not in DIAGNOSTIC_STAGES
    )


def render_table(stages: Sequence[Stage]) -> str:
    total = producer_total(stages)
    lines = [
        "| stage | s/picture | p90 | load s | RSS MB | share | note |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for stage in stages:
        if stage.unavailable:
            lines.append(f"| {stage.name} | — | — | — | — | — | UNAVAILABLE: {stage.unavailable} |")
            continue
        diagnostic = stage.name in DIAGNOSTIC_STAGES
        share = "—" if diagnostic else f"{100 * stage.seconds_per_picture / total:.1f}%"
        lines.append(
            f"| {stage.name} | {stage.seconds_per_picture:.4f} | {stage.p90_seconds:.4f} | "
            f"{stage.load_seconds:.2f} | {stage.rss_mb:.0f} | {share} | {stage.note} |"
        )
    lines.append(f"| **total** | **{total:.4f}** | | | | 100% | one picture, every producer |")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, default=DEFAULT_IMAGES)
    parser.add_argument("--repeat", type=int, default=3, help="measured cycles after a cold pass")
    parser.add_argument("--stages", nargs="+", default=list(ALL_STAGES), choices=ALL_STAGES)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--ort-threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--torch-threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--provider", default="auto", choices=("auto", "cpu", "coreml"))
    parser.add_argument("--encoder", default=DEFAULT_ENCODER)
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE / "public-6heads-v3.npz"))
    parser.add_argument("--detector-cache", default="")
    parser.add_argument("--marqo-onnx", default="")
    parser.add_argument("--allow-downloads", action="store_true")
    parser.add_argument("--caption-base-url", default="")
    parser.add_argument("--caption-timeout", type=float, default=600.0)
    parser.add_argument("--caption-concurrency", type=int, default=1)
    parser.add_argument("--label", default="", help="how this run is named in the results file")
    parser.add_argument("--json", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    apply_knobs(options)
    previews = load_previews(Path(options.images).expanduser())
    facts = machine()
    print(f"{facts['processor']} — {facts.get('simd', 'n/a')} — {len(previews)} images", flush=True)
    stages = measure(options, previews)
    print("\n" + render_table(stages))
    payload = {
        "label": options.label,
        "machine": facts,
        "knobs": {
            "batch_size": options.batch_size,
            "ort_threads": options.ort_threads,
            "torch_threads": options.torch_threads,
            "provider": options.provider,
            "caption_concurrency": options.caption_concurrency,
            "repeat": options.repeat,
        },
        "images": [name for name, _ in previews],
        "stages": [stage.row() for stage in stages],
        "seconds_per_picture_total": round(producer_total(stages), 4),
    }
    if options.json:
        Path(options.json).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {options.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
