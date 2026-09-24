"""Shared test fixtures and factories for immich-video-memory-generator."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from rich.console import Console

from immich_memories import config_loader
from immich_memories.api.models import Asset, AssetType, ExifInfo, VideoClipInfo
from immich_memories.config_loader import Config

_TEST_ROOT: Path | None = None
_TEST_ENV_KEYS = {
    "IMMICH_MEMORIES_CACHE__DATABASE": "cache.db",
    "IMMICH_MEMORIES_CACHE__DIRECTORY": "cache",
    "IMMICH_MEMORIES_OUTPUT__DIRECTORY": "output",
}
_ORIGINAL_TEST_ENV: dict[str, str | None] = {}

# The width every CLI render is pinned to. Wide enough that a sentence with a
# pytest temporary path in it still fits on one line.
_CLI_WIDTH = 200
_TERMINAL_PATCHES = pytest.MonkeyPatch()


def pytest_configure(config: pytest.Config) -> None:
    """Route test configuration paths to one disposable session directory."""
    del config
    global _TEST_ROOT
    _TEST_ROOT = Path(tempfile.mkdtemp(prefix="immich-memories-pytest-"))

    for key, relative in _TEST_ENV_KEYS.items():
        _ORIGINAL_TEST_ENV[key] = os.environ.get(key)
        os.environ[key] = str(_TEST_ROOT / relative)

    _pin_the_cli_width()


def _pin_the_cli_width() -> None:
    """Render CLI output at one width instead of at the developer's terminal's.

    Nothing in a CliRunner run replaces the terminal, so the width reached the
    CLI from whatever window the suite ran in: a message wrapped in a different
    place on every machine, and an assertion on a phrase that landed across the
    break passed or failed by luck (#880). Two seams ask for that width, so both
    are pinned here rather than in the tests that happen to trip over them.

    A Rich console takes its width from $COLUMNS when it is built and keeps it,
    and the CLI builds its console at import time — before any fixture could
    reach it — so the width goes in at construction. `runs why` instead wraps its
    reasons with shutil.get_terminal_size at the moment it prints, which reads
    $COLUMNS live; CliRunner already applies `env` for the length of an
    invocation and puts it back after, so pinning it there leaves pytest's own
    report at the terminal's real width.
    """
    console_init = Console.__init__
    runner_init = CliRunner.__init__

    def sized_console(self: Console, **kwargs: Any) -> None:
        if kwargs.get("width") is None:
            kwargs["width"] = _CLI_WIDTH
        console_init(self, **kwargs)

    def sized_runner(self: CliRunner, *args: Any, **kwargs: Any) -> None:
        runner_init(self, *args, **kwargs)
        self.env = {"COLUMNS": str(_CLI_WIDTH), **self.env}

    _TERMINAL_PATCHES.setattr(Console, "__init__", sized_console)
    _TERMINAL_PATCHES.setattr(CliRunner, "__init__", sized_runner)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore the process environment and remove the validated test root."""
    del config
    global _TEST_ROOT

    _TERMINAL_PATCHES.undo()

    for key, original_value in _ORIGINAL_TEST_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    _ORIGINAL_TEST_ENV.clear()

    if _TEST_ROOT is None:
        return

    test_root = _TEST_ROOT
    _TEST_ROOT = None
    temp_root = Path(tempfile.gettempdir()).resolve()
    resolved_test_root = test_root.resolve()
    if not (
        test_root.name.startswith("immich-memories-pytest-")
        and resolved_test_root.is_relative_to(temp_root)
    ):
        raise RuntimeError(f"Refusing to remove unvalidated pytest root: {test_root}")

    shutil.rmtree(test_root)


@pytest.fixture(autouse=True)
def fresh_ffmpeg_capabilities() -> Iterator[None]:
    """Forget which filters FFmpeg has, before and after every test.

    check_zscale_available caches its answer for the process. A test that stubs
    `ffmpeg -filters` as having zscale left that answer behind, and a later test
    rendering with the real FFmpeg then used a filter the runner's build lacked.
    """
    from immich_memories.processing import hdr_utilities

    hdr_utilities._zscale_cache = None
    yield
    hdr_utilities._zscale_cache = None


@pytest.fixture(autouse=True)
def isolated_user_paths() -> Iterator[Path]:
    """Reset cached settings and assert tests never resolve user directories."""
    assert _TEST_ROOT is not None
    config_loader._config = None
    yield _TEST_ROOT
    config_loader._config = None

    config = Config()
    normal_user_paths = {
        Path.home() / ".immich-memories" / "cache.db",
        Path.home() / ".immich-memories" / "cache",
        Path.home() / "Videos" / "Memories",
    }
    resolved_paths = {
        config.cache.database_path,
        config.cache.cache_path,
        config.output.output_path,
    }
    assert not resolved_paths & normal_user_paths


@pytest.fixture()
def git_checkout_factory() -> Callable[[Path, int], Path]:
    """Build a real checkout whose HEAD trails an already-fetched tracking branch.

    Real git rather than a mocked one: the staleness guard's whole job is reading refs
    that only git can produce, and a faked `rev-list` would prove nothing about it.
    """

    # GIT_DIR and friends beat `-C`, so under a git hook (pre-commit runs the suite as
    # one) every command below would target the real repository instead of tmp_path.
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}

    def run(root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=env,
        )
        return result.stdout.strip()

    def build(root: Path, commits_behind: int = 0) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        run(root, "init", "-b", "main", "-q")
        run(root, "config", "user.email", "test@example.invalid")
        run(root, "config", "user.name", "Test")
        # The upstream needs a registered remote for its fetch refspec to resolve.
        run(root, "remote", "add", "origin", "https://example.invalid/repo.git")
        run(root, "commit", "--allow-empty", "-q", "-m", "base")
        base = run(root, "rev-parse", "HEAD")
        for index in range(commits_behind):
            run(root, "commit", "--allow-empty", "-q", "-m", f"upstream-{index}")
        run(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        run(root, "reset", "--hard", "-q", base)
        run(root, "config", "branch.main.remote", "origin")
        run(root, "config", "branch.main.merge", "refs/heads/main")
        return root

    return build


HDR_SAMPLES = Path(__file__).parent / "fixtures" / "hdr_samples"
_LFS_POINTER_HEADER = b"version https://git-lfs.github.com/spec/v1"


def lfs_fixture(path: Path) -> Path:
    """Return a Git LFS-tracked fixture, or skip the calling test when it is absent.

    A clone without `git lfs pull` (or with GIT_LFS_SKIP_SMUDGE=1) checks out a
    ~130-byte text pointer in place of the media, and a test reading it fails as
    if the product were broken. Skipping names the real cause instead.
    """
    if not path.is_file():
        pytest.skip(f"{path.name} is missing: run `git lfs pull` to fetch the test media")
    with path.open("rb") as handle:
        if handle.read(len(_LFS_POINTER_HEADER)) == _LFS_POINTER_HEADER:
            pytest.skip(f"{path.name} is a Git LFS pointer, not the file: run `git lfs pull`")
    return path


def make_asset(
    asset_id: str = "test-asset-001",
    *,
    is_favorite: bool = False,
    file_created_at: datetime | None = None,
    original_file_name: str = "VID_001.MOV",
    exif_make: str | None = "Apple",
    exif_model: str | None = "iPhone 15 Pro",
    duration: str | int | float | None = "0:00:10.000",
) -> Asset:
    """Create an Asset with sensible defaults for testing."""
    now = file_created_at or datetime.now(tz=UTC)
    exif = ExifInfo(make=exif_make, model=exif_model) if exif_make or exif_model else None
    return Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=is_favorite,
        originalFileName=original_file_name,
        exifInfo=exif,
        duration=duration,
    )


