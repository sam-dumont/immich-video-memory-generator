#!/usr/bin/env python3
"""Measure whether packing AlbumBench images cuts calls without losing selection quality.

This is deliberately a small ablation, not an AlbumBench leaderboard runner.  It
keeps the albums, intent labels, prompt, image size, and model fixed, then changes
only how many images are attached to one request.  The useful answer for the
editor is the quality/call-count curve for packs of 1, 4, and 6 images.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image, ImageOps

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_query import LLMTransportAttempt, query_llm
from immich_memories.analysis.strict_json import final_json_object
from immich_memories.config_loader import get_config
from immich_memories.config_models_llm import LLMConfig

SELECTION_SCHEMA = "albumbench-packed-intent-selection-v1"
DEFAULT_EVENTS = (
    "CasualFamilyGather",
    "BeachTrip",
    "Birthday",
    "NatureTrip",
    "Wedding",
    "UrbanTrip",
)
HF_IMAGE_ROOT = "https://huggingface.co/datasets/Shawn-Huang/CUFED-AlbumBench/resolve/main/images/"

T = TypeVar("T")


@dataclass(frozen=True)
class ProbeImage:
    image_id: str
    path: str


@dataclass(frozen=True)
class ProbeTask:
    album_id: str
    event_type: str
    task_id: str
    prompt: str
    images: tuple[ProbeImage, ...]
    expected_selected: frozenset[str]


def batches(values: Sequence[T], size: int) -> tuple[tuple[T, ...], ...]:
    """Split a sequence without changing its AlbumBench order."""
    if size < 1:
        raise ValueError("batch size must be positive")
    return tuple(tuple(values[index : index + size]) for index in range(0, len(values), size))


def request_count(count: int, size: int) -> int:
    """Return the nominal request count for one album and one packing size."""
    if count < 0:
        raise ValueError("image count cannot be negative")
    if size < 1:
        raise ValueError("batch size must be positive")
    return math.ceil(count / size)


def _balanced_distance(task: dict[str, Any]) -> tuple[float, str]:
    image_ids = task.get("image_ids", [])
    selected = task.get("target", {}).get("selected_images", [])
    rate = len(selected) / len(image_ids) if image_ids else 0.0
    return abs(rate - 0.45), str(task.get("task_id", ""))


def select_probe_tasks(
    albums: Iterable[dict[str, Any]],
    tasks: Iterable[dict[str, Any]],
    *,
    event_types: Sequence[str] = DEFAULT_EVENTS,
) -> tuple[ProbeTask, ...]:
    """Choose one small test album and one balanced intent query per event type."""
    album_rows = tuple(albums)
    task_rows = tuple(tasks)
    selected: list[ProbeTask] = []
    for event_type in event_types:
        candidates = [
            row
            for row in album_rows
            if row.get("event_type") == event_type
            and row.get("metadata", {}).get("split") == "test"
        ]
        if not candidates:
            raise ValueError(f"no test album for event type {event_type}")
        album = min(candidates, key=lambda row: (int(row.get("num_images", 0)), row["album_id"]))
        album_tasks = [
            row
            for row in task_rows
            if row.get("album_id") == album["album_id"]
            and row.get("task_type") == "intent_selection"
            and row.get("image_ids")
        ]
        if not album_tasks:
            raise ValueError(f"no intent-selection task for album {album['album_id']}")
        task = min(album_tasks, key=_balanced_distance)
        image_by_id = {
            str(row["image_id"]): ProbeImage(str(row["image_id"]), str(row["path"]))
            for row in album["images"]
        }
        ordered_images = tuple(image_by_id[str(image_id)] for image_id in task["image_ids"])
        selected.append(
            ProbeTask(
                album_id=str(album["album_id"]),
                event_type=event_type,
                task_id=str(task["task_id"]),
                prompt=str(task["prompt"]),
                images=ordered_images,
                expected_selected=frozenset(
                    str(image_id) for image_id in task["target"]["selected_images"]
                ),
            )
        )
    return tuple(selected)


def read_selection(raw: str, valid_aliases: Sequence[str]) -> tuple[str, ...]:
    """Read one exact, grounded selection envelope without coercion or guessing."""
    payload = final_json_object(raw)
    if (
        payload is None
        or set(payload) != {"schema_version", "selected"}
        or payload.get("schema_version") != SELECTION_SCHEMA
        or not isinstance(payload.get("selected"), list)
        or not all(isinstance(value, str) for value in payload["selected"])
    ):
        raise ValueError("selection is not the exact JSON envelope")
    selected = tuple(payload["selected"])
    if len(selected) != len(set(selected)):
        raise ValueError("selected aliases must be unique")
    valid = frozenset(valid_aliases)
    if not set(selected).issubset(valid):
        raise ValueError("selected aliases must be grounded in the attached images")
    order = {alias: index for index, alias in enumerate(valid_aliases)}
    if tuple(sorted(selected, key=order.__getitem__)) != selected:
        raise ValueError("selected aliases must preserve image order")
    return selected


def selection_metrics(
    *,
    all_ids: Sequence[str],
    expected: frozenset[str],
    predicted: frozenset[str],
) -> dict[str, Any]:
    """Return the full binary confusion matrix and useful derived scores."""
    universe = frozenset(all_ids)
    if not expected.issubset(universe) or not predicted.issubset(universe):
        raise ValueError("expected and predicted IDs must belong to the album")
    true_positive = len(expected & predicted)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    true_negative = len(universe - expected - predicted)
    precision = true_positive / (true_positive + false_positive) if predicted else 0.0
    recall = true_positive / (true_positive + false_negative) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (true_positive + true_negative) / len(universe) if universe else 1.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "exact": expected == predicted,
    }


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(line) for line in path.read_text().splitlines() if line.strip())


def _download_image(relative_path: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = HF_IMAGE_ROOT + urllib.parse.quote(relative_path, safe="/") + "?download=true"
    request = urllib.request.Request(  # noqa: S310 - URL is built from the fixed HTTPS host above
        url, headers={"User-Agent": "immich-memories-albumbench-probe"}
    )
    temporary_path: Path | None = None
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,  # noqa: S310
            tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary,
        ):
            temporary.write(response.read())
            temporary_path = Path(temporary.name)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def prepare_images(tasks: Sequence[ProbeTask], image_root: Path, *, download: bool) -> int:
    """Ensure the public sample is present, downloading only missing images."""
    downloaded = 0
    for task in tasks:
        for image in task.images:
            destination = image_root / image.path
            if destination.is_file():
                continue
            if not download:
                raise FileNotFoundError(
                    f"missing {destination}; rerun once with --download-missing"
                )
            _download_image(image.path, destination)
            downloaded += 1
    return downloaded


def _jpeg_bytes(path: Path, *, max_edge: int) -> bytes:
    """Use the same bounded-thumbnail premise as the smart editor."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        with tempfile.SpooledTemporaryFile() as output:
            image.save(output, format="JPEG", quality=88, optimize=True)
            output.seek(0)
            return output.read()


