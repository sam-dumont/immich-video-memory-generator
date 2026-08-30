#!/usr/bin/env python3
"""Fetch a taken-at timestamp for every asset in the pairwise-head bank.

Lever 3 (time-delta feature) of the pairhead cascade needs a taken-at per
asset for all 6,841 bank ids (``ids.json``). Two local sources exist under
``smart-edit-consistency-v23-2026-08-30/*/cards.json`` (year-2007 and
year-2011 replay cases) but only cover ~17% of the bank, and at card
granularity: a multi-asset moment card carries one ``taken_at`` for every
asset it groups, not a true per-asset value. Rather than mix that coarser
source with exact per-asset values, this script checks local coverage (and
reports it) but then fetches ``fileCreatedAt`` for every bank asset from the
live Immich API (metadata-only ``GET /assets/{id}``, concurrency 4), matching
the field the rest of the codebase uses for ``taken_at``
(``selection_source.py``: ``taken_at=asset.file_created_at``).

Read-only: this script never writes to Immich or to the shared thumbnail
cache. Output goes to ``timestamps.json`` inside the matrix dir only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
CONSISTENCY_DIR = Path.home() / ".immich-memories-matrix" / "smart-edit-consistency-v23-2026-08-30"
CONFIG_PATH = Path.home() / ".immich-memories" / "config.yaml"
CONCURRENCY = 4
MAX_RETRIES = 2


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
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    return args


def local_card_coverage(bank_ids: list[str]) -> dict[str, str]:
    """Asset id -> card-level taken_at, from every cards.json under the consistency dir.

    Reported for diligence only (see module docstring on why it is not used
    as the retrain's actual time source): it is coarse (one timestamp per
    multi-asset moment) and covers a small slice of the bank.
    """
    found: dict[str, str] = {}
    if not CONSISTENCY_DIR.exists():
        return found
    for cards_path in sorted(CONSISTENCY_DIR.glob("*/cards.json")):
        payload = json.loads(cards_path.read_text())
        for card in payload.get("cards", []):
            taken_at = card.get("taken_at")
            if not taken_at:
                continue
            for asset_id in card.get("asset_ids", []):
                found.setdefault(asset_id, taken_at)
    return {asset_id: taken_at for asset_id, taken_at in found.items() if asset_id in set(bank_ids)}


def _load_immich_credentials() -> tuple[str, str]:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    immich = config["immich"]
    return immich["url"].rstrip("/"), immich["api_key"]


async def _fetch_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, asset_id: str
) -> tuple[str, str | None, str | None]:
    """Return (asset_id, file_created_at_iso_or_None, error_or_None)."""
    async with semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.get(f"/api/assets/{asset_id}")
                if response.status_code == 200:
                    data = response.json()
                    return asset_id, data.get("fileCreatedAt"), None
                if response.status_code == 404:
                    return asset_id, None, "404 not found"
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                return asset_id, None, f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                return asset_id, None, f"{type(exc).__name__}: {exc}"
        return asset_id, None, "unreachable"


async def fetch_from_immich(asset_ids: list[str]) -> dict[str, dict[str, Any]]:
    base_url, api_key = _load_immich_credentials()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"x-api-key": api_key, "Accept": "application/json"},
        timeout=10.0,
    ) as client:
        tasks = [_fetch_one(client, semaphore, asset_id) for asset_id in asset_ids]
        for coro in asyncio.as_completed(tasks):
            asset_id, taken_at, error = await coro
            results[asset_id] = {"taken_at": taken_at, "error": error}
    return results


def main() -> int:
    args = _arguments()
    bank_ids: list[str] = json.loads((args.matrix_dir / "ids.json").read_text())

    local = local_card_coverage(bank_ids)
    local_fraction = len(local) / len(bank_ids)
    print(
        f"local cards.json coverage: {len(local)}/{len(bank_ids)} ({local_fraction:.1%}) "
        "-- card-level granularity, not used as the retrain source (see docstring)",
        flush=True,
    )

    started = time.monotonic()
    api_results = asyncio.run(fetch_from_immich(bank_ids))
    elapsed = time.monotonic() - started

    ok = {aid: r["taken_at"] for aid, r in api_results.items() if r["taken_at"] is not None}
    failed = {aid: r["error"] for aid, r in api_results.items() if r["taken_at"] is None}
    coverage_fraction = len(ok) / len(bank_ids)
    print(
        f"Immich API: {len(ok)}/{len(bank_ids)} ({coverage_fraction:.1%}) fetched OK, "
        f"{len(failed)} failed, {elapsed:.1f}s at concurrency {CONCURRENCY}",
        flush=True,
    )

    payload = {
        "schema_version": "pairhead-timestamps-v1",
        "bank_size": len(bank_ids),
        "local_card_coverage": {"count": len(local), "fraction": local_fraction},
        "immich_api": {
            "count_ok": len(ok),
            "count_failed": len(failed),
            "coverage_fraction": coverage_fraction,
            "elapsed_seconds": elapsed,
            "concurrency": CONCURRENCY,
        },
        "source": "immich_api_file_created_at",
        "timestamps": ok,
        "failed_ids": list(failed.keys()),
    }
    (args.matrix_dir / "timestamps.json").write_text(json.dumps(payload))
    print(f"wrote timestamps.json ({coverage_fraction:.1%} coverage)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
