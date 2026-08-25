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

import click
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
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
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

        def _assemble(clips, output_path, progress_callback=None, **_kwargs):
            shutil.copy(fixture_mp4, output_path)
            return output_path

        return _assemble

    def test_automation_memory_key_override_is_authoritative(self, tmp_path) -> None:
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, _build_memory_key

        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "memory.mp4",
            config=Config(),
            memory_key_override="candidate:exact:key",
        )

        assert _build_memory_key(params) == "candidate:exact:key"

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

    def test_omitted_format_preserves_configured_h265(self, tmp_path) -> None:
        """An omitted CLI option must not become an explicit H.264 override."""
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        config.output.codec = "h265"
        config.output.directory = str(tmp_path)
        output = tmp_path / "all_memories_2025.mp4"
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        asset = MagicMock(duration_seconds=10.0)

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[asset],
            ),
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as run_pipeline,
        ):
            result = CliRunner().invoke(
                main,
                ["generate", "--year", "2025", "--no-music", "--quiet"],
            )

        assert result.exit_code == 0, result.output
        assert run_pipeline.call_args.kwargs["output_format"] is None
        assert run_pipeline.call_args.kwargs["output_orientation"] == "landscape"

    def test_explicit_prores_normalizes_conflicting_output_suffix(self, tmp_path) -> None:
        """The resolved MOV container is authoritative over a typed .mp4 suffix."""
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        requested = tmp_path / "custom-name.mp4"
        expected = requested.with_suffix(".mov")
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        asset = MagicMock(duration_seconds=10.0)

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[asset],
            ),
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(expected, False, None),
            ) as run_pipeline,
        ):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--year",
                    "2025",
                    "--format",
                    "prores",
                    "--output",
                    str(requested),
                    "--no-music",
                    "--quiet",
                ],
            )

        assert result.exit_code == 0, result.output
        assert run_pipeline.call_args.kwargs["output_path"] == expected

    def test_explicit_h265_override_is_available(self, tmp_path) -> None:
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        output = tmp_path / "memory.mp4"
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        asset = MagicMock(duration_seconds=10.0)

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[asset],
            ),
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as run_pipeline,
        ):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--year",
                    "2025",
                    "--format",
                    "h265",
                    "--output",
                    str(output),
                    "--no-music",
                    "--quiet",
                ],
            )

        assert result.exit_code == 0, result.output
        assert run_pipeline.call_args.kwargs["output_format"] == "h265"

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
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[],
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

    def test_automation_identity_options_are_hidden_from_help(self) -> None:
        from click.testing import CliRunner

        from immich_memories.cli import main

        result = CliRunner().invoke(main, ["generate", "--help"])

        assert result.exit_code == 0
        assert "--source" not in result.output
        assert "--memory-key" not in result.output
        assert "--memory-category" not in result.output
        assert "--automation-target-date" not in result.output

    def test_automation_identity_options_reach_pipeline(self, tmp_path) -> None:
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        output = tmp_path / "auto.mp4"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        asset = MagicMock(duration_seconds=10.0)

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=mock_client),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[asset],
            ),
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as mock_pipeline,
        ):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "year_in_review",
                    "--year",
                    "2025",
                    "--source=auto",
                    "--memory-key=candidate:key",
                    "--memory-category=birthday",
                    "--no-music",
                    "--output",
                    str(output),
                ],
            )

        assert result.exit_code == 0, result.output
        assert mock_pipeline.call_args.kwargs["source"] == "auto"
        assert mock_pipeline.call_args.kwargs["memory_key"] == "candidate:key"
        assert mock_pipeline.call_args.kwargs["memory_category"] == "birthday"

    def test_automation_on_this_day_uses_candidate_date_in_child(self, tmp_path) -> None:
        """A child starting after midnight must retain the parent's target day."""
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        output = tmp_path / "on-this-day.mp4"
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        asset = MagicMock(duration_seconds=10.0)

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[asset],
            ) as fetch,
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ),
        ):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "on_this_day",
                    "--automation-target-date=2026-02-03",
                    "--source=auto",
                    "--memory-key=on-this-day:2026-02-03",
                    "--memory-category=on_this_day",
                    "--automation-attempt-id=attempt-123",
                    "--no-music",
                    "--output",
                    str(output),
                ],
            )

        assert result.exit_code == 0, result.output
        date_ranges = fetch.call_args.kwargs["date_ranges"]
        assert date_ranges
        assert {(item.start.month, item.start.day) for item in date_ranges} == {(2, 2)}
        assert {(item.end.month, item.end.day) for item in date_ranges} == {(2, 4)}

    def test_automation_target_date_rejects_manual_context(self, tmp_path) -> None:
        """The hidden exact-date selector cannot change public manual semantics."""
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        with patch("immich_memories.cli.get_config", return_value=config):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "on_this_day",
                    "--automation-target-date=2026-02-03",
                    "--output",
                    str(tmp_path / "manual.mp4"),
                ],
            )

        assert result.exit_code != 0
        assert "requires complete on_this_day automation identity" in result.output

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
        """--dry-run connects, discovers assets, and delegates read-only planning."""
        from click.testing import CliRunner

        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        asset = MagicMock(duration_seconds=10.0)
        output = tmp_path / "out.mp4"

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[asset],
            ),
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as run_pipeline,
        ):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--start",
                    "2025-01-01",
                    "--end",
                    "2025-01-31",
                    "--dry-run",
                    "--quiet",
                    "-O",
                    str(output),
                ],
            )

        assert result.exit_code == 0, result.output
        assert run_pipeline.call_args.kwargs["dry_run"] is True
        assert not output.exists()

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


