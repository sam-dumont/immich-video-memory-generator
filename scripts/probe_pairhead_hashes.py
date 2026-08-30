#!/usr/bin/env python3
"""Compute a perceptual hash for every asset in the pairwise-head bank.

Lever 3 (rendering-family / near-duplicate feature) of the pairhead cascade.
Reuses the repo's existing aHash implementation
(``immich_memories.analysis.duplicate_hashing.compute_thumbnail_hash`` +
``hamming_distance``) rather than reimplementing dHash -- it is standalone
(only cv2 + numpy) and already operates on thumbnail JPEG bytes, which is
exactly what the flat ``previews/`` dump and the shared thumbnail cache hold.

Image resolution mirrors ``probe_pairhead_embed.py``'s ``resolve_image_path``:
flat matrix preview first, then the shared (read-only) thumbnail cache.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_pairhead_embed import resolve_image_path  # noqa: E402

from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash  # noqa: E402

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
DEFAULT_CACHE_DIR = Path.home() / ".immich-memories" / "cache" / "thumbnails"
HASH_SIZE = 8  # 64-bit hash, matching the pairhead embedding's bit budget for a cheap feature


def _within_matrix(path: Path) -> bool:
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    try:
        path.resolve().relative_to(matrix)
    except ValueError:
        return False
    return True


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    if not _within_matrix(args.cache_dir) and args.cache_dir != DEFAULT_CACHE_DIR:
        parser.error(
            "--cache-dir must be the shared thumbnail cache or inside ~/.immich-memories-matrix"
        )
    return args


def hash_all(
    asset_ids: list[str], previews_dir: Path, cache_dir: Path
) -> tuple[dict[str, str], list[str]]:
    hashes: dict[str, str] = {}
    failed: list[str] = []
    for asset_id in asset_ids:
        try:
            path = resolve_image_path(asset_id, previews_dir, cache_dir)
            digest = compute_thumbnail_hash(path.read_bytes(), hash_size=HASH_SIZE)
        except (FileNotFoundError, OSError):
            failed.append(asset_id)
            continue
        if not digest:
            failed.append(asset_id)
            continue
        hashes[asset_id] = digest
    return hashes, failed


def main() -> int:
    args = _arguments()
    asset_ids: list[str] = json.loads((args.matrix_dir / "ids.json").read_text())

    started = time.monotonic()
    hashes, failed = hash_all(asset_ids, args.matrix_dir / "previews", args.cache_dir)
    elapsed = time.monotonic() - started

    coverage = len(hashes) / len(asset_ids)
    print(
        f"hashed {len(hashes)}/{len(asset_ids)} ({coverage:.1%}) assets in {elapsed:.1f}s, "
        f"{len(failed)} failed",
        flush=True,
    )

    payload = {
        "schema_version": "pairhead-hashes-v1",
        "bank_size": len(asset_ids),
        "hash_algorithm": f"aHash-{HASH_SIZE}x{HASH_SIZE} (duplicate_hashing.compute_thumbnail_hash)",
        "coverage_fraction": coverage,
        "elapsed_seconds": elapsed,
        "hashes": hashes,
        "failed_ids": failed,
    }
    (args.matrix_dir / "hashes.json").write_text(json.dumps(payload))
    print(f"wrote hashes.json ({coverage:.1%} coverage)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