def source_packet(pts: int, ticks: int, clock: int = 600) -> dict[str, float | int | str]:
    """One presentation packet as ProbeCache reports it, on the source's own clock."""
    return {
        "time_base": f"1/{clock}",
        "pts": pts,
        "duration_ticks": ticks,
        "start_seconds": pts / clock,
        "end_seconds": (pts + ticks) / clock,
        "frame_seconds": ticks / clock,
    }


@pytest.fixture()
def asset_payload() -> dict[str, object]:
    """Provide a minimal raw Immich asset response payload."""
    now = datetime.now(tz=UTC)
    return {
        "id": "asset-payload-001",
        "type": "VIDEO",
        "fileCreatedAt": now,
        "fileModifiedAt": now,
        "updatedAt": now,
    }


def make_clip(
    asset_id: str = "test-clip-001",
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float = 5.0,
    bitrate: int = 10_000_000,
    codec: str = "hevc",
    is_favorite: bool = False,
    color_transfer: str | None = None,
    exif_make: str | None = "Apple",
    exif_model: str | None = "iPhone 15 Pro",
    file_created_at: datetime | None = None,
) -> VideoClipInfo:
    """Create a VideoClipInfo with sensible defaults for testing."""
    asset = make_asset(
        asset_id,
        is_favorite=is_favorite,
        exif_make=exif_make,
        exif_model=exif_model,
        file_created_at=file_created_at,
        duration=f"0:00:{duration:06.3f}",
    )
    return VideoClipInfo(
        asset=asset,
        width=width,
        height=height,
        duration_seconds=duration,
        bitrate=bitrate,
        codec=codec,
        color_transfer=color_transfer,
    )


@pytest.fixture()
def mock_immich_client() -> MagicMock:
    """Mock SyncImmichClient with empty defaults."""
    client = MagicMock()
    client.get_videos_for_date_range.return_value = []
    client.get_all_videos_for_year.return_value = []
    client.get_asset_thumbnail.return_value = b"\x00" * 100
    return client


@pytest.fixture()
def mock_analysis_cache(tmp_path: Path) -> MagicMock:
    """Mock VideoAnalysisCache with empty defaults."""
    cache = MagicMock()
    cache.get_cached_analysis.return_value = None
    cache.db_path = tmp_path / "test_cache.db"
    return cache


