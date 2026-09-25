"""Print what each product tier runs with, as the product itself resolves it.

    uv run python scripts/tier_settings.py          # a table
    uv run python scripts/tier_settings.py --json   # one JSON object per tier

The validation kit reads this to check that its per-tier configs match the product's.
"""

from __future__ import annotations

import json
import sys
from typing import Any, get_args

from immich_memories.config_loader import Config
from immich_memories.config_tiers import ProductTier


def tier_settings(tier: str) -> dict[str, Any]:
    """What a config stating only `tier` runs with, read back from a loaded Config.

    `full` is given a placeholder LLM endpoint, because it refuses to load without one.
    """
    llm = {"base_url": "http://llm.invalid/v1", "model": "stated"} if tier == "full" else {}
    config = Config(tier=tier, llm=llm)
    editorial = config.editorial
    return {
        "tier": tier,
        "reader": editorial.resolve_reader(config.llm.model),
        "preparation_tier": editorial.preparation.tier,
        "laya_audience": editorial.laya_audience,
        "llm_endpoint_required": tier == "full",
    }


def main(argv: list[str]) -> int:
    rows = [tier_settings(tier) for tier in get_args(ProductTier)]
    if "--json" in argv:
        for row in rows:
            print(json.dumps(row))
        return 0
    columns = list(rows[0])
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(str(row[column]) for column in columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
