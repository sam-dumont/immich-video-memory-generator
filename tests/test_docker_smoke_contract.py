"""The release smoke gate has to survive the pipeline it exists to exercise.

Two releases died here already. #480: the script invented an API key while
FakeImmichServer checks `x-api-key` against its own. #525: the synthetic
inventory was sub-1080p with no camera EXIF, so the real source gates emptied
the pool and every run ended in "Pipeline selected no clips".

The third way it can die is the story-first route: a release runner has no text
model and no prepared annotation store, so `generate` stops on the blank-model
guard unless the hermetic editorial route is mounted into the container.

The fourth killed v0.85.0 (#881), and it was the same mistake twice: the smoke
was still written for the six-picture fixture #876 replaced. It handed the
container `fake_library.py` without the directory that module globs to know which
pictures exist, so the scripted editor kept none of the 133 sources the fake
Immich served ("Pipeline selected no clips"), and it asked for 20 seconds, which
is too short a budget to hold the eighteen carriers the month now ships.

The fifth killed v0.85.1: the gate captured the container's output instead of
streaming it, so a 1200 s timeout left a release log holding one line --
"FAIL: generation hung past 1200s" -- and no way to tell which phase had eaten
the twenty minutes.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from scripts import docker_smoke

from immich_memories.analysis.source_filter import not_shot_here
from immich_memories.analysis.source_quality import is_usable_source
from immich_memories.api.models import Asset
from immich_memories.config_loader import Config
from immich_memories.config_models_analysis import AnalysisConfig
from immich_memories.config_models_render import TitleScreenConfig
from immich_memories.generate_clips import MIN_CLIP_DURATION
from immich_memories.processing.hdr_utilities import quality_encoder_preset
from tests.e2e.fake_immich import TIMELINE_ASSETS, FakeImmichServer
from tests.e2e.fake_library import CARRIERS, LIBRARY

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


def _printing(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", textwrap.dedent(script)]


def test_the_gate_prints_the_container_as_it_runs_and_times_its_phases(capsys) -> None:
    """A captured log is no log at all while the run is still going (v0.85.1)."""
    run = docker_smoke.stream_container(
        _printing(
            """
            import time
            print("Downloading clips...")
            time.sleep(0.4)
            print("Rendering memory")
            print("Video saved to: /app/output/smoke.mp4")
            """
        ),
        container="not-a-container",
        timeout=60,
    )

    assert run.returncode == 0
    assert not run.timed_out
    assert [name for name, _ in run.phases] == [
        "Downloading clips",
        "Rendering memory",
        "Video saved to:",
    ]
    downloading, rendering = run.phases[0][1], run.phases[1][1]
    assert rendering - downloading >= 0.4, "the phase offsets are not measured from the run"
    assert "Rendering memory" in capsys.readouterr().out


def test_a_timeout_names_the_phase_the_container_reached_and_repeats_its_tail(capsys) -> None:
    """`hung past 1200s` alone is what run 34769467426 left behind."""
    run = docker_smoke.stream_container(
        _printing(
            """
            import time
            print("Downloading clips...")
            print("Rendering memory")
            time.sleep(120)
            """
        ),
        container="not-a-container",
        timeout=2,
    )

    assert run.timed_out
    assert run.last_phase == "Rendering memory"
    assert "Rendering memory" in run.tail

    docker_smoke.report_failure(run, 2)
    reported = capsys.readouterr().err
    assert "hung past 2s in 'Rendering memory'" in reported
    assert "Downloading clips..." in reported


def test_a_failure_repeats_the_childs_own_output(capsys) -> None:
    """The CLI logs to stdout, so stderr alone hid the cause."""
    run = docker_smoke.stream_container(
        _printing(
            """
            import sys
            print("Downloading clips...")
            print("boom, the image is missing a codec")
            sys.exit(3)
            """
        ),
        container="not-a-container",
        timeout=60,
    )
    capsys.readouterr()

    docker_smoke.report_failure(run, 60)
    reported = capsys.readouterr().err
    assert "boom, the image is missing a codec" in reported
    assert "exit 3 in 'Downloading clips'" in reported


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
    assert (injected / "tests" / "e2e" / fixture.name).read_text() == fixture.read_text()
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


def test_mounted_bootstrap_imports_without_the_repository_tests_package(tmp_path: Path) -> None:
    """An image has the installed app and mounted fixtures, not this checkout."""
    injected = docker_smoke.prepare_editorial_fixture(tmp_path)
    # Supply the host equivalent of /smoke while -I excludes the checkout and
    # PYTHONPATH. Running the real bootstrap catches missing fixture imports.
    bootstrap = (
        "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
        "sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            bootstrap,
            str(injected),
            str(injected / "smoke_bootstrap.py"),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "generate" in proc.stdout


def test_the_mounted_route_reads_the_month_the_fake_immich_serves(tmp_path: Path) -> None:
    """The staged library must hold the same pictures, or the cut comes back empty (#881).

    `fake_library` decides which pictures exist by globbing its own directory, so
    a container handed the module without the files keeps none of the sources the
    host serves and the release ends in "Pipeline selected no clips".
    """
    injected = docker_smoke.prepare_editorial_fixture(tmp_path)
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from tests.e2e.fake_library import CARRIERS, LIBRARY, STORY_OF; "
        "print(len(LIBRARY), len(CARRIERS), len(STORY_OF))"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(injected)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.split() == [str(len(LIBRARY)), str(len(CARRIERS)), str(len(CARRIERS))]


def test_the_smoke_asks_for_a_memory_the_scripted_cut_fits_inside(tmp_path: Path) -> None:
    """Too short an ask samples carriers out of a certified selection (#881).

    `apply_final_content_budget` keeps at most `budget // MIN_CLIP_DURATION` clips,
    and dropping one from a bound editorial timeline makes assembly refuse the
    render. The fixture grew from six pictures to eighteen; the ask has to follow.
    """
    titles = TitleScreenConfig()
    argv = docker_smoke.generate_argv(
        image="ghcr.io/example/app@sha256:cafe",
        container="smoke-test",
        immich_url="http://127.0.0.1:9",
        api_key="not-a-real-key",
        out_dir=tmp_path / "output",
        editorial_dir=tmp_path,
    )

    requested = float(argv[argv.index("--duration") + 1])
    assert requested >= (
        len(CARRIERS) * MIN_CLIP_DURATION + titles.title_duration + titles.ending_duration
    )


def test_the_bootstrap_counts_the_month_before_anything_selects(tmp_path: Path) -> None:
    """An unreadable pool must stop the gate, not arrive as an editorial verdict (#881).

    Pointed at a closed port, the bootstrap has to name the pool it could not read
    and stop there — before a download, a render, or a "selected no clips" that
    says nothing about the image.
    """
    injected = docker_smoke.prepare_editorial_fixture(tmp_path)
    runner = (
        "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
        "sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            runner,
            str(injected),
            str(injected / "smoke_bootstrap.py"),
            "generate",
        ],
        cwd=tmp_path,
        env={**os.environ, "IMMICH_URL": "http://127.0.0.1:9", "IMMICH_API_KEY": "not-a-real-key"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode != 0
    assert "smoke: cannot read the pool at http://127.0.0.1:9" in proc.stderr
    assert "Downloading" not in proc.stdout


def test_the_gate_leaves_the_preparation_tier_alone() -> None:
    """No tier's producers run here, so pinning one would be a decoration.

    The mounted route replaces `build_smart_pipeline`, preparation included: a
    release smoke's 133 previews are scripted and cost 0.15 s. Measured on the
    v0.85.1 arm64 image, nothing in the container opens an annotation store or a
    detector, so the tier is not a lever on this gate's cost and the gate has no
    business overriding what a deployment asks for.
    """
    assert not [name for name in docker_smoke.PINNED_CONFIG if "PREPARATION" in name]


def test_the_pins_are_the_names_the_container_config_actually_reads(monkeypatch) -> None:
    """A pin the loader does not recognise is a comment, not a setting.

    Every entry has to land on the field it names, through the same
    `IMMICH_MEMORIES_<SECTION>__<FIELD>` path the container gets them on.
    """
    for name, value in docker_smoke.PINNED_CONFIG.items():
        monkeypatch.setenv(name, value)

    config = Config()

    assert config.output.resolution_tuple == (1280, 720)
    assert quality_encoder_preset(config.output.quality, config.hardware.encoder_preset) == "fast"


def test_the_pins_travel_on_the_docker_run_that_renders_the_cut(tmp_path: Path) -> None:
    """Pinned in the script and absent from the argv is the failure mode to catch."""
    argv = docker_smoke.generate_argv(
        image="ghcr.io/example/app@sha256:cafe",
        container="smoke-test",
        immich_url="http://127.0.0.1:9",
        api_key="not-a-real-key",
        out_dir=tmp_path / "output",
        editorial_dir=tmp_path,
    )

    for name, value in docker_smoke.PINNED_CONFIG.items():
        assert f"{name}={value}" in argv
    # Streaming is worth nothing if the child's own prints sit in a pipe buffer.
    assert "PYTHONUNBUFFERED=1" in argv
    # #890's staging and host networking are untouched: both are why it renders.
    assert argv[argv.index("--network") + 1] == "host"
