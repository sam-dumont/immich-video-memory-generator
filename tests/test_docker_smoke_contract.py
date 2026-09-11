"""The release smoke gate has to survive the pipeline it exists to exercise.

Two releases died here already. #480: the script invented an API key while
FakeImmichServer checks `x-api-key` against its own. #525: the synthetic
inventory was sub-1080p with no camera EXIF, so the real source gates emptied
the pool and every run ended in "Pipeline selected no clips".
"""

from __future__ import annotations

import ast
from pathlib import Path

from immich_memories.analysis.source_filter import not_shot_here
from immich_memories.analysis.source_quality import is_usable_source
from immich_memories.api.models import Asset
from immich_memories.config_models_analysis import AnalysisConfig
from tests.e2e.fake_immich import TIMELINE_ASSETS

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "docker_smoke.py"


def test_the_container_gets_the_fake_servers_key() -> None:
    source = _SCRIPT.read_text()

    assert "IMMICH_API_KEY={server.api_key}" in source
    assert "smoke-test-key" not in source


def test_a_failure_prints_the_childs_own_output() -> None:
    """The CLI logs to stdout, so stderr alone hid the cause."""
    source = _SCRIPT.read_text()
    failure_block = source.split("if proc.returncode != 0:")[1].split("return 1")[0]

    assert "proc.stdout" in failure_block
    assert "proc.stderr" in failure_block


def test_the_script_still_parses() -> None:
    ast.parse(_SCRIPT.read_text())


def _names_a_camera(asset: Asset) -> bool:
    return bool(asset.exif_info and (asset.exif_info.make or asset.exif_info.model))


def test_every_smoke_asset_survives_the_real_source_gates() -> None:
    """A fixture the pipeline throws away tests nothing (#525)."""
    analysis = AnalysisConfig()

    for payload in TIMELINE_ASSETS:
        asset = Asset.model_validate(payload)
        assert not not_shot_here(
            asset,
            patterns=analysis.exclude_filename_patterns,
            stills_need_a_camera=analysis.exclude_stills_without_camera_exif,
        ), f"{asset.id} reads as something other than a camera original"
        assert is_usable_source(
            width=asset.width,
            height=asset.height,
            has_camera_exif=_names_a_camera(asset),
            min_short_side=analysis.min_source_short_side,
        ), f"{asset.id} is dropped as a re-encode"
        assert _names_a_camera(asset), f"{asset.id} names no camera"
