#!/usr/bin/env python3
"""Label a PUBLIC image corpus with openjev, using the product's own eleven questions.

Teacher = the local openjev server (`OPENJEV_BACKEND=mlx python -m openjev`).
Student = a linear head on frozen DINOv2 packs, exactly like the shipped triage heads.

Only public images go through here. The owner's library is never labelled by this script.

    python label_public.py --images <dir> [<dir> ...] --out public-labels.jsonl

Write-ahead JSONL, resumable: a row already in the file is never re-asked.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import QUESTIONS, STATE, question_set_hash  # noqa: E402

READER_MODEL = "openjev-latest"
PRODUCER = f"picture-facts-v1@openjev/{question_set_hash(QUESTIONS)}"


def picture_tile(path: Path) -> bytes:
    """The product's own tile: 800 px long edge, JPEG q90 — same bytes the pipeline sends."""
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((800, 800), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def ask(base_url: str, tile: bytes, timeout: float = 300.0) -> dict:
    payload = {
        "model": READER_MODEL,
        "state": STATE,
        "images": ["data:image/jpeg;base64," + base64.b64encode(tile).decode()],
        "questions": QUESTIONS,
    }
    request = urllib.request.Request(  # noqa: S310
        f"{base_url.rstrip('/')}/systemone",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


def read_answers(reply: dict) -> dict:
    answers = reply["answers"]
    read: dict = {}
    for name, question in QUESTIONS.items():
        answer = answers[name]
        if question["type"] == "noul":
            read[name] = round(float(answer["noul"]), 4)
        else:
            read[name] = {
                "choice": answer["choice"],
                "probabilities": {
                    k: round(float(v), 4)
                    for k, v in answer["probabilities"].items()
                    if k in question["criteria"]
                },
            }
    return read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--images", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", type=Path, default=None, help="newline-separated image ids")
    args = parser.parse_args(argv)

    wanted: set[str] | None = None
    if args.only:
        wanted = {line.strip() for line in args.only.read_text().splitlines() if line.strip()}

    files: dict[str, Path] = {}
    for directory in args.images:
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".jpg"):
                continue
            image_id = name[:-4]
            if wanted is not None and image_id not in wanted:
                continue
            files.setdefault(image_id, directory / name)

    done: set[str] = set()
    if args.out.exists():
        with args.out.open() as handle:
            for line in handle:
                line = line.strip()
                if line:
                    done.add(json.loads(line)["image_id"])
    todo = [(k, v) for k, v in files.items() if k not in done]
    # Shuffled, so a run that has to be cut short still leaves a corpus with every
    # source folder in it rather than one folder's worth of pictures.
    random.Random(42).shuffle(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(files)} images, {len(done)} already labelled, {len(todo)} to ask", flush=True)

    started = time.monotonic()
    written = 0
    with args.out.open("a") as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:

        def work(item: tuple[str, Path]) -> dict:
            image_id, path = item
            try:
                tile = picture_tile(path)
            except (OSError, ValueError) as exc:
                return {"image_id": image_id, "status": "unreadable", "reason": str(exc)}
            try:
                reply = ask(args.base_url, tile)
                return {
                    "image_id": image_id,
                    "status": "described",
                    "producer": PRODUCER,
                    "answers": read_answers(reply),
                    "usage": reply.get("usage"),
                }
            except Exception as exc:  # the row is retried on the next run
                return {
                    "image_id": image_id,
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }

        for row in pool.map(work, todo):
            if row["status"] == "failed":
                print(f"  ! {row['image_id']} {row['reason']}", flush=True)
                continue
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            written += 1
            if written % 200 == 0:
                rate = (time.monotonic() - started) / written
                left = (len(todo) - written) * rate / 60
                handle.flush()
                print(f"  {written}/{len(todo)} {rate:.2f}s/img  ~{left:.0f} min left", flush=True)
    elapsed = time.monotonic() - started
    print(
        json.dumps(
            {
                "written": written,
                "seconds": round(elapsed, 1),
                "seconds_per_image": round(elapsed / max(1, written), 3),
                "producer": PRODUCER,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
