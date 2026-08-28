#!/usr/bin/env python3
"""Run the production editorial path through Cull and render Structure's workprint.

Private sheets must be written outside the repository:

    uv run python scripts/probe_editorial_workprint.py \
        --start 2023-06-01 --end 2023-06-30 \
        --out /private/tmp/immich-structure-june
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, time
from pathlib import Path


def _boundary(value: str, *, end: bool) -> datetime:
    day = date.fromisoformat(value)
    return datetime.combine(day, time.max if end else time.min, tzinfo=UTC)


def _outside_repository(path: Path, parser: argparse.ArgumentParser) -> Path:
    resolved = path.expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    if resolved.is_relative_to(repository):
        parser.error(
            "--out must be outside the repository because the sheets contain private media"
        )
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="First included day, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last included day, YYYY-MM-DD")
    parser.add_argument("--out", required=True, type=Path, help="Private output directory")
    args = parser.parse_args()
    output_dir = _outside_repository(args.out, parser)

    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull
    from immich_memories.analysis.selection_source import (
        EditorialDependencies,
        EditorialSelectionRequest,
        SourceScope,
        prepare_editorial_source,
    )
    from immich_memories.analysis.selection_structure import build_structure_workprint
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.cache.judgment_cache import verdicts_beside
    from immich_memories.config import get_config
    from immich_memories.timeperiod import DateRange

    config = get_config()
    requested = DateRange(
        _boundary(args.start, end=False),
        _boundary(args.end, end=True),
    )
    scope = SourceScope(
        start_at=requested.start,
        end_at=requested.end,
        excluded_filename_patterns=tuple(config.analysis.exclude_filename_patterns),
        stills_need_a_camera=config.analysis.exclude_stills_without_camera_exif,
        min_source_short_side=config.analysis.min_source_short_side,
        include_off_timeline=False,
    )
    with SyncImmichClient(
        base_url=config.immich.url,
        api_key=config.immich.api_key,
    ) as client:
        assets = tuple(client.get_assets_for_date_range(requested))
        prepared = prepare_editorial_source(
            EditorialSelectionRequest(scope=scope),
            EditorialDependencies(
                source_fetcher=lambda _scope: assets,
                preview_jpeg=lambda asset: client.get_asset_thumbnail(asset.id, "preview"),
            ),
        )
        gateway = VisualEditorialGateway(
            llm_config=config.llm,
            cache_path=verdicts_beside(config.cache.cache_path),
            trace=prepared.trace,
        )
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=output_dir / "insight",
            frame_cache_dir=config.cache.cache_path / "editorial-frames",
        )
        pass_one = run_cull(
            prepared,
            pass_zero,
            review_output_dir=output_dir / "cull-review",
        )
        workprint = build_structure_workprint(
            prepared,
            pass_one.survivors,
            atlas=pass_zero.atlas,
            output_dir=output_dir / "structure",
        )

    print(
        json.dumps(
            {
                "fetched": len(assets),
                "source_eligible": len(prepared.candidates),
                "cull_survivors": len(pass_one.survivors),
                "episodes": len(prepared.episode_groups),
                "surviving_moments": len(workprint.moments),
                "workprint_tiles": len(workprint.representative_ids),
                "actual_model_calls": pass_zero.actual_calls,
                "warnings": [*pass_zero.warnings, *pass_one.warnings],
                "workprint_pages": [str(page.path) for page in workprint.pages],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
