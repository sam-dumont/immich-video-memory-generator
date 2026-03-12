"""Benchmarks for clip quality scoring."""

from __future__ import annotations

from tests.conftest import make_clip


def test_bench_quality_score_hd(benchmark):
    """Benchmark quality_score for a 1080p clip."""
    clip = make_clip("bench-hd", width=1920, height=1080, bitrate=10_000_000, duration=10.0)
    benchmark(lambda: clip.quality_score)


def test_bench_quality_score_4k(benchmark):
    """Benchmark quality_score for a 4K clip."""
    clip = make_clip("bench-4k", width=3840, height=2160, bitrate=40_000_000, duration=30.0)
    benchmark(lambda: clip.quality_score)


def test_bench_is_hdr_check(benchmark):
    """Benchmark is_hdr property check."""
    clip = make_clip("bench-hdr", color_transfer="arib-std-b67")
    benchmark(lambda: clip.is_hdr)
