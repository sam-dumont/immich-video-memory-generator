"""Benchmarks for duplicate detection with synthetic clips."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.duplicate_hashing import hamming_distance
from tests.conftest import make_clip


def _make_100_clips():
    """Create 100 synthetic clips spread over a year."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        make_clip(
            f"dup-{i:03d}",
            file_created_at=base + timedelta(days=i * 3),
            width=1920,
            height=1080,
        )
        for i in range(100)
    ]


def test_bench_create_100_clips(benchmark):
    """Benchmark creating 100 synthetic VideoClipInfo objects."""
    benchmark(_make_100_clips)


def test_bench_pairwise_hamming_10(benchmark):
    """Benchmark 10 pairwise hamming distance comparisons."""
    hashes = [f"{i:016x}" for i in range(10)]

    def compare_all():
        for i in range(len(hashes)):
            for j in range(i + 1, len(hashes)):
                hamming_distance(hashes[i], hashes[j])

    benchmark(compare_all)


def test_bench_quality_score_100_clips(benchmark):
    """Benchmark quality_score for 100 clips."""
    clips = _make_100_clips()

    def score_all():
        return [c.quality_score for c in clips]

    benchmark(score_all)