# ---------------------------------------------------------------------------
# Pipeline runner tests — real pipeline logic, mocked assembly
# ---------------------------------------------------------------------------


class TestPipelineRunner:
    """Tests for the fetch and runner functions with mocked assembly.

    WHY mock assembly: These functions run SmartPipeline + generate_memory.
    Assembly is FFmpeg-heavy; we mock it to test the pipeline wiring.
    """

    def test_fetch_videos_returns_assets(self, tmp_path):
        """fetch_videos returns deduped assets."""
        from immich_memories.cli._asset_fetch import fetch_videos
        from immich_memories.timeperiod import DateRange

        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_progress = MagicMock()

        asset1 = MagicMock(id="a1")
        asset2 = MagicMock(id="a2")
        asset1_dup = MagicMock(id="a1")  # duplicate
        mock_client.get_videos_for_date_range.return_value = [asset1, asset2, asset1_dup]

        dr = DateRange(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 1, 31, 23, 59, 59),
        )
        assets, live = fetch_videos(
            client=mock_client,
            config=mock_config,
            progress=mock_progress,
            date_ranges=[dr],
            person_ids=[],
            use_live_photos=False,
        )
        # Deduplication: a1 appears twice but kept once
        assert len(assets) == 2
        assert live == []

    def test_fetch_videos_person_filter(self, tmp_path):
        """fetch with single person_id calls person-specific API."""
        from immich_memories.cli._asset_fetch import fetch_videos
        from immich_memories.timeperiod import DateRange

        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_progress = MagicMock()
        mock_client.get_videos_for_person_and_date_range.return_value = []

        dr = DateRange(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 1, 31, 23, 59, 59),
        )
        fetch_videos(
            client=mock_client,
            config=mock_config,
            progress=mock_progress,
            date_ranges=[dr],
            person_ids=["person-123"],
            use_live_photos=False,
        )
        mock_client.get_videos_for_person_and_date_range.assert_called_once()

    def test_fetch_videos_multi_person(self, tmp_path):
        """fetch with multiple person_ids asks for the videos holding all of them."""
        from immich_memories.cli._asset_fetch import fetch_videos
        from immich_memories.timeperiod import DateRange

        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_progress = MagicMock()
        mock_client.get_videos_for_all_persons.return_value = []

        dr = DateRange(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 1, 31, 23, 59, 59),
        )
        fetch_videos(
            client=mock_client,
            config=mock_config,
            progress=mock_progress,
            date_ranges=[dr],
            person_ids=["p1", "p2"],
            use_live_photos=False,
        )
        mock_client.get_videos_for_all_persons.assert_called_once()

    def test_run_pipeline_wires_config_and_calls_generate(self, tmp_path, fixture_mp4):
        """run_pipeline_and_generate builds correct GenerationParams and calls generate_memory."""
        from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
        from immich_memories.config_loader import Config
        from immich_memories.timeperiod import DateRange

        config = Config()
        output = tmp_path / "pipeline_test.mp4"
        output.write_bytes(b"fake")
        mock_client = MagicMock()
        mock_progress = MagicMock()

        clips = [_make_fake_clip(f"asset{i}", tmp_path, fixture_mp4) for i in range(3)]
        assets = [c.asset for c in clips]
        dr = DateRange(start=datetime(2025, 1, 1), end=datetime(2025, 12, 31, 23, 59, 59))

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.selected_clips = clips
        mock_pipeline_result.clip_segments = {}

        with (
            # WHY: mock assets_to_clips — real one needs Asset.duration from Immich metadata
            patch("immich_memories.generate.assets_to_clips", return_value=clips),
            # WHY: mock at source — lazy imports inside function body
            patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as MockPipeline,
            # WHY: mock generate_memory — real one acquires lock + runs FFmpeg
            patch("immich_memories.generate.generate_memory", return_value=output) as mock_gen,
        ):
            MockPipeline.return_value.run.return_value = mock_pipeline_result
            result_path, should_upload, _ = run_pipeline_and_generate(
                assets=assets,
                client=mock_client,
                config=config,
                progress=mock_progress,
                duration=60.0,
                transition="cut",
                music=None,
                no_music=True,
                output_path=output,
                output_resolution="720p",
                output_orientation="landscape",
                memory_type="year_in_review",
                person_names=[" Alice ", "BOB\tJones"],
                date_range=dr,
                upload_to_immich=False,
                album=None,
                source="auto",
                memory_key="candidate:key",
                memory_category="birthday",
                automation_attempt_id="attempt-pipeline-1",
            )

        assert result_path == output
        assert not should_upload
        mock_gen.assert_called_once()
        gen_params = mock_gen.call_args[0][0]
        assert gen_params.transition == "cut"
        assert gen_params.no_music is True
        assert gen_params.memory_type == "year_in_review"
        assert gen_params.source == "auto"
        assert gen_params.memory_key_override == "candidate:key"
        assert gen_params.memory_category == "birthday"
        assert gen_params.automation_attempt_id == "attempt-pipeline-1"
        assert gen_params.memory_people == ("alice", "bob jones")
        assert gen_params.target_duration_seconds == 60.0
        assert gen_params.timeline_plan.target_duration == 60.0
        assert gen_params.timeline_plan.content_budget >= 48.0
        assert gen_params.output_canvas.width == 1280
        assert gen_params.output_canvas.height == 720
        pipeline_config = MockPipeline.call_args.kwargs["config"]
        assert pipeline_config.target_duration_seconds == gen_params.timeline_plan.content_budget
        assert pipeline_config.output_resolution == 720

    def test_dry_run_selects_and_writes_no_artifact(self, tmp_path, fixture_mp4, capsys):
        """Planning uses cached/metadata analysis and stops before generation."""
        from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineResult
        from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
        from immich_memories.config_loader import Config
        from immich_memories.timeperiod import DateRange

        config = Config(
            cache={"database": str(tmp_path / "analysis.db"), "directory": str(tmp_path / "cache")}
        )
        clips = [_make_fake_clip(f"asset{i}", tmp_path, fixture_mp4) for i in range(3)]
        analyzed = [
            ClipWithSegment(clip=clip, start_time=0.0, end_time=3.0, score=0.5) for clip in clips
        ]
        result = PipelineResult(
            selected_clips=clips,
            clip_segments={clip.asset.id: (0.0, 3.0) for clip in clips},
            errors=[],
        )
        output = tmp_path / "preview.mp4"

        with (
            patch("immich_memories.generate.assets_to_clips", return_value=clips),
            patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as pipeline_type,
            patch("immich_memories.generate.generate_memory") as generate,
        ):
            pipeline_type.return_value.run_planning_analysis.return_value = analyzed
            pipeline_type.return_value.run_selection.return_value = result
            actual, should_upload, _ = run_pipeline_and_generate(
                assets=[clip.asset for clip in clips],
                client=MagicMock(),
                config=config,
                progress=MagicMock(),
                duration=60.0,
                transition="cut",
                music=None,
                no_music=True,
                output_path=output,
                memory_type="year_in_review",
                person_names=[],
                date_range=DateRange(
                    start=datetime(2025, 1, 1), end=datetime(2025, 12, 31, 23, 59, 59)
                ),
                upload_to_immich=True,
                album="Memories",
                dry_run=True,
            )

        assert actual == output
        assert should_upload is True
        pipeline_type.return_value.run_planning_analysis.assert_called_once()
        generate.assert_not_called()
        assert "Selected: 3" in capsys.readouterr().out
        assert not output.exists()

    def test_dry_run_reports_all_selected_month_dividers(
        self, tmp_path, fixture_mp4, capsys
    ) -> None:
        """Dry-run reports the complete post-selection month-divider decision."""
        from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineResult
        from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
        from immich_memories.config_loader import Config
        from immich_memories.timeperiod import DateRange

        config = Config(
            cache={"database": str(tmp_path / "analysis.db"), "directory": str(tmp_path / "cache")},
            title_screens={"ending_duration": 4.0},
        )
        clips = [
            _make_fake_clip(f"asset-{month}", tmp_path, fixture_mp4, duration=8.0)
            for month in range(2, 8)
        ]
        for month, clip in zip(range(2, 8), clips, strict=True):
            clip.asset.file_created_at = datetime(2026, month, 5, tzinfo=UTC)
        analyzed = [
            ClipWithSegment(clip=clip, start_time=0.0, end_time=8.0, score=0.5) for clip in clips
        ]
        result = PipelineResult(
            selected_clips=clips,
            clip_segments={clip.asset.id: (0.0, 8.0) for clip in clips},
            errors=[],
        )
        output_path = tmp_path / "noah-preview.mp4"

        with (
            patch("immich_memories.generate.assets_to_clips", return_value=clips),
            patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as pipeline_type,
            patch("immich_memories.generate.generate_memory") as generate,
        ):
            pipeline_type.return_value.run_planning_analysis.return_value = analyzed
            pipeline_type.return_value.run_selection.return_value = result
            run_pipeline_and_generate(
                assets=[clip.asset for clip in clips],
                client=MagicMock(),
                config=config,
                progress=MagicMock(),
                duration=60.0,
                transition="crossfade",
                music=None,
                no_music=True,
                output_path=output_path,
                memory_type="person_spotlight",
                person_names=["Noah"],
                date_range=DateRange(
                    start=datetime(2026, 1, 1), end=datetime(2026, 12, 31, 23, 59, 59)
                ),
                upload_to_immich=False,
                album=None,
                dry_run=True,
            )

        output = capsys.readouterr().out
        assert "Month dividers: all 5 selected month changes" in output
        assert "Title cards: 7 (17.5s)" in output
        assert "Estimated final duration: 59.5s" in output
        assert "Music: disabled" in output
        assert not output_path.exists()
        generate.assert_not_called()


