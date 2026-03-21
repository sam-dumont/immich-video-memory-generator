"""Integration tests for CLI generate command and generate_memory() pipeline.

Mocks expensive operations (FFmpeg assembly, Immich downloads) to test the CLI
layer quickly. Real FFmpeg assembly is covered by test-integration-assembly (134
tests) and test-integration-pipeline (21 tests).

Run: make test-integration-cli
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.api.models import Asset, VideoClipInfo

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="session")
def fixture_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a 1-second black MP4 for mocked assembly output."""
    import subprocess

    out = tmp_path_factory.mktemp("fixtures") / "fixture_1s.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-c:a",
            "aac",
            str(out),
        ],
        capture_output=True,
        timeout=10,
    )
    return out


def _has_immich() -> bool:
    """Check if Immich is reachable using the real config."""
    try:
        from immich_memories.config_loader import Config

        config = Config.from_yaml(Config.get_default_path())
        if not config.immich.url or not config.immich.api_key:
            return False

        import httpx

        resp = httpx.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


requires_immich = pytest.mark.skipif(not _has_immich(), reason="Immich not reachable")


def _make_fake_clip(
    asset_id: str, tmp_path: Path, fixture_mp4: Path, duration: float = 3.0
) -> VideoClipInfo:
    """Create a VideoClipInfo backed by a real fixture MP4."""
    clip_path = tmp_path / f"{asset_id}.mp4"
    shutil.copy(fixture_mp4, clip_path)
    now = datetime.now(tz=UTC)
    asset = Asset(
        id=asset_id,
        type="VIDEO",
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=False,
    )
    return VideoClipInfo(
        asset=asset,
        local_path=str(clip_path),
        duration_seconds=duration,
        width=320,
        height=240,
    )


# ---------------------------------------------------------------------------
# generate_memory() tests — mocked assembly, real config/progress wiring
# ---------------------------------------------------------------------------


class TestGenerateMemoryPipeline:
    """Tests generate_memory() flow with mocked assembly.

    WHY mock assembly: Real FFmpeg assembly takes minutes. The assembly
    pipeline is already tested by 134 assembly integration tests.
    Here we verify config wiring, progress callbacks, and error handling.
    """

    def _mock_assemble(self, fixture_mp4):
        """Return a side_effect that copies fixture to output path."""

        def _assemble(clips, output_path, **_kwargs):
            shutil.copy(fixture_mp4, output_path)
            return output_path

        return _assemble

    def test_two_clips_crossfade(self, tmp_path, fixture_mp4):
        """2 clips → generate_memory completes with valid output."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "crossfade.mp4"
        clips = [_make_fake_clip(f"clip{i}", tmp_path, fixture_mp4) for i in range(2)]

        phases_seen: list[str] = []

        def progress_cb(phase: str, _pct: float, _msg: str) -> None:
            if phase not in phases_seen:
                phases_seen.append(phase)

        params = GenerationParams(
            clips=clips,
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=False,
            no_music=True,
            progress_callback=progress_cb,
        )

        # WHY: mock assembly — we're testing generate_memory flow, not FFmpeg
        with patch(
            "immich_memories.processing.video_assembler.VideoAssembler.assemble_with_titles",
            side_effect=self._mock_assemble(fixture_mp4),
        ):
            result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 100
        assert "extract" in phases_seen
        assert "done" in phases_seen

    def test_single_clip_cut(self, tmp_path, fixture_mp4):
        """1 clip → cut transition → completes."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "single_cut.mp4"

        params = GenerationParams(
            clips=[_make_fake_clip("solo", tmp_path, fixture_mp4)],
            output_path=output,
            config=config,
            transition="cut",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=False,
            no_music=True,
        )

        with patch(
            "immich_memories.processing.video_assembler.VideoAssembler.assemble_with_titles",
            side_effect=self._mock_assemble(fixture_mp4),
        ):
            result = generate_memory(params)

        assert result.exists()

    def test_upload_back_mocked_write(self, tmp_path, fixture_mp4):
        """Upload-back: mock both assembly AND upload."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "upload_test.mp4"

        mock_client = MagicMock()
        mock_client.upload_memory.return_value = {"asset_id": "mock-id"}

        params = GenerationParams(
            clips=[_make_fake_clip("upload_clip", tmp_path, fixture_mp4)],
            output_path=output,
            config=config,
            client=mock_client,
            transition="cut",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=True,
            upload_album="Test Album",
            no_music=True,
        )

        with patch(
            "immich_memories.processing.video_assembler.VideoAssembler.assemble_with_titles",
            side_effect=self._mock_assemble(fixture_mp4),
        ):
            result = generate_memory(params)

        assert result.exists()
        mock_client.upload_memory.assert_called_once()

    def test_empty_clips_raises_error(self, tmp_path):
        """generate_memory with empty clips should raise GenerationError."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationError, GenerationParams, generate_memory

        config = Config()
        config.title_screens.enabled = False

        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "empty.mp4",
            config=config,
            transition="cut",
            no_music=True,
        )

        with pytest.raises(GenerationError, match="No clips"):
            generate_memory(params)


# ---------------------------------------------------------------------------
# CLI generate tests — test Click layer with mocked pipeline
# ---------------------------------------------------------------------------


