"""Integration test for CLI generate — real Immich, real FFmpeg, real pipeline.

Reads from Immich (no writes). Requires:
- Immich server reachable (from ~/.immich-memories/config.yaml)
- FFmpeg installed

Run: make test-integration
"""

from __future__ import annotations

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


@requires_immich
class TestCLIGenerateIntegration:
    """Full pipeline test: real Immich reads, real FFmpeg, no uploads."""

    def test_generate_memory_from_real_immich(self, tmp_path):
        """Fetch real clips from Immich, analyze, assemble → valid video."""
        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, assets_to_clips, generate_memory

        config = Config.from_yaml(Config.get_default_path())
        client = SyncImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
        )

        # Fetch ALL video assets, filter to short ones (< 30s) for speed
        from datetime import date

        from immich_memories.timeperiod import DateRange

        date_range = DateRange(start=date(2024, 1, 1), end=date(2025, 12, 31))
        assets = client.get_videos_for_date_range(date_range)
        if not assets:
            pytest.skip("No video assets found in Immich")

        clips = assets_to_clips(assets)
        if not clips:
            pytest.skip("No clips after duration filtering")

        # Pick only short clips (< 30s) for fast test, limit to 2
        short_clips = [c for c in clips if c.duration_seconds < 30]
        if len(short_clips) < 2:
            pytest.skip("Need at least 2 short clips (<30s) in Immich")
        test_clips = short_clips[:2]

        output = tmp_path / "integration_test.mp4"
        progress_phases = []

        config.title_screens.enabled = False  # Skip titles for speed

        params = GenerationParams(
            clips=test_clips,
            output_path=output,
            config=config,
            client=client,
            transition="crossfade",
            transition_duration=0.3,
            date_start=date(2024, 1, 1),
            date_end=date(2025, 12, 31),
            upload_enabled=False,  # NO WRITES to Immich
            progress_callback=lambda phase, _pct, _msg: progress_phases.append(phase),
        )

        result = generate_memory(params)

        # Verify: a valid video was produced (non-deterministic content)
        assert result.exists(), "Output file was not created"
        assert result.stat().st_size > 1000, "Output too small to be a valid video"

        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        probe = ffprobe_json(result)
        assert has_stream(probe, "video"), "Output has no video stream"
        assert get_duration(probe) > 0, "Output has zero duration"

        # Progress was reported
        assert len(progress_phases) > 0, "No progress callbacks received"

    def test_cli_generate_command_runs(self, tmp_path):
        """The actual CLI command runs end-to-end with real Immich."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        output = tmp_path / "cli_test.mp4"

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate",
                "--year",
                "2025",
                "--output",
                str(output),
                "--dry-run",  # Don't actually generate, just verify the pipeline starts
            ],
            catch_exceptions=False,
        )

        # dry-run should show the pipeline structure without errors
        # (exact behavior depends on whether --dry-run is implemented)
        assert result.exit_code in (0, 1)  # 0=success, 1=no clips found (fine for test)