@pytest.fixture()
def mock_thumbnail_cache(tmp_path: Path) -> MagicMock:
    """Mock ThumbnailCache with empty defaults."""
    cache = MagicMock()
    cache.get.return_value = None
    cache.cache_dir = tmp_path / "thumbnails"
    return cache


@pytest.fixture()
def tmp_output_dir(tmp_path: Path) -> Path:
    """Temporary directory for test output files."""
    output = tmp_path / "output"
    output.mkdir()
    return output


@pytest.fixture()
def sample_config() -> Config:
    """Provide a Config with safe defaults for testing."""
    return Config()


@pytest.fixture(autouse=True)
def _an_install_that_ran_models_fetch(request, monkeypatch):
    """Every run in the suite starts on a host that can finish it.

    The pinned exports are 110 MB and cannot live in the repo, so without this every
    CLI test would stop at the pre-run install checks. A test marked `install_checks`
    meets the real ones.
    """
    if request.node.get_closest_marker("install_checks"):
        return
    # WHY: model files and the output volume are install-time host state, not
    # what the CLI tests are about.
    monkeypatch.setattr("immich_memories.preflight_run.run_blockers", lambda *_args, **_kwargs: [])


@pytest.fixture(autouse=True)
def _forget_learned_endpoints():
    """Clear what one test taught the transport about a server, before the next runs.

    The reasoning headroom and the parameter dialect are learned per (server, model)
    and kept for the life of the process, which is right in a run and wrong across
    tests: with pytest-randomly the order changes every time, so one test teaching
    "api.anthropic.com/claude reasons anyway" silently rewrites another's expected
    token budget.
    """
    from immich_memories.analysis import llm_wire

    llm_wire._REASONING_HEADROOM.clear()
    llm_wire.PARAM_ADAPTATIONS.clear()
    yield
    llm_wire._REASONING_HEADROOM.clear()
    llm_wire.PARAM_ADAPTATIONS.clear()


@pytest.fixture(autouse=True)
def _open_the_throttle_gate():
    """Clear the shared provider pause one test shut, before the next one runs.

    A rate limit holds every caller in the process until the window the provider
    named has passed, which is right in a run. Tests fake the clock by replacing
    sleep, so the window never passes on its own and the next test inherits it.
    """
    from immich_memories.analysis import provider_failure

    provider_failure.THROTTLE._until = 0.0
    yield
    provider_failure.THROTTLE._until = 0.0


@pytest.fixture(autouse=True)
def _finished_cuts_keep_their_promises(monkeypatch):
    """Fail any planner test whose finished cut breaks a promise one of its passes made.

    In a run a broken promise is logged and recorded, and the cut ships. In a test it is a
    failure, so a change that lets a later pass undo an earlier one is caught by whichever
    planner fixture shows it.
    """
    from immich_memories.analysis import editorial_cut_invariants

    report = editorial_cut_invariants.report_violations

    def strict(violations, record):
        report(violations, record)
        assert not violations, "the finished cut breaks its promises:\n" + "\n".join(
            f"  {v.invariant}: {v.subject}: {v.detail} (last pass: {v.last_pass})"
            for v in violations
        )

    monkeypatch.setattr(editorial_cut_invariants, "report_violations", strict)


@pytest.fixture(autouse=True)
def _no_leaked_drain_threads():
    """Fail any test that leaves a stderr drain thread spinning.

    A mocked pipe whose read() never returns b"" (constant return_value, or a
    bare MagicMock — its __iter__ yields nothing and __bool__ is True) traps
    drain_stderr_tail in an infinite loop. The thread outlives the test at
    ~250k read()/s, MagicMock records every call, and the suite bloats by
    gigabytes until the 7 GB macOS CI runner kills pytest (exit 137).
    """
    yield
    leaked = [t for t in threading.enumerate() if "drain_stderr_tail" in t.name and t.is_alive()]
    assert not leaked, (
        f"stderr drain thread(s) outlived this test: {leaked} — a mocked pipe "
        'never reached EOF; use stderr.read.side_effect = [data, b""]'
    )


def pytest_collection_modifyitems(items) -> None:
    """Mark everything under tests/integration/ as integration, by path.

    WHY: `pytestmark` in a conftest does nothing, so a forgotten decorator
    silently runs an integration test in every unit job (#450). Path-based
    marking cannot be forgotten.
    """
    for item in items:
        if "tests/integration" in item.path.as_posix():
            item.add_marker("integration")


@pytest.fixture()
def every_run_an_occasion(monkeypatch):
    """A day-sequence reader that names every run it is offered as an occasion.

    For the special-day tests whose subject is what happens to a day after it is found:
    which runs the reader picks is `test_special_day_sequence.py`'s subject, not theirs.
    """
    import json
    import re

    # WHY: the sequence reader is a text-model call; these tests are about what follows it.
    monkeypatch.setattr(
        "immich_memories.analysis.special_day_sequence._read",
        lambda prompt, *_a, **_k: json.dumps(
            {
                "occasions": [
                    {"run": run, "what": "an occasion"}
                    for run in re.findall(r"^(R\d+) \|", prompt, re.MULTILINE)
                ]
            }
        ),
    )
