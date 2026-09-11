"""The release smoke gate has to survive the pipeline it exists to exercise.

Two releases died here already. #480: the script invented an API key while
FakeImmichServer checks `x-api-key` against its own. #525: the synthetic
inventory was sub-1080p with no camera EXIF, so the real source gates emptied
the pool and every run ended in "Pipeline selected no clips".

The third way it can die is the story-first route: a release runner has no text
model and no prepared annotation store, so `generate` stops on the blank-model
guard unless the hermetic editorial route is mounted into the container.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from scripts import docker_smoke

from immich_memories.analysis.source_filter import not_shot_here
from immich_memories.analysis.source_quality import is_usable_source
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.config_models_analysis import AnalysisConfig
from immich_memories.config_models_render import PhotoConfig
from tests.e2e.fake_immich import TIMELINE_ASSETS, FakeImmichServer

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "docker_smoke.py"


def test_the_container_gets_the_fake_servers_key(tmp_path: Path) -> None:
    argv = docker_smoke.generate_argv(
        image="ghcr.io/example/app@sha256:cafe",
        container="smoke-test",
        immich_url="http://127.0.0.1:9",
        api_key=FakeImmichServer.api_key,
        out_dir=tmp_path,
        editorial_dir=tmp_path,
    )

    assert f"IMMICH_API_KEY={FakeImmichServer.api_key}" in argv
    assert "api_key=server.api_key" in _SCRIPT.read_text()
    assert "smoke-test-key" not in _SCRIPT.read_text()


def test_a_failure_prints_the_childs_own_output() -> None:
    """The CLI logs to stdout, so stderr alone hid the cause."""
    source = _SCRIPT.read_text()
    failure_block = source.split("if proc.returncode != 0:")[1].split("return 1")[0]

    assert "proc.stdout" in failure_block
    assert "proc.stderr" in failure_block


def test_the_script_still_parses() -> None:
    ast.parse(_SCRIPT.read_text())


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
            has_camera_exif=VideoClipInfo(asset=asset).is_camera_original,
            min_short_side=analysis.min_source_short_side,
        ), f"{asset.id} is dropped as a re-encode"
        assert VideoClipInfo(asset=asset).is_camera_original, f"{asset.id} names no camera"


def test_no_two_smoke_captures_fall_in_the_same_moment() -> None:
    """A still beside a clip is dropped as already shown, which emptied the pool."""
    window = timedelta(seconds=PhotoConfig().moment_gap_seconds)
    taken_at = sorted(Asset.model_validate(payload).file_created_at for payload in TIMELINE_ASSETS)

    assert all(later - earlier > window for earlier, later in pairwise(taken_at))


def test_the_release_smoke_drives_the_story_first_route_inside_the_image(tmp_path) -> None:
    """Without the mounted route, `generate` in the image never reaches a render."""
    injected = docker_smoke.prepare_editorial_fixture(tmp_path)
    argv = docker_smoke.generate_argv(
        image="ghcr.io/example/app@sha256:cafe",
        container="smoke-test",
        immich_url="http://127.0.0.1:9",
        api_key="not-a-real-key",
        out_dir=tmp_path / "output",
        editorial_dir=injected,
    )

    fixture = _SCRIPT.parent.parent / "tests" / "e2e" / "fake_editorial.py"
    assert (injected / fixture.name).read_text() == fixture.read_text()
    assert f"{injected}:{docker_smoke.SMOKE_MOUNT}:ro" in argv
    entry = argv.index("ghcr.io/example/app@sha256:cafe")
    assert argv[entry + 1 : entry + 3] == [
        "python",
        f"{docker_smoke.SMOKE_MOUNT}/smoke_bootstrap.py",
    ]
    assert "generate" in argv


def test_the_release_smoke_stops_when_the_editorial_fixture_is_gone(tmp_path, monkeypatch) -> None:
    """Removing the fixture must fail the gate loudly, never render nothing quietly."""
    monkeypatch.setattr(
        docker_smoke, "FIXTURE", _SCRIPT.parent.parent / "tests" / "e2e" / "removed.py"
    )

    with pytest.raises(FileNotFoundError):
        docker_smoke.prepare_editorial_fixture(tmp_path)
