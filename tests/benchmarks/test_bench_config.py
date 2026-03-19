"""Benchmarks for configuration loading."""

from __future__ import annotations

import yaml

from immich_memories.config_loader import Config


def test_bench_config_default_construction(benchmark):
    """Benchmark Config() with all defaults."""
    benchmark(Config)


def test_bench_config_from_yaml(benchmark, tmp_path):
    """Benchmark Config.from_yaml() with a minimal config file."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "immich": {"url": "http://localhost:2283", "api_key": "test-key"},
                "defaults": {"target_duration_seconds": 600},
            }
        )
    )

    benchmark(Config.from_yaml, config_file)


def test_bench_config_from_nonexistent(benchmark, tmp_path):
    """Benchmark Config.from_yaml() with non-existent file (returns defaults)."""
    path = tmp_path / "nonexistent.yaml"
    benchmark(Config.from_yaml, path)