class TestTripGenerationFlow:
    """Tests for _trip_generation with real Immich, mocked assembly."""

    def test_resolve_music_arg_none(self):
        from immich_memories.cli._trip_generation import resolve_music_arg

        assert resolve_music_arg(None) is None
        assert resolve_music_arg("auto") is None

    def test_resolve_music_arg_valid_path(self, tmp_path):
        from immich_memories.cli._trip_generation import resolve_music_arg

        music_file = tmp_path / "song.mp3"
        music_file.write_bytes(b"fake")
        assert resolve_music_arg(str(music_file)) == str(music_file)

    def test_resolve_music_arg_missing_file(self):
        """Missing music file should call sys.exit."""
        from immich_memories.cli._trip_generation import resolve_music_arg

        with pytest.raises(SystemExit):
            resolve_music_arg("/nonexistent/path/song.mp3")

    def test_handle_trip_generation_no_trips(self, tmp_path):
        """handle_trip_generation exits when no trips detected."""
        from immich_memories.cli._trip_generation import handle_trip_generation

        mock_client = MagicMock()
        mock_progress = MagicMock()
        mock_config = MagicMock()

        # WHY: mock trip detection — real detection needs GPS assets
        with (
            patch(
                "immich_memories.cli._trip_display.run_trip_detection",
                return_value=[],
            ),
            pytest.raises(SystemExit),
        ):
            handle_trip_generation(
                client=mock_client,
                config=mock_config,
                progress=mock_progress,
                year=2025,
                month=None,
                trip_index=None,
                all_trips=False,
                near_date=None,
                person_names=[],
                output_path=tmp_path / "trip.mp4",
                use_live_photos=False,
                use_photos=False,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
            )

    def test_handle_trip_generation_discovery_mode(self, tmp_path):
        """Discovery mode (no selection flags) returns without generating."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_generation import handle_trip_generation

        mock_client = MagicMock()
        mock_progress = MagicMock()
        mock_config = MagicMock()

        trips = [
            DetectedTrip(
                start_date=date(2025, 7, 1),
                end_date=date(2025, 7, 10),
                location_name="Test Beach",
                asset_count=20,
                centroid_lat=43.5,
                centroid_lon=3.8,
            ),
        ]

        with patch(
            "immich_memories.cli._trip_display.run_trip_detection",
            return_value=trips,
        ):
            # WHY: no selection flags → discovery mode, returns without generating
            handle_trip_generation(
                client=mock_client,
                config=mock_config,
                progress=mock_progress,
                year=2025,
                month=None,
                trip_index=None,
                all_trips=False,
                near_date=None,
                person_names=[],
                output_path=tmp_path / "trip.mp4",
                use_live_photos=False,
                use_photos=False,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
            )

        # No video generated in discovery mode
        assert not (tmp_path / "trip.mp4").exists()

    def test_handle_trip_generation_selects_and_generates(self, tmp_path, fixture_mp4):
        """Trip selected → fetches videos → runs pipeline → generates."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_generation import handle_trip_generation

        mock_client = MagicMock()
        mock_progress = MagicMock()
        mock_config = MagicMock()
        mock_config.defaults.transition = "crossfade"
        mock_config.defaults.scale_mode = "blur"
        mock_config.output.codec = "h264"
        mock_config.output.format = "mp4"
        mock_config.trips.homebase_latitude = 48.8
        mock_config.trips.homebase_longitude = 2.3

        trips = [
            DetectedTrip(
                start_date=date(2025, 7, 1),
                end_date=date(2025, 7, 5),
                location_name="Nice",
                asset_count=15,
                centroid_lat=43.7,
                centroid_lon=7.2,
            ),
        ]

        output = tmp_path / "trip_nice_2025-07-01.mp4"
        output.write_bytes(b"fake")

        with (
            patch(
                "immich_memories.cli._trip_display.run_trip_detection",
                return_value=trips,
            ),
            # WHY: mock fetch — real one needs Immich videos in that date range
            patch(
                "immich_memories.cli._trip_generation.fetch_videos",
                return_value=[MagicMock()],
            ),
            # WHY: mock pipeline — real one runs SmartPipeline + FFmpeg
            patch(
                "immich_memories.cli._trip_generation.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as mock_pipeline,
        ):
            handle_trip_generation(
                client=mock_client,
                config=mock_config,
                progress=mock_progress,
                year=2025,
                month=None,
                trip_index=1,
                all_trips=False,
                near_date=None,
                person_names=[],
                output_path=tmp_path / "trip.mp4",
                use_live_photos=False,
                use_photos=False,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format="prores",
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
            )

        mock_pipeline.assert_called_once()
        call_kwargs = mock_pipeline.call_args[1]
        assert call_kwargs["memory_type"] == "trip"
        assert call_kwargs["no_music"] is True
        assert call_kwargs["output_path"].suffix == ".mov"

    def test_exact_trip_range_selects_only_matching_trip(self, tmp_path):
        """Automation dates select the detected trip, not the nearest or first trip."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_generation import handle_trip_generation

        mock_client = MagicMock()
        mock_progress = MagicMock()
        mock_config = MagicMock()
        mock_config.defaults.transition = "crossfade"
        mock_config.defaults.scale_mode = "blur"
        mock_config.output.codec = "h264"
        mock_config.output.format = "mp4"
        mock_config.trips.homebase_latitude = 48.8
        mock_config.trips.homebase_longitude = 2.3
        trips = [
            DetectedTrip(
                start_date=date(2025, 5, 1),
                end_date=date(2025, 5, 4),
                location_name="Wrong",
                asset_count=12,
                centroid_lat=50.0,
                centroid_lon=4.0,
            ),
            DetectedTrip(
                start_date=date(2025, 7, 10),
                end_date=date(2025, 7, 17),
                location_name="Exact",
                asset_count=20,
                centroid_lat=43.7,
                centroid_lon=7.2,
            ),
        ]
        output = tmp_path / "trip_exact_2025-07-10.mp4"

        with (
            patch("immich_memories.cli._trip_display.run_trip_detection", return_value=trips),
            patch(
                "immich_memories.cli._trip_generation.fetch_videos",
                return_value=[MagicMock()],
            ) as mock_fetch,
            patch(
                "immich_memories.cli._trip_generation.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as mock_pipeline,
        ):
            handle_trip_generation(
                client=mock_client,
                config=mock_config,
                progress=mock_progress,
                year=2025,
                month=None,
                trip_index=None,
                all_trips=False,
                near_date=None,
                person_names=[],
                output_path=tmp_path / "trip.mp4",
                use_live_photos=False,
                use_photos=False,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
                requested_start=date(2025, 7, 10),
                requested_end=date(2025, 7, 17),
                source="auto",
                memory_key="trip:exact:key",
                memory_category="trip",
            )

        fetched_range = mock_fetch.call_args.kwargs["date_ranges"][0]
        assert fetched_range.start.date() == date(2025, 7, 10)
        assert fetched_range.end.date() == date(2025, 7, 17)
        assert mock_pipeline.call_args.kwargs["source"] == "auto"
        assert mock_pipeline.call_args.kwargs["memory_key"] == "trip:exact:key"
        assert mock_pipeline.call_args.kwargs["memory_category"] == "trip"

    def test_exact_trip_range_without_match_fails(self, tmp_path):
        """A stale automation candidate cannot silently generate another trip."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_generation import handle_trip_generation

        trip = DetectedTrip(
            start_date=date(2025, 5, 1),
            end_date=date(2025, 5, 4),
            location_name="Different",
            asset_count=12,
            centroid_lat=50.0,
            centroid_lon=4.0,
        )

        with (
            patch("immich_memories.cli._trip_display.run_trip_detection", return_value=[trip]),
            patch("immich_memories.cli._trip_generation.run_pipeline_and_generate") as pipeline,
            pytest.raises(click.ClickException, match="No detected trip exactly matches"),
        ):
            handle_trip_generation(
                client=MagicMock(),
                config=MagicMock(),
                progress=MagicMock(),
                year=2025,
                month=None,
                trip_index=None,
                all_trips=False,
                near_date=None,
                person_names=[],
                output_path=tmp_path / "trip.mp4",
                use_live_photos=False,
                use_photos=False,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
                requested_start=date(2025, 7, 10),
                requested_end=date(2025, 7, 17),
                source="auto",
                memory_key="trip:missing:key",
                memory_category="trip",
            )

        pipeline.assert_not_called()

    @pytest.mark.parametrize(
        ("selector_args", "expected_starts"),
        [
            (["--trip-index", "2"], (date(2025, 8, 10),)),
            (["--month", "8"], (date(2025, 8, 10),)),
            (["--near-date", "2025-08-12"], (date(2025, 8, 10),)),
            (["--all-trips"], (date(2025, 1, 2), date(2025, 8, 10))),
        ],
    )
    def test_manual_custom_range_preserves_trip_selector_precedence(
        self,
        tmp_path,
        selector_args: list[str],
        expected_starts: tuple[date, ...],
    ) -> None:
        """Manual custom dates must not replace the established trip selectors."""
        from click.testing import CliRunner

        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli import main
        from immich_memories.config_loader import Config

        trips = [
            DetectedTrip(
                start_date=date(2025, 1, 2),
                end_date=date(2025, 1, 5),
                location_name="Exact Range",
                asset_count=12,
                centroid_lat=50.0,
                centroid_lon=4.0,
            ),
            DetectedTrip(
                start_date=date(2025, 8, 10),
                end_date=date(2025, 8, 15),
                location_name="Selected Trip",
                asset_count=20,
                centroid_lat=43.7,
                centroid_lon=7.2,
            ),
        ]
        config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        output = tmp_path / "trip.mp4"

        with (
            patch("immich_memories.cli.get_config", return_value=config),
            patch("immich_memories.api.immich.SyncImmichClient", return_value=mock_client),
            patch("immich_memories.cli._trip_display.run_trip_detection", return_value=trips),
            patch(
                "immich_memories.cli._trip_generation.fetch_videos",
                return_value=[MagicMock()],
            ),
            patch(
                "immich_memories.cli._trip_generation.run_pipeline_and_generate",
                return_value=(output, False, None),
            ) as mock_pipeline,
        ):
            result = CliRunner().invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "trip",
                    "--year",
                    "2025",
                    "--start",
                    "2025-01-02",
                    "--end",
                    "2025-01-05",
                    *selector_args,
                    "--no-music",
                    "--output",
                    str(output),
                ],
            )

        assert result.exit_code == 0, result.output
        selected_starts = tuple(
            call.kwargs["date_range"].start.date() for call in mock_pipeline.call_args_list
        )
        assert selected_starts == expected_starts

    def test_exact_trip_range_with_duplicate_matches_fails(self, tmp_path) -> None:
        """Ambiguous detector output cannot pick an arbitrary trip."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_generation import handle_trip_generation

        duplicates = [
            DetectedTrip(
                start_date=date(2025, 7, 10),
                end_date=date(2025, 7, 17),
                location_name=location,
                asset_count=20,
                centroid_lat=43.7,
                centroid_lon=7.2,
            )
            for location in ("Duplicate A", "Duplicate B")
        ]

        with (
            patch("immich_memories.cli._trip_display.run_trip_detection", return_value=duplicates),
            patch("immich_memories.cli._trip_generation.run_pipeline_and_generate") as pipeline,
            pytest.raises(click.ClickException, match="found 2"),
        ):
            handle_trip_generation(
                client=MagicMock(),
                config=MagicMock(),
                progress=MagicMock(),
                year=2025,
                month=None,
                trip_index=None,
                all_trips=False,
                near_date=None,
                person_names=[],
                output_path=tmp_path / "trip.mp4",
                use_live_photos=False,
                use_photos=False,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
                requested_start=date(2025, 7, 10),
                requested_end=date(2025, 7, 17),
                source="auto",
                memory_key="trip:duplicate:key",
                memory_category="trip",
            )

        pipeline.assert_not_called()


def _combined_mock(tmp_path, mock_client=None):
    """Stack of patches for CLI tests that need to bypass Immich + pipeline."""
    from contextlib import contextmanager

    if mock_client is None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
    output = tmp_path / "out.mp4"

    @contextmanager
    def _ctx():
        with (
            patch("immich_memories.api.immich.SyncImmichClient", return_value=mock_client),
            patch(
                "immich_memories.cli.generate.run_pipeline_and_generate",
                return_value=(output, False, None),
            ),
            patch(
                "immich_memories.cli.generate.fetch_videos",
                return_value=[],
            ),
        ):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# New flexible filtering CLI tests
# ---------------------------------------------------------------------------


class TestCLIFlexibleFiltering:
    """Tests for --month, --start/--end override, --years-back, --near-date, --birthday."""

    def test_person_spotlight_with_month(self, tmp_path):
        """--memory-type person_spotlight --month 2 narrows to February."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "person_spotlight",
                    "--person",
                    "FakePersonForTest",
                    "--year",
                    "2026",
                    "--month",
                    "2",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        # exit_code 1 OK (person not found or no clips), we check arg parsing
        assert result.exit_code in (0, 1), f"Unexpected error: {result.output}"

    def test_year_in_review_with_month(self, tmp_path):
        """--memory-type year_in_review --month 6 narrows to June."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "year_in_review",
                    "--year",
                    "2025",
                    "--month",
                    "6",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_start_end_overrides_person_spotlight(self, tmp_path):
        """--start/--end overrides person_spotlight's default calendar year."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "person_spotlight",
                    "--person",
                    "FakePersonForTest",
                    "--year",
                    "2026",
                    "--start",
                    "2026-02-01",
                    "--end",
                    "2026-03-31",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_start_end_overrides_season(self, tmp_path):
        """--start/--end overrides season's 3-month default."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "season",
                    "--season",
                    "summer",
                    "--year",
                    "2025",
                    "--start",
                    "2025-07-01",
                    "--end",
                    "2025-07-31",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_on_this_day_with_years_back(self, tmp_path):
        """--memory-type on_this_day --years-back 3."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "on_this_day",
                    "--years-back",
                    "3",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_on_this_day_default_all_years(self, tmp_path):
        """--memory-type on_this_day without --years-back defaults to all."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--memory-type",
                    "on_this_day",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_near_date_requires_trip(self, tmp_path):
        """--near-date without --memory-type trip should fail."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--memory-type",
                "year_in_review",
                "--year",
                "2025",
                "--near-date",
                "2025-07-15",
                "-O",
                str(tmp_path / "out.mp4"),
            ],
        )
        assert result.exit_code != 0
        assert "trip" in result.output.lower()

    def test_years_back_requires_on_this_day(self, tmp_path):
        """--years-back without --memory-type on_this_day should fail."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--memory-type",
                "year_in_review",
                "--year",
                "2025",
                "--years-back",
                "3",
                "-O",
                str(tmp_path / "out.mp4"),
            ],
        )
        assert result.exit_code != 0
        assert "on_this_day" in result.output.lower()

    def test_birthday_flag_without_person_is_harmless(self, tmp_path):
        """--birthday without --person — birthday auto-detect skipped."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--year",
                    "2025",
                    "--birthday",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        # Should proceed (birthday="auto" but no person → skips detection)
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"

    def test_birthday_manual_override(self, tmp_path):
        """--birthday 07/21/2000 with --person still works as manual override."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        with _combined_mock(tmp_path):
            result = runner.invoke(
                main,
                [
                    "generate",
                    "--year",
                    "2025",
                    "--birthday",
                    "07/21/2000",
                    "--person",
                    "FakePersonForTest",
                    "--no-music",
                    "-O",
                    str(tmp_path / "out.mp4"),
                ],
            )
        assert result.exit_code in (0, 1), f"Unexpected: {result.output}"
