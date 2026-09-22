#!/usr/bin/env python3
"""Seal the exact 20,000-image, label-blind owner training cohort."""

from __future__ import annotations

import argparse
from pathlib import Path

from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.config_loader import get_config

from .cohorts import TruthReservationLedger, load_inventory_snapshot
from .memory import acquire_recovery_pipeline_lock
from .training_cohort import (
    DEFAULT_ACQUISITION_TARGET,
    DEFAULT_MINIMUM_AVAILABLE,
    MAX_PROCESS_MEMORY_BYTES,
    TrainingCohortConfig,
    build_training_cohort,
    discover_v2_reuse_stores,
    require_fresh_truth_ready,
)
from .visual_cohort import ThreadLocalPreviewFetcher

DEFAULT_RECOVERY_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads" / "recovery-1b"
DEFAULT_INVENTORY_DIR = DEFAULT_RECOVERY_DIR / "full-image-inventory-v1"
DEFAULT_LEDGER = DEFAULT_RECOVERY_DIR / "truth-reservations.sqlite"
DEFAULT_APPROVAL_DIR = DEFAULT_RECOVERY_DIR / "fresh-location-cert-v3-approval"
DEFAULT_OUTPUT_DIR = DEFAULT_RECOVERY_DIR / "owner-training-cohort-v1"


def _minimum_available(value: str) -> int:
    parsed = int(value)
    if parsed < DEFAULT_MINIMUM_AVAILABLE:
        raise argparse.ArgumentTypeError(
            f"minimum available cannot be lower than {DEFAULT_MINIMUM_AVAILABLE}"
        )
    return parsed


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory-dir", type=Path, default=DEFAULT_INVENTORY_DIR)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--approval-dir", type=Path, default=DEFAULT_APPROVAL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", default="location-recovery-1b-training-v1")
    parser.add_argument("--acquisition-target", type=int, default=DEFAULT_ACQUISITION_TARGET)
    parser.add_argument(
        "--minimum-available",
        type=_minimum_available,
        default=DEFAULT_MINIMUM_AVAILABLE,
    )
    parser.add_argument("--cluster-count", type=int, default=128)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--memory-limit-gib", type=float, default=8.0)
    parser.add_argument("--review-sample-size", type=int, default=64)
    parser.add_argument(
        "--reuse-root",
        action="append",
        type=Path,
        default=[],
        help="search this private root for authenticated v2 candidate preview stores",
    )
    parser.add_argument(
        "--reuse-store",
        action="append",
        type=Path,
        default=[],
        help="authenticate and reuse this exact v2 candidate preview store",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        _pipeline_lock = acquire_recovery_pipeline_lock()
        if not args.ledger.is_file():
            raise RuntimeError("truth ledger is missing; training is forbidden")
        snapshot = load_inventory_snapshot(args.inventory_dir)
        ledger = TruthReservationLedger(args.ledger)
        memory_limit_bytes = int(args.memory_limit_gib * 1024**3)
        if memory_limit_bytes > MAX_PROCESS_MEMORY_BYTES:
            raise ValueError("--memory-limit-gib cannot exceed 8")
        config = TrainingCohortConfig(
            seed=args.seed,
            acquisition_target=args.acquisition_target,
            minimum_available=args.minimum_available,
            cluster_count=args.cluster_count,
            workers=args.workers,
            memory_limit_bytes=memory_limit_bytes,
            review_sample_size=args.review_sample_size,
        )
        require_fresh_truth_ready(
            snapshot=snapshot,
            ledger=ledger,
            config=config,
            approval_dir=args.approval_dir,
        )
        reuse_roots = tuple(args.reuse_root) or (DEFAULT_RECOVERY_DIR,)
        reuse_stores = tuple(
            dict.fromkeys((*discover_v2_reuse_stores(reuse_roots), *args.reuse_store))
        )

        app_config = get_config()
        if not app_config.immich.url or not app_config.immich.api_key:
            raise RuntimeError("Immich URL and API key must be configured")

        def progress(completed: int, total: int) -> None:
            if completed == 1 or completed == total or completed % 250 == 0:
                print(f"owner previews: {completed}/{total}", flush=True)

        with ThreadLocalPreviewFetcher(
            lambda: SyncImmichClient(
                base_url=app_config.immich.url,
                api_key=app_config.immich.api_key,
            )
        ) as fetch_preview:
            run = build_training_cohort(
                snapshot=snapshot,
                ledger=ledger,
                output_dir=args.output_dir,
                fetch_preview=fetch_preview,
                config=config,
                reuse_stores=reuse_stores,
                approval_dir=args.approval_dir,
                progress=progress,
            )
        audit = run.selection.audit
        print(
            "owner training cohort sealed: "
            f"selected={audit.temporal.selected_count} "
            f"days={audit.temporal.distinct_days} "
            f"screens={audit.selected_screen_signature_count}/"
            f"{audit.screen_signature_count_cap} "
            f"output={args.output_dir}",
            flush=True,
        )
        return 0
    except (MemoryError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"STOP: {error}") from error


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
