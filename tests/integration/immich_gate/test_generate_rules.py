"""Real-Immich gate: `generate --no-render` picks a cut from the fixture month on the rules tier.

No language model, no picture models: the reader is `rules` and preparation is
`metadata_only` (seed.py writes that config), so this runs on a plain runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from immich_memories.config_loader import Config
from tests.integration.immich_fixtures import requires_immich

pytestmark = [requires_immich]

_PROVIDER_SHORTCUTS = ("IMMICH_URL", "IMMICH_API_KEY", "OPENAI_API_KEY")


def _isolated_env() -> dict[str, str]:
    # WHY: a developer's provider variables would override the gate's config and
    # point the run at their own library or model.
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("IMMICH_MEMORIES_", "ZAI_")) and key not in _PROVIDER_SHORTCUTS
    }


def test_rules_tier_selection_over_the_fixture_month_picks_a_cut(tmp_path):
    trace = tmp_path / "selection-trace.txt"
    command = [
        str(Path(sys.executable).parent / "immich-memories"),
        "--config",
        str(Config.get_default_path()),
        "generate",
        # A standard memory type: rules refuse a free-form custom range.
        "--memory-type",
        "monthly_highlights",
        "--year",
        "2024",
        "--month",
        "6",
        "--include-photos",
        "--no-music",
        "--no-render",
        "--quiet",
        "--trace-selection",
        str(trace),
    ]
    result = subprocess.run(  # noqa: S603 -- our own CLI, fixed argv
        command,
        capture_output=True,
        text=True,
        timeout=600,
        env=_isolated_env(),
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output[-4000:]
    assert "Selection complete; no video was created" in output, output[-4000:]
    assert trace.exists(), output[-4000:]
    assert trace.stat().st_size > 0