def _prompt(intent: str, aliases: Sequence[str], *, retry: bool) -> str:
    retry_note = (
        "Your previous answer was invalid. Obey the exact JSON contract this time.\n"
        if retry
        else ""
    )
    return f"""{retry_note}You are judging an AlbumBench intent-selection task.

INTENT
{intent}

The attached images correspond in order to these aliases:
{json.dumps(list(aliases), separators=(",", ":"))}

Select every attached image that clearly matches the intent. Judge each image
independently; do not select an image merely because its neighbors match.

Return only one complete JSON object with exactly these keys:
{{"schema_version":"{SELECTION_SCHEMA}","selected":["ALIAS"]}}
Use an empty selected list when none match. Preserve alias order."""


def _sum_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    numeric_keys = {
        key
        for row in rows
        for key, value in row.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return {key: sum(row.get(key, 0) for row in rows) for key in sorted(numeric_keys)}


def _micro_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(int(row["true_positive"]) for row in rows)
    fp = sum(int(row["false_positive"]) for row in rows)
    fn = sum(int(row["false_negative"]) for row in rows)
    tn = sum(int(row["true_negative"]) for row in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "accuracy": (tp + tn) / (tp + fp + fn + tn),
        "exact_albums": sum(bool(row["exact"]) for row in rows),
        "albums": len(rows),
    }


async def _judge_chunk(
    *,
    task: ProbeTask,
    chunk: Sequence[ProbeImage],
    image_root: Path,
    config: LLMConfig,
    max_edge: int,
    timeout_seconds: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    aliases = tuple(f"I{image.image_id}" for image in chunk)
    images = tuple(_jpeg_bytes(image_root / image.path, max_edge=max_edge) for image in chunk)
    attempts: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    started = time.monotonic()
    selected: tuple[str, ...] | None = None
    call_metrics: list[dict[str, Any]] = []
    async with semaphore:
        for retry in (False, True):
            transport: list[LLMTransportAttempt] = []
            with llm_metrics.collecting() as counters:
                raw = await query_llm(
                    _prompt(task.prompt, aliases, retry=retry),
                    config,
                    temperature=0.0,
                    max_tokens=1024,
                    timeout_seconds=timeout_seconds,
                    thinking=False,
                    images=images,
                    image_detail="high",
                    transport_observer=transport.append,
                    require_complete=True,
                )
            call_metrics.append(counters.as_metrics())
            attempts.extend(asdict(row) for row in transport)
            try:
                selected = read_selection(raw, aliases)
                break
            except ValueError as error:
                parse_errors.append(str(error))
    return {
        "image_ids": [image.image_id for image in chunk],
        "selected_ids": [alias.removeprefix("I") for alias in selected or ()],
        "status": "complete" if selected is not None else "invalid",
        "parse_errors": parse_errors,
        "transport_attempts": attempts,
        "metrics": _sum_metrics(call_metrics),
        "elapsed_seconds": time.monotonic() - started,
    }


async def _run_size(
    tasks: Sequence[ProbeTask],
    *,
    pack_size: int,
    image_root: Path,
    config: LLMConfig,
    max_edge: int,
    timeout_seconds: int,
    concurrency: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    pending: list[tuple[ProbeTask, asyncio.Task[dict[str, Any]]]] = []
    for task in tasks:
        for chunk in batches(task.images, pack_size):
            pending.append(
                (
                    task,
                    asyncio.create_task(
                        _judge_chunk(
                            task=task,
                            chunk=chunk,
                            image_root=image_root,
                            config=config,
                            max_edge=max_edge,
                            timeout_seconds=timeout_seconds,
                            semaphore=semaphore,
                        )
                    ),
                )
            )
    chunk_results = await asyncio.gather(*(job for _, job in pending))
    grouped: dict[str, list[dict[str, Any]]] = {task.task_id: [] for task in tasks}
    for (task, _job), result in zip(pending, chunk_results, strict=True):
        grouped[task.task_id].append(result)

    task_results: list[dict[str, Any]] = []
    for task in tasks:
        chunks = grouped[task.task_id]
        predicted = frozenset(image_id for chunk in chunks for image_id in chunk["selected_ids"])
        metrics = selection_metrics(
            all_ids=tuple(image.image_id for image in task.images),
            expected=task.expected_selected,
            predicted=predicted,
        )
        task_results.append(
            {
                "album_id": task.album_id,
                "event_type": task.event_type,
                "task_id": task.task_id,
                "image_count": len(task.images),
                "expected_selected_count": len(task.expected_selected),
                "predicted_selected_count": len(predicted),
                "quality": metrics,
                "chunks": chunks,
            }
        )
    request_metrics = [chunk["metrics"] for chunk in chunk_results]
    return {
        "pack_size": pack_size,
        "nominal_requests": sum(request_count(len(task.images), pack_size) for task in tasks),
        "actual_model_replies": sum(int(row.get("llm_calls", 0)) for row in request_metrics),
        "invalid_chunks": sum(chunk["status"] != "complete" for chunk in chunk_results),
        "quality": _micro_metrics([row["quality"] for row in task_results]),
        "metrics": _sum_metrics(request_metrics),
        "elapsed_seconds": time.monotonic() - started,
        "tasks": task_results,
    }


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--image-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--events", nargs="+", default=list(DEFAULT_EVENTS))
    parser.add_argument("--pack-sizes", nargs="+", type=int, default=[1, 4, 6])
    parser.add_argument("--model")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--max-edge", type=int, default=768)
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument(
        "--json-object",
        action="store_true",
        help="Ask an OpenAI-compatible server for JSON mode in addition to the prompt contract",
    )
    args = parser.parse_args()
    if args.out.exists():
        parser.error(f"output already exists: {args.out}")
    if args.concurrency < 1 or args.max_edge < 128 or any(size < 1 for size in args.pack_sizes):
        parser.error("concurrency and pack sizes must be positive; max edge must be >= 128")
    for name in ("albums.jsonl", "tasks.jsonl"):
        if not (args.annotations / name).is_file():
            parser.error(f"missing {args.annotations / name}")
    return args


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    albums = _read_jsonl(args.annotations / "albums.jsonl")
    task_rows = _read_jsonl(args.annotations / "tasks.jsonl")
    tasks = select_probe_tasks(albums, task_rows, event_types=tuple(args.events))
    downloaded = prepare_images(tasks, args.image_root, download=args.download_missing)
    llm = get_config().llm
    updates: dict[str, Any] = {}
    if args.model:
        updates["model"] = args.model
    if args.json_object:
        updates["extra_params"] = {
            **llm.extra_params,
            "response_format": {"type": "json_object"},
        }
    config = llm.model_copy(update=updates) if updates else llm
    started = time.monotonic()
    runs = []
    for pack_size in dict.fromkeys(args.pack_sizes):
        run = await _run_size(
            tasks,
            pack_size=pack_size,
            image_root=args.image_root,
            config=config,
            max_edge=args.max_edge,
            timeout_seconds=args.timeout_seconds,
            concurrency=args.concurrency,
        )
        runs.append(run)
        print(
            f"pack {pack_size}: F1 {run['quality']['f1']:.3f}, "
            f"{run['actual_model_replies']} replies in {run['elapsed_seconds']:.1f}s",
            flush=True,
        )
    return {
        "schema_version": "albumbench-packing-ablation-v1",
        "purpose": "call-count ablation only; not an official AlbumBench leaderboard result",
        "configuration": {
            "provider": config.provider,
            "base_url": config.base_url,
            "model": config.model,
            "thinking": False,
            "temperature": 0.0,
            "json_object": args.json_object,
            "concurrency": args.concurrency,
            "max_edge": args.max_edge,
            "event_types": list(args.events),
            "pack_sizes": list(dict.fromkeys(args.pack_sizes)),
        },
        "sample": [
            {
                "album_id": task.album_id,
                "event_type": task.event_type,
                "task_id": task.task_id,
                "image_count": len(task.images),
                "positive_count": len(task.expected_selected),
            }
            for task in tasks
        ],
        "downloaded_images": downloaded,
        "elapsed_seconds": time.monotonic() - started,
        "runs": runs,
    }


def main() -> int:
    args = _arguments()
    result = asyncio.run(_run(args))
    _atomic_json(args.out, result)
    return 0 if all(run["invalid_chunks"] == 0 for run in result["runs"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
