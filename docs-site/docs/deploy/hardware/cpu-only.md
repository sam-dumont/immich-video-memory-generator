---
sidebar_position: 6
title: CPU-Only Mode
---

# CPU-Only Mode

The app can prepare, select and render a memory without a GPU. Start with the rules reader and
`metadata_only` to avoid model services; add classifiers with `no_captions` after `models fetch`.
A model reader is a separate choice, with its own memory and serving requirements. See
[Running modes](../running-modes.md) for measured timings and quality limits.

## Configuration

FFmpeg hardware encoders must pass a one-frame test encode before use. To force software video
encoding:

```yaml
hardware:
  enabled: false
```

This controls video encoding, not title kernels or model-server devices. Preflight can still
probe hardware. `immich-memories hardware` reports encoder availability;
`immich-memories preflight` also checks title rendering and the selected preparation providers.

## Title kernels

Quadrants is part of the base install on supported platforms. It tries Metal, CUDA or Vulkan
where available, then its CPU backend. A CPU-only machine can therefore keep animated titles,
including particles and SDF text. Force that backend with `IMMICH_FORCE_CPU=1` when comparing
render timings or diagnosing a GPU driver.

The pinned package supports Python 3.11 to 3.13 for this app on Linux x86_64 and aarch64,
macOS arm64 and Windows AMD64. On macOS x86_64 or Python 3.14, the package has no matching wheel;
the app uses PIL. Preflight reports the selected fallback before a long run.

PIL draws the same title text and timing, including text animation and simpler gradient
backgrounds. It does not provide the kernel renderer's particles, SDF effects or animated
slow-motion deblur. A content-backed PIL title uses a still frame.

### CPUs without AVX

On the tested Celeron J4125, loading Quadrants dies with an illegal instruction. The app probes
it in a child process so the main run survives, then uses PIL. Do not import Quadrants directly
to test an affected machine; use preflight.

## Performance expectations

Preparation scales with the pictures in the requested period. Cached facts avoid repeating
matching producer work; reading the period, downloading selected media and rendering still cost
time. The [NAS guide](../common-setups/nas-only.md#preparation-tiers-what-the-nas-pays) and
[Running modes](../running-modes.md) separate measured cold and warm selection costs.

### Title rendering used to be the bottleneck

Title cost depends on resolution, duration and backend. The current blur implementation works
at quarter resolution and reuses held frames. On the recorded two-thread CPU profile, a 1080p
opening frame took 64 ms after that change. This is a title microbenchmark, not a total-run
estimate. Measure the content and settings you intend to run.

Software video encoding uses libx264 or libx265. Reducing output resolution or using the `fast`
preset can reduce render cost; the preset also changes codec and title backgrounds. It does not
change which facts the editorial preparation tier requires.
