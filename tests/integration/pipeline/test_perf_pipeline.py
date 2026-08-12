"""Full pipeline per-step benchmark (requires Immich + FFmpeg).

Measures wall time, CPU time, and memory for each pipeline phase using
real Immich clips. Run with: make benchmark-pipeline

Exports results to tests/perf-results-pipeline.json for Mac vs Linux
GPU comparison.
"""

from __future__ import annotations

import logging
from datetime import date
from fractions import Fraction
from pathlib import Path
from subprocess import run as sp_run

import numpy as np
import pytest

from tests.integration.assembly.perf_utils import (
    BenchmarkSummary,
    run_repetitions,
    save_benchmark_json,
    save_summary_results,
)
from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)
from tests.integration.immich_fixtures import requires_immich

logger = logging.getLogger("test.perf.pipeline")

pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich, pytest.mark.perf]

_pipeline_summaries: list[BenchmarkSummary] = []


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


def _extract_frame_pixels(video_path: Path, timestamp: float) -> np.ndarray:
    result = sp_run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0 or len(result.stdout) == 0:
        return np.array([], dtype=np.uint8)
    return np.frombuffer(result.stdout, dtype=np.uint8)


def _input_metadata(clips, client, temporary_root: Path) -> tuple[float, str, float | str]:
    """Probe exact source identity outside the timed benchmark operation."""
    input_root = temporary_root / "benchmark-inputs"
    input_root.mkdir(parents=True, exist_ok=True)
    durations: list[float] = []
    codecs: set[str] = set()
    frame_rates: set[float] = set()
    for index, clip in enumerate(clips[:2]):
        suffix = Path(clip.asset.original_file_name or "video.mp4").suffix or ".mp4"
        input_path = input_root / f"input_{index}{suffix.lower()}"
        client.download_asset(clip.video_asset_id, input_path)
        probe = ffprobe_json(input_path)
        stream = next(item for item in probe["streams"] if item["codec_type"] == "video")
        codec = str(stream["codec_name"]).lower().replace("hevc", "h265")
        rate = round(float(Fraction(str(stream["avg_frame_rate"]))), 3)
        codecs.add(codec)
        frame_rates.add(rate)
        durations.append(get_duration(probe))

    frame_rate: float | str
    if len(frame_rates) == 1:
        frame_rate = next(iter(frame_rates))
    else:
        frame_rate = "+".join(f"{rate:g}" for rate in sorted(frame_rates))
    return sum(durations), "+".join(sorted(codecs)), frame_rate


def _validate_pipeline_output(
    final: Path,
    *,
    minimum_duration: float,
    title_timestamp: float | None = None,
    content_timestamp: float | None = None,
) -> Path:
    assert final.exists(), f"Output not created: {final}"
    probe = ffprobe_json(final)
    assert has_stream(probe, "video")
    duration = get_duration(probe)
    assert duration > minimum_duration, f"Duration too short: {duration:.1f}s"
    width, height = _get_resolution(probe)
    assert max(width, height) <= 1280, f"Resolution exceeds 720p: {width}x{height}"
    if title_timestamp is not None:
        pixels = _extract_frame_pixels(final, title_timestamp)
        assert len(pixels) > 0, "Failed to extract title frame"
        assert float(np.mean(pixels)) > 3.0, "Title frame appears blank"
    if content_timestamp is not None:
        pixels = _extract_frame_pixels(final, content_timestamp)
        assert len(pixels) > 0, "Failed to extract content frame"
        assert float(np.std(pixels)) > 3.0, "Content frame lacks detail"
    return final


def _run_pipeline_benchmark(
    operation,
    *,
    scenario: str,
    clips,
    client,
    tmp_path: Path,
) -> BenchmarkSummary:
    duration, codec, frame_rate = _input_metadata(clips, client, tmp_path)
    assert codec != "unknown", "Pipeline benchmark must record the input codec"
    assert frame_rate not in {0, "", "unknown"}, "Pipeline benchmark must record input fps"
    summary = run_repetitions(
        operation,
        scenario=scenario,
        output_dir=tmp_path / "benchmark-output",
        clip_count=2,
        resolution="1280x720",
        input_duration_seconds=duration,
        codec=codec,
        frame_rate=frame_rate,
    )
    logger.info(summary.summary_line)
    _pipeline_summaries.append(summary)
    return summary


def _isolated_config(config, temporary_root: Path, scenario: str):
    """Give each scenario one cold application cache and disposable database."""
    isolated = config.model_copy(deep=True)
    scenario_root = temporary_root / scenario
    isolated.cache.directory = str(scenario_root / "cache")
    isolated.cache.database = str(scenario_root / "cache.db")
    return isolated


