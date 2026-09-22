#!/usr/bin/env python3
"""Seal a full Immich image inventory, then build disjoint diverse cohorts."""

from __future__ import annotations

import argparse
from pathlib import Path

from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.config_loader import get_config

from .cohorts import (
    CohortSpec,
    TruthReservationLedger,
    load_inventory_snapshot,
    load_truth_asset_ids,
    reserve_truth_asset_ids,
    save_cohort_plan,
    save_inventory_snapshot,
    scan_live_image_inventory,
    select_certification_then_training,
)

DEFAULT_RECOVERY_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads" / "recovery-1b"
DEFAULT_INVENTORY_DIR = DEFAULT_RECOVERY_DIR / "full-image-inventory-v1"
DEFAULT_PLAN_DIR = DEFAULT_RECOVERY_DIR / "diverse-cohorts-v2"
DEFAULT_LEDGER = DEFAULT_RECOVERY_DIR / "truth-reservations.sqlite"


def _cohort_spec(prefix: str, args: argparse.Namespace) -> CohortSpec:
    return CohortSpec(
        target_size=getattr(args, f"{prefix}_target"),
        minimum_size=getattr(args, f"{prefix}_minimum"),
        max_per_day=getattr(args, f"{prefix}_max_per_day"),
        max_per_moment=getattr(args, f"{prefix}_max_per_moment"),
        min_distinct_days=getattr(args, f"{prefix}_min_days"),
        min_year_quarter_coverage=args.min_year_quarter_coverage,
        max_sqrt_quota_tv=args.max_sqrt_quota_tv,
    )


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cert-target", type=int, default=400)
    parser.add_argument("--cert-minimum", type=int, default=300)
    parser.add_argument("--cert-max-per-day", type=int, default=2)
    parser.add_argument("--cert-max-per-moment", type=int, default=1)
    parser.add_argument("--cert-min-days", type=int, default=300)
    parser.add_argument("--training-target", type=int, default=20_000)
    parser.add_argument("--training-minimum", type=int, default=16_000)
    parser.add_argument("--training-max-per-day", type=int, default=12)
    parser.add_argument("--training-max-per-moment", type=int, default=3)
    parser.add_argument("--training-min-days", type=int, default=1_000)
    parser.add_argument("--min-year-quarter-coverage", type=float, default=0.90)
    parser.add_argument("--max-sqrt-quota-tv", type=float, default=0.10)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="read and seal full owner image metadata")
    scan.add_argument("--output-dir", type=Path, default=DEFAULT_INVENTORY_DIR)
    scan.add_argument("--page-size", type=int, default=1000)

    plan = commands.add_parser("plan", help="reserve truth, then seal cert/train indices")
    plan.add_argument("--inventory-dir", type=Path, default=DEFAULT_INVENTORY_DIR)
    plan.add_argument("--output-dir", type=Path, default=DEFAULT_PLAN_DIR)
    plan.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    plan.add_argument("--seed", default="location-recovery-1b-v2")
    plan.add_argument("--legacy-truth", type=Path, action="append", default=[])
    plan.add_argument("--legacy-cohort-name", default="retired-location-cert-v1")
    plan.add_argument(
        "--allow-absent-type",
        choices=("VIDEO", "AUDIO", "OTHER"),
        default=None,
        help="explicitly classify every legacy ID absent from the IMAGE inventory",
    )
    plan.add_argument("--certification-cohort-name", default="fresh-location-cert-v2")
    _add_spec_arguments(plan)
    return parser.parse_args(argv)


def _scan(args: argparse.Namespace) -> int:
    config = get_config()
    if not config.immich.url or not config.immich.api_key:
        raise RuntimeError("Immich URL and API key must be configured")

    def progress(pages: int, seen: int, eligible: int) -> None:
        print(
            f"inventory scan: pages={pages} seen={seen} eligible={eligible}",
            flush=True,
        )

    with SyncImmichClient(
        base_url=config.immich.url,
        api_key=config.immich.api_key,
    ) as client:
        snapshot = scan_live_image_inventory(
            client,
            page_size=args.page_size,
            progress=progress,
        )
    manifest = save_inventory_snapshot(args.output_dir, snapshot)
    print(
        "inventory sealed: "
        f"assets={manifest['asset_count']} days={manifest['distinct_capture_days']} "
        f"inventory_sha256={manifest['inventory_sha256']}",
        flush=True,
    )
    return 0


def _plan(args: argparse.Namespace) -> int:
    snapshot = load_inventory_snapshot(args.inventory_dir)
    ledger = TruthReservationLedger(args.ledger)
    if args.legacy_truth:
        legacy_ids = load_truth_asset_ids(args.legacy_truth)
        snapshot_ids = {row.asset_id for row in snapshot.rows}
        absent_ids = {asset_id for asset_id in legacy_ids if asset_id not in snapshot_ids}
        verified_absent_types = (
            dict.fromkeys(absent_ids, args.allow_absent_type)
            if args.allow_absent_type is not None
            else None
        )
        reserve_truth_asset_ids(
            ledger,
            name=args.legacy_cohort_name,
            snapshot=snapshot,
            asset_ids=legacy_ids,
            verified_absent_types=verified_absent_types,
        )
        public_ledger = ledger.public_manifest()
        legacy_public = next(
            cohort
            for cohort in public_ledger["cohorts"]
            if cohort["name"] == args.legacy_cohort_name
        )
        print(
            "legacy truth reserved: "
            f"assets={len(legacy_ids)} "
            f"mapped={legacy_public['mapped_count']} "
            f"asset_only={legacy_public['asset_only_count']} "
            f"ledger={public_ledger['ledger_sha256']}",
            flush=True,
        )
    plan = select_certification_then_training(
        snapshot.rows,
        certification_spec=_cohort_spec("cert", args),
        training_spec=_cohort_spec("training", args),
        seed=args.seed,
        already_reserved=ledger.blocklist(exclude_cohort=args.certification_cohort_name),
    )
    ledger_digest = ledger.reserve_truth_cohort(
        name=args.certification_cohort_name,
        inventory_sha256=snapshot.inventory_sha256,
        rows=plan.certification.rows,
    )
    manifest = save_cohort_plan(
        args.output_dir,
        snapshot=snapshot,
        plan=plan,
        ledger_sha256=ledger_digest,
    )
    certification = manifest["certification"]["audit"]
    training = manifest["training"]["audit"]
    print(
        "cohorts sealed: "
        f"certification={certification['selected_count']} "
        f"training={training['selected_count']} "
        f"ledger={ledger_digest}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        return _scan(args) if args.command == "scan" else _plan(args)
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"STOP: {error}") from error


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
