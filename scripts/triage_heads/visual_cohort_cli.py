#!/usr/bin/env python3
"""Build a label-blind, visually diverse provisional certification cohort."""

from __future__ import annotations

import argparse
from pathlib import Path

from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.config_loader import get_config

from .cohorts import TruthReservationLedger, load_inventory_snapshot
from .memory import acquire_recovery_pipeline_lock
from .visual_cohort import (
    DEFAULT_CANDIDATE_SURPLUS,
    DEFAULT_CANDIDATE_TARGET,
    DEFAULT_FINAL_TARGET,
    MAX_VISUAL_PROCESS_BYTES,
    ThreadLocalPreviewFetcher,
    VisualCohortConfig,
    build_provisional_visual_cohort,
)

DEFAULT_RECOVERY_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads" / "recovery-1b"
DEFAULT_INVENTORY_DIR = DEFAULT_RECOVERY_DIR / "full-image-inventory-v1"
DEFAULT_LEDGER = DEFAULT_RECOVERY_DIR / "truth-reservations.sqlite"
DEFAULT_OUTPUT_DIR = DEFAULT_RECOVERY_DIR / "fresh-location-cert-provisional-v2"


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory-dir", type=Path, default=DEFAULT_INVENTORY_DIR)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", default="location-recovery-1b-visual-v1")
    parser.add_argument("--candidate-target", type=int, default=DEFAULT_CANDIDATE_TARGET)
    parser.add_argument("--candidate-surplus", type=int, default=DEFAULT_CANDIDATE_SURPLUS)
    parser.add_argument("--final-target", type=int, default=DEFAULT_FINAL_TARGET)
    parser.add_argument("--cluster-count", type=int, default=64)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--memory-limit-gib", type=float, default=8.0)
    parser.add_argument("--nearest-pairs", type=int, default=32)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        _pipeline_lock = acquire_recovery_pipeline_lock()
        if not args.ledger.is_file():
            raise RuntimeError(
                "truth ledger is missing; import prior truth before sampling a new cohort"
            )
        memory_limit_bytes = int(args.memory_limit_gib * 1024**3)
        if memory_limit_bytes > MAX_VISUAL_PROCESS_BYTES:
            raise ValueError("--memory-limit-gib cannot exceed 8")
        config = VisualCohortConfig(
            seed=args.seed,
            candidate_target=args.candidate_target,
            candidate_surplus=args.candidate_surplus,
            final_target=args.final_target,
            cluster_count=args.cluster_count,
            workers=args.workers,
            memory_limit_bytes=memory_limit_bytes,
            nearest_pair_count=args.nearest_pairs,
        )
        snapshot = load_inventory_snapshot(args.inventory_dir)
        ledger = TruthReservationLedger(args.ledger)
        app_config = get_config()
        if not app_config.immich.url or not app_config.immich.api_key:
            raise RuntimeError("Immich URL and API key must be configured")

        def progress(completed: int, total: int) -> None:
            print(f"candidate previews: {completed}/{total}", flush=True)

        with ThreadLocalPreviewFetcher(
            lambda: SyncImmichClient(
                base_url=app_config.immich.url,
                api_key=app_config.immich.api_key,
            )
        ) as fetch_preview:
            run = build_provisional_visual_cohort(
                snapshot=snapshot,
                ledger=ledger,
                output_dir=args.output_dir,
                fetch_preview=fetch_preview,
                config=config,
                progress=progress,
            )
        audit = run.visual_selection.audit
        print(
            "provisional visual cohort ready: "
            f"selected={audit.temporal.selected_count} "
            f"cluster_coverage={audit.cluster_coverage:.3f} "
            f"cluster_entropy={audit.normalized_cluster_entropy:.3f} "
            "truth_reserved=no (inspect blind-review first)",
            flush=True,
        )
        return 0
    except (MemoryError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"STOP: {error}") from error


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
