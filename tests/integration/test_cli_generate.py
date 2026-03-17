"""Integration tests for CLI generate and generate_memory() pipeline.

Real Immich reads, real FFmpeg, short clips (< 30s). Mocks only WRITES.
Skips gracefully if services unavailable.

Run: make test-integration
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


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


@pytest.fixture(scope="module")
def immich_short_clips():
    """Fetch 3 short clips (<30s) from a narrow date range. Module-scoped for speed."""
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import assets_to_clips
    from immich_memories.timeperiod import DateRange

    config = Config.from_yaml(Config.get_default_path())
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    # Narrow range (1 month) to avoid pulling a full year
    date_range = DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))
    assets = client.get_videos_for_date_range(date_range)

    # Fallback: try a wider range if January has nothing
    if not assets:
        date_range = DateRange(start=date(2024, 6, 1), end=date(2024, 12, 31))
        assets = client.get_videos_for_date_range(date_range)

    clips = assets_to_clips(assets)
    short = [c for c in clips if c.duration_seconds < 30]

    if len(short) < 2:
        pytest.skip("Need at least 2 short clips (<30s) in Immich")

    return short[:3], config, client


# ---------------------------------------------------------------------------
# generate_memory() pipeline tests — real Immich reads, real FFmpeg
# ---------------------------------------------------------------------------


@requires_immich
class TestGenerateMemoryPipeline:
    """End-to-end generate_memory() with real Immich + FFmpeg."""

    def test_two_clips_crossfade(self, immich_short_clips, tmp_path):
        """2 clips → crossfade → valid video output."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "crossfade.mp4"
        progress_phases = []

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            transition="crossfade",
            transition_duration=0.3,
            date_start=date(2024, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=False,
            progress_callback=lambda phase, _pct, _msg: progress_phases.append(phase),
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 1000
        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        assert has_stream(ffprobe_json(result), "video")
        assert get_duration(ffprobe_json(result)) > 0
        assert len(progress_phases) > 0

    def test_single_clip_cut(self, immich_short_clips, tmp_path):
        """1 clip → cut transition → valid video."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "single_cut.mp4"

        params = GenerationParams(
            clips=clips[:1],
            output_path=output,
            config=config,
            client=client,
            transition="cut",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=False,
        )

        result = generate_memory(params)
        assert result.exists()
        assert result.stat().st_size > 1000

    def test_upload_back_mocked_write(self, immich_short_clips, tmp_path):
        """Upload-back: real download + assembly, mocked upload POST."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "upload_test.mp4"

        params = GenerationParams(
            clips=clips[:1],
            output_path=output,
            config=config,
            client=client,
            transition="cut",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=True,
            upload_album="Test Album",
        )

        # WHY: upload_memory WRITES to Immich — mock writes only
        from unittest.mock import MagicMock

        mock_upload = MagicMock(return_value={"asset_id": "mock-id", "album_id": "mock-album"})
        with patch.object(client, "upload_memory", mock_upload):
            result = generate_memory(params)

        assert result.exists()
        mock_upload.assert_called_once()

    def test_empty_clips_raises_error(self, tmp_path):
        """generate_memory with empty clips should raise GenerationError."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationError, GenerationParams, generate_memory

        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "empty.mp4"

        params = GenerationParams(
            clips=[],
            output_path=output,
            config=config,
            transition="cut",
        )

        with pytest.raises(GenerationError, match="No clips"):
            generate_memory(params)


# ---------------------------------------------------------------------------
# CLI generate tests — real Immich + FFmpeg via CliRunner
# ---------------------------------------------------------------------------


@requires_immich
class TestCLIGenerate:
    """Test the actual CLI command with real Immich."""

    def test_cli_generate_produces_video(self, tmp_path):
        """CLI generate with short custom range → real video file."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        output = tmp_path / "cli_output.mp4"

        # Use a 1-month custom range for speed (not a full year)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-31",
                "--output",
                str(output),
            ],
            catch_exceptions=False,
        )

        # 0=success (video produced), 1=no clips found (fine for test env)
        assert result.exit_code in (0, 1), (
            f"Unexpected exit code {result.exit_code}: {result.output}"
        )
        if result.exit_code == 0:
            # Output may be in a run_id subdirectory
            mp4s = list(tmp_path.rglob("*.mp4"))
            assert len(mp4s) > 0, f"No .mp4 files found in {tmp_path}"
            assert mp4s[0].stat().st_size > 1000

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
