#!/usr/bin/env python3
"""Audit, build, and approve owner-training cohort v2 without Immich access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cohorts import TruthReservationLedger, load_inventory_snapshot
from .memory import (
    acquire_recovery_pipeline_lock,
    ensure_private_directory,
    validate_offline_working_set_gib,
)
from .training_cohort import TrainingCohortConfig
from .training_cohort_dino_audit import build_dino_diversity_audit
from .training_cohort_v2 import (
    audit_source_v1_retention,
    build_training_cohort_v2,
    create_visual_review_decision_input,
    finalize_visual_review_approval,
)

DEFAULT_RECOVERY_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads" / "recovery-1b"
DEFAULT_INVENTORY_DIR = DEFAULT_RECOVERY_DIR / "full-image-inventory-v1"
DEFAULT_LEDGER = DEFAULT_RECOVERY_DIR / "truth-reservations.sqlite"
DEFAULT_APPROVAL_DIR = DEFAULT_RECOVERY_DIR / "fresh-location-cert-v3-approval"
DEFAULT_SOURCE_ROOT = DEFAULT_RECOVERY_DIR / "owner-training-cohort-v1"
DEFAULT_SOURCE_REJECTION_DECISION = DEFAULT_RECOVERY_DIR / "owner-training-cohort-v1-rejected.json"
DEFAULT_OUTPUT_DIR = DEFAULT_RECOVERY_DIR / "owner-training-cohort-v2"
DEFAULT_DINO_AUDIT_DIR = DEFAULT_RECOVERY_DIR / "owner-training-cohort-v2-dino-audit"
DEFAULT_DINO_ONNX = DEFAULT_RECOVERY_DIR.parent / "dinov2-small-ed25f3a" / "model.onnx"


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inventory-dir", type=Path, default=DEFAULT_INVENTORY_DIR)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--approval-dir", type=Path, default=DEFAULT_APPROVAL_DIR)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument(
        "--source-rejection-decision",
        type=Path,
        default=DEFAULT_SOURCE_REJECTION_DECISION,
    )
    parser.add_argument("--seed", default="location-recovery-1b-training-v2")
    parser.add_argument("--cluster-count", type=int, default=128)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--memory-limit-gib", type=float, default=8.0)
    parser.add_argument("--review-sample-size", type=int, default=64)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser(
        "scan",
        help="authenticate v1 and report v2 retained counts without output",
    )
    _add_source_arguments(scan)

    build = commands.add_parser(
        "build",
        help="create the immutable, unapproved v2 cohort from sealed v1 pixels",
    )
    _add_source_arguments(build)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    dino = commands.add_parser(
        "dino-audit",
        help="seal the label-blind DINO global-nearest review for owner v2",
    )
    dino.add_argument("--cohort-root", type=Path, default=DEFAULT_OUTPUT_DIR)
    dino.add_argument("--output-dir", type=Path, default=DEFAULT_DINO_AUDIT_DIR)
    dino.add_argument("--onnx", type=Path, default=DEFAULT_DINO_ONNX)
    dino.add_argument("--provider", choices=("cpu",), default="cpu")
    dino.add_argument("--batch-size", type=int, default=32)
    dino.add_argument("--memory-limit-gib", type=float, default=8.0)
    dino.add_argument("--review-columns", type=int, default=4)
    dino.add_argument("--review-rows", type=int, default=4)

    decision = commands.add_parser(
        "decision",
        help="create a canonical human review decision bound to every review page",
    )
    decision.add_argument("--cohort-root", type=Path, required=True)
    decision.add_argument("--output", type=Path, required=True)
    decision.add_argument("--decision", choices=("approved", "rejected"), required=True)
    decision.add_argument("--reviewer", required=True)
    decision.add_argument("--notes", required=True)
    decision.add_argument("--dino-audit-root", type=Path, default=DEFAULT_DINO_AUDIT_DIR)

    approve = commands.add_parser(
        "approve",
        help="seal a reviewed v2 decision as the training approval",
    )
    approve.add_argument("--cohort-root", type=Path, default=DEFAULT_OUTPUT_DIR)
    approve.add_argument("--decision-file", type=Path, required=True)
    approve.add_argument("--dino-audit-root", type=Path, default=DEFAULT_DINO_AUDIT_DIR)
    return parser.parse_args(argv)


def _config(args: argparse.Namespace) -> TrainingCohortConfig:
    memory_limit_gib = validate_offline_working_set_gib(args.memory_limit_gib)
    return TrainingCohortConfig(
        seed=args.seed,
        cluster_count=args.cluster_count,
        workers=args.workers,
        memory_limit_bytes=int(memory_limit_gib * 1024**3),
        review_sample_size=args.review_sample_size,
    )


def _source_inputs(
    args: argparse.Namespace,
) -> tuple[object, TruthReservationLedger, TrainingCohortConfig]:
    if not args.ledger.is_file():
        raise RuntimeError("truth ledger is missing; owner v2 reuse is forbidden")
    snapshot = load_inventory_snapshot(args.inventory_dir)
    return snapshot, TruthReservationLedger(args.ledger), _config(args)


def _progress(completed: int, total: int) -> None:
    if completed == 1 or completed == total or completed % 250 == 0:
        print(f"authenticated source pixels: {completed}/{total}", flush=True)


def _dino_progress(completed: int, total: int) -> None:
    if completed == total or completed % 250 == 0:
        print(f"DINO audit embeddings: {completed}/{total}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    pipeline_lock = None
    try:
        pipeline_lock = acquire_recovery_pipeline_lock()
        if args.command == "dino-audit":
            audit = build_dino_diversity_audit(
                cohort_root=args.cohort_root,
                output_dir=args.output_dir,
                onnx_path=args.onnx,
                provider=args.provider,
                batch_size=args.batch_size,
                max_working_set_gib=args.memory_limit_gib,
                review_columns=args.review_columns,
                review_rows=args.review_rows,
                progress=_dino_progress,
            )
            print(
                "DINO diversity audit sealed for visual review: "
                f"count={audit.vector_count} "
                f"max_nearest_similarity={audit.max_nearest_similarity:.9f} "
                f"output={args.output_dir}",
                flush=True,
            )
            return 0
        if args.command == "decision":
            ensure_private_directory(args.output.parent)
            decision = create_visual_review_decision_input(
                cohort_root=args.cohort_root,
                output_path=args.output,
                decision=args.decision,
                reviewer=args.reviewer,
                notes=args.notes,
                dino_audit_root=args.dino_audit_root,
            )
            print(
                "visual-review decision sealed: "
                f"decision={decision['decision']} output={args.output}",
                flush=True,
            )
            return 0
        if args.command == "approve":
            approval = finalize_visual_review_approval(
                cohort_root=args.cohort_root,
                decision_path=args.decision_file,
                dino_audit_root=args.dino_audit_root,
            )
            print(
                f"owner v2 approved for training: approval_sha256={approval['approval_sha256']}",
                flush=True,
            )
            return 0

        snapshot, ledger, config = _source_inputs(args)
        common = {
            "snapshot": snapshot,
            "ledger": ledger,
            "source_root": args.source_root,
            "source_rejection_decision_path": args.source_rejection_decision,
            "config": config,
            "fresh_truth_approval_dir": args.approval_dir,
            "progress": _progress,
        }
        if args.command == "scan":
            audit = audit_source_v1_retention(**common)
            print(json.dumps(audit.public_dict(), sort_keys=True), flush=True)
            if not audit.target_reachable:
                raise RuntimeError(
                    "v2 duplicate-family survivors are below the 20,000-image target"
                )
            return 0
        run = build_training_cohort_v2(output_dir=args.output_dir, **common)
        audit = run.selection.audit
        print(
            "owner training cohort v2 sealed for visual review: "
            f"selected={audit.temporal.selected_count} "
            f"days={audit.temporal.distinct_days} "
            f"max_nearest_similarity={run.nearest_audit.max_nearest_similarity:.9f} "
            f"status=awaiting_visual_review output={args.output_dir}",
            flush=True,
        )
        return 0
    except (MemoryError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"STOP: {error}") from error
    finally:
        if pipeline_lock is not None:
            pipeline_lock.close()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