class TestFullPipelineBenchmark:
    """End-to-end pipeline timing with real Immich clips.

    Measures: clip download/extraction, title rendering, assembly,
    music mixing, and total pipeline time. Verifies real output at
    each step (resolution, pixel content, audio streams).
    """

    def test_immich_assembly_only(self, immich_short_clips, tmp_path):
        """Immich clips -> assembly only (no titles, no music). Baseline measurement."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config = _isolated_config(config, tmp_path, "immich_assembly_only")
        config.title_screens.enabled = False

        def generate(output: Path) -> Path:
            params = GenerationParams(
                clips=clips[:2],
                output_path=output,
                config=config,
                client=client,
                transition="crossfade",
                transition_duration=0.3,
                output_resolution="720p",
                no_music=True,
            )
            final = generate_memory(params)
            return _validate_pipeline_output(
                final,
                minimum_duration=2.0,
                content_timestamp=1.0,
            )

        summary = _run_pipeline_benchmark(
            generate,
            scenario="immich_assembly_only",
            clips=clips,
            client=client,
            tmp_path=tmp_path,
        )
        assert len(summary.repetitions) == 3

    def test_immich_with_titles(self, immich_short_clips, tmp_path):
        """Immich clips -> titles + assembly. Measures title rendering overhead."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config = _isolated_config(config, tmp_path, "immich_with_titles")
        config.title_screens.enabled = True
        config.title_screens.style_mode = "elegant_minimal"
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0

        def generate(output: Path) -> Path:
            params = GenerationParams(
                clips=clips[:2],
                output_path=output,
                config=config,
                client=client,
                transition="crossfade",
                transition_duration=0.3,
                output_resolution="720p",
                person_name="Benchmark Person",
                date_start=date(2025, 1, 1),
                date_end=date(2025, 12, 31),
                memory_type="year_in_review",
                no_music=True,
            )
            final = generate_memory(params)
            return _validate_pipeline_output(
                final,
                minimum_duration=4.0,
                title_timestamp=0.5,
            )

        summary = _run_pipeline_benchmark(
            generate,
            scenario="immich_with_titles",
            clips=clips,
            client=client,
            tmp_path=tmp_path,
        )
        assert summary.median_wall_seconds > 0

    def test_immich_full_pipeline(
        self,
        immich_short_clips,
        test_music_short,
        tmp_path,
        monkeypatch,
    ):
        """Immich clips -> titles + music + assembly. Full pipeline measurement."""
        import immich_memories.generate as generate_module
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config = _isolated_config(config, tmp_path, "immich_full_pipeline")
        config.title_screens.enabled = True
        config.title_screens.style_mode = "elegant_minimal"
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        original_music_phase = generate_module._run_music_phase
        music_applied: list[bool] = []

        def observe_music_phase(*args, **kwargs):
            result = original_music_phase(*args, **kwargs)
            music_applied.append(result.applied)
            return result

        monkeypatch.setattr(generate_module, "_run_music_phase", observe_music_phase)

        def generate(output: Path) -> Path:
            params = GenerationParams(
                clips=clips[:2],
                output_path=output,
                config=config,
                client=client,
                transition="smart",
                transition_duration=0.3,
                output_resolution="720p",
                person_name="Benchmark Person",
                date_start=date(2025, 1, 1),
                date_end=date(2025, 12, 31),
                memory_type="year_in_review",
                music_path=test_music_short,
                no_music=False,
            )
            final = generate_memory(params)
            validated = _validate_pipeline_output(
                final,
                minimum_duration=4.0,
                title_timestamp=0.5,
                content_timestamp=4.0,
            )
            assert validated.stat().st_size > 10_485
            assert has_stream(ffprobe_json(validated), "audio")
            return validated

        summary = _run_pipeline_benchmark(
            generate,
            scenario="immich_full_pipeline",
            clips=clips,
            client=client,
            tmp_path=tmp_path,
        )
        assert music_applied == [True] * 4, "Music must be applied in warmup and every repetition"
        assert summary.median_wall_seconds > 0


def test_save_pipeline_results(tmp_path):
    """Save collected pipeline results to JSON after all scenarios complete."""
    if not _pipeline_summaries:
        pytest.skip("No pipeline results collected")

    logger.info("=" * 60)
    logger.info("PIPELINE PERFORMANCE SUMMARY (Immich)")
    logger.info("=" * 60)
    for summary in _pipeline_summaries:
        logger.info(summary.summary_line)
    logger.info("=" * 60)

    # Compute overheads
    asm_only = next(
        (summary for summary in _pipeline_summaries if summary.scenario == "immich_assembly_only"),
        None,
    )
    with_titles = next(
        (summary for summary in _pipeline_summaries if summary.scenario == "immich_with_titles"),
        None,
    )
    if asm_only and with_titles:
        overhead = with_titles.median_wall_seconds - asm_only.median_wall_seconds
        logger.info(f"DERIVED: immich_title_overhead = {overhead:.1f}s")

    output = tmp_path / "perf-results-pipeline.json"
    save_summary_results(_pipeline_summaries, output)

    # Export in github-action-benchmark format for CI comparison
    benchmark_output = tmp_path / "benchmark-pipeline.json"
    save_benchmark_json(_pipeline_summaries, benchmark_output)
    logger.info(f"Results saved to {output}")
    logger.info(f"Benchmark JSON saved to {benchmark_output}")
