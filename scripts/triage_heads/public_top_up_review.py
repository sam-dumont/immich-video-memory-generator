#!/usr/bin/env python3
"""Verify a completed private top-up source and print its manual pin value.

This command is intentionally read-only. It never edits ``public_top_up.py``;
the printed digest is evidence for a separate human review and code change.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from scripts.triage_heads.public_top_up import TopUpSpec
from scripts.triage_heads.public_top_up_acquire import (
    DEFAULT_ACQUIRED_SOURCE,
    DEFAULT_BASE_PARTITION,
    DEFAULT_CANDIDATE_INVENTORY,
    DEFAULT_OFFICIAL_METADATA,
    resolve_acquisition_plan,
    review_acquired_source,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-partition", type=Path, default=DEFAULT_BASE_PARTITION)
    parser.add_argument("--candidate-inventory", type=Path, default=DEFAULT_CANDIDATE_INVENTORY)
    parser.add_argument("--official-metadata", type=Path, default=DEFAULT_OFFICIAL_METADATA)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=DEFAULT_ACQUIRED_SOURCE / "manifest.json",
    )
    parser.add_argument(
        "--source-images",
        type=Path,
        default=DEFAULT_ACQUIRED_SOURCE / "images",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plan, _base_hashes = resolve_acquisition_plan(
        base_partition=args.base_partition,
        candidate_inventory=args.candidate_inventory,
        official_metadata=args.official_metadata,
    )
    digest = review_acquired_source(
        args.source_manifest,
        args.source_images,
        expected_count=TopUpSpec().candidate_count,
        expected_plan=plan,
    )
    print(f'EXPECTED_TOP_UP_SOURCE_MANIFEST_SHA256 = "{digest}"')
    print("review only: no source file or production pin was changed")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
