"""Benchmark for title screen rendering performance (issue #164).

Verifies GPU-resident buffer optimization: target sub-16ms/frame.
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.fixture()
def _init_taichi():
    from immich_memories.titles.taichi_kernels import init_taichi

    init_taichi()


@pytest.mark.benchmark
class TestTitleRenderPerf:
    def test_gradient_per_frame_time(self, _init_taichi, benchmark):
        """720p gradient-only title: target <16ms/frame avg."""
        from immich_memories.titles.renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

        cfg = TaichiTitleConfig(width=1280, height=720, fps=30.0, duration=3.5)
        renderer = TaichiTitleRenderer(cfg)

        # Warm up JIT
        renderer.render_frame(0, "Warm Up")

        frame_idx = [1]

        def render_one():
            i = frame_idx[0]
            renderer.render_frame(i, "January 2025", "A look back")
            frame_idx[0] = (i + 1) % renderer.total_frames or 1

        benchmark(render_one)

    def test_content_backed_per_frame_time(self, _init_taichi, benchmark):
        """720p content-backed title: target <20ms/frame avg."""
        from immich_memories.titles.renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

        bg = np.random.default_rng(42).random((720, 1280, 3)).astype(np.float32)
        cfg = TaichiTitleConfig(width=1280, height=720, fps=30.0, duration=3.5, background_image=bg)
        renderer = TaichiTitleRenderer(cfg)

        renderer.render_frame(0, "Warm Up")

        frame_idx = [1]

        def render_one():
            i = frame_idx[0]
            renderer.render_frame(i, "Summer 2025", "Our best memories")
            frame_idx[0] = (i + 1) % renderer.total_frames or 1

        benchmark(render_one)

    def test_manual_timing_report(self, _init_taichi):
        """Manual per-frame timing with percentiles (not pytest-benchmark)."""
        from immich_memories.titles.renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

        cfg = TaichiTitleConfig(width=1280, height=720, fps=30.0, duration=3.5)
        renderer = TaichiTitleRenderer(cfg)
        renderer.render_frame(0, "Warm Up")

        times = []
        for i in range(1, renderer.total_frames):
            start = time.perf_counter()
            renderer.render_frame(i, "January 2025", "A look back")
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)

        avg_ms = np.mean(times)
        p95_ms = np.percentile(times, 95)
        print(
            f"\nPERF: 720p gradient — avg={avg_ms:.1f}ms, p95={p95_ms:.1f}ms ({len(times)} frames)"
        )