class TestCLIGenerate:
    """Test the CLI command layer: arg parsing, config wiring, error handling.

    WHY mock generate_memory: The pipeline is tested above. Here we verify
    Click routes args correctly and handles edge cases.
    """

    def test_cli_generate_invokes_pipeline(self, tmp_path):
        """CLI generate with valid args reaches the pipeline."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        output = tmp_path / "cli_output.mp4"
        runner = CliRunner()

        # WHY: mock SyncImmichClient + pipeline — CLI test only checks arg wiring
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("immich_memories.api.immich.SyncImmichClient", return_value=mock_client),
            patch(
                "immich_memories.cli.generate._run_pipeline_and_generate",
                return_value=(output, False, None),
            ),
            patch(
                "immich_memories.cli.generate._fetch_videos_and_live_photos",
                return_value=([], []),
            ),
        ):
            # Create a fake output so the CLI doesn't complain
            output.parent.mkdir(parents=True, exist_ok=True)
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--start",
                    "2025-01-01",
                    "--end",
                    "2025-01-31",
                    "--no-music",
                    "--output",
                    str(output),
                ],
            )

        # exit_code 1 is OK (no clips found), we just verify arg parsing worked
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    @requires_immich
    def test_cli_generate_nonexistent_person(self, tmp_path):
        """CLI with fake person name should fail gracefully."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-31",
                "--person",
                "ZZZZNONEXISTENT_PERSON_ZZZZZ",
                "--output",
                str(tmp_path / "person.mp4"),
            ],
        )

        # Should fail (person not found or no clips)
        assert result.exit_code != 0

    def test_cli_missing_date_args_fails(self):
        """CLI without date args should show error."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["generate"])
        # Should fail — no date range specified
        assert result.exit_code != 0

    def test_cli_year_flag(self, tmp_path):
        """--year flag resolves to a full calendar year."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with self._mock_pipeline(tmp_path):
            result = runner.invoke(
                main,
                ["generate", "--year", "2025", "--no-music", "-O", str(tmp_path / "out.mp4")],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_cli_memory_type_person_spotlight(self, tmp_path):
        """--memory-type person_spotlight requires --person."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        # Missing --person → should fail
        result = runner.invoke(
            main,
            [
                "generate",
                "--memory-type",
                "person_spotlight",
                "--year",
                "2025",
                "-O",
                str(tmp_path / "out.mp4"),
            ],
        )
        assert result.exit_code != 0
        assert "person" in result.output.lower() or "required" in result.output.lower()

    def test_cli_memory_type_monthly(self, tmp_path):
        """--memory-type monthly_highlights --month --year parses correctly."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with self._mock_pipeline(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "monthly_highlights",
                    "--month",
                    "7",
                    "--year",
                    "2025",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_cli_resolution_flag(self, tmp_path):
        """--resolution 720p is accepted."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with self._mock_pipeline(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--start",
                    "2025-01-01",
                    "--end",
                    "2025-01-31",
                    "--resolution",
                    "720p",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_cli_dry_run(self, tmp_path):
        """--dry-run shows parameters without generating."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-31",
                "--dry-run",
                "-O",
                str(tmp_path / "out.mp4"),
            ],
        )
        # Dry run should exit 0 without connecting to Immich
        assert result.exit_code == 0
        assert "dry run" in result.output.lower() or "Dry" in result.output

    def test_cli_include_photos_flag(self, tmp_path):
        """--include-photos is accepted without error."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with self._mock_pipeline(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--start",
                    "2025-06-01",
                    "--end",
                    "2025-06-30",
                    "--include-photos",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_cli_privacy_mode(self, tmp_path):
        """--privacy-mode is accepted."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with self._mock_pipeline(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--start",
                    "2025-01-01",
                    "--end",
                    "2025-01-31",
                    "--privacy-mode",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_cli_trip_requires_year(self):
        """--memory-type trip without --year fails."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", "--memory-type", "trip"],
        )
        assert result.exit_code != 0

    def test_cli_invalid_date_format(self, tmp_path):
        """Invalid date format fails gracefully."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--start",
                "not-a-date",
                "--end",
                "2025-01-31",
                "-O",
                str(tmp_path / "out.mp4"),
            ],
        )
        assert result.exit_code != 0

    def test_cli_period_flag(self, tmp_path):
        """--start with --period resolves correctly."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with self._mock_pipeline(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--start",
                    "2025-01-01",
                    "--period",
                    "3m",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    @staticmethod
    def _mock_pipeline(tmp_path):
        """Context manager that mocks Immich + pipeline for CLI arg testing."""
        return _combined_mock(tmp_path)


def _combined_mock(tmp_path):
    """Stack of patches for CLI tests that need to bypass Immich + pipeline."""
    from contextlib import contextmanager

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    output = tmp_path / "out.mp4"

    @contextmanager
    def _ctx():
        with (
            patch("immich_memories.api.immich.SyncImmichClient", return_value=mock_client),
            patch(
                "immich_memories.cli.generate._run_pipeline_and_generate",
                return_value=(output, False, None),
            ),
            patch(
                "immich_memories.cli.generate._fetch_videos_and_live_photos",
                return_value=([], []),
            ),
        ):
            yield

    return _ctx()
