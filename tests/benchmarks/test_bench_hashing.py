"""Benchmarks for duplicate detection hashing functions."""

from __future__ import annotations

from immich_memories.analysis.duplicate_hashing import hamming_distance


def test_bench_hamming_distance(benchmark):
    """Benchmark hamming_distance with typical hex hashes."""
    hash_a = "a1b2c3d4e5f6a7b8"
    hash_b = "a1b2c3d4e5f6a7b9"

    benchmark(hamming_distance, hash_a, hash_b)


def test_bench_hamming_distance_identical(benchmark):
    """Benchmark hamming_distance with identical hashes."""
    hash_val = "ffffffffffffffff"

    benchmark(hamming_distance, hash_val, hash_val)


def test_bench_hamming_distance_very_different(benchmark):
    """Benchmark hamming_distance with maximally different hashes."""
    hash_a = "0000000000000000"
    hash_b = "ffffffffffffffff"

    benchmark(hamming_distance, hash_a, hash_b)
