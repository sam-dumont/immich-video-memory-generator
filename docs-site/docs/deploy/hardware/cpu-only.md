---
sidebar_position: 6
title: CPU-Only Mode
---

# CPU-Only Mode

The pipeline is designed around GPU acceleration and ML-powered features for the best results, but **every feature has a CPU fallback**. You can generate memory videos on a headless server, a cheap VPS, or any machine without a GPU.

## What changes without a GPU

| Feature | With GPU | Without GPU | Impact |
|---------|----------|-------------|--------|
| Title screens | Animated GPU-rendered (Taichi: bokeh particles, gradient animation, SDF text) | Static PIL-rendered (gradient background, text overlay) | Simpler visuals, same text — and the dominant cost of a run (see below) |
| Video encoding | NVENC / VideoToolbox / VAAPI / QSV | libx264 / libx265 (software) | 3-10x slower encoding, the smaller half |
| Face detection (macOS) | Apple Vision (Neural Engine) | OpenCV Haar cascades (CPU) | Slightly less accurate |
| SDF text rendering | Taichi GPU kernels + FreeType atlas | PIL text drawing | No SDF glow/shadow effects |
| Video scaling | GPU-accelerated (scale_cuda, scale_vaapi) | FFmpeg swscale (CPU) | Slower for resolution changes |

**Core pipeline features that work identically on CPU:**
- Clip discovery and selection from Immich
- Quality scoring and ranking
- Duplicate detection (perceptual hashing)
- Scene detection (PySceneDetect)
- LLM-powered content analysis
- Audio ducking and music mixing
- Clip ordering
- All CLI and UI functionality

## Configuration

No configuration is needed. The pipeline auto-detects available hardware and falls back to CPU automatically. A hardware encoder (NVENC, Quick Sync, VAAPI) is only used if it passes a one-frame test encode at startup: FFmpeg builds such as Debian's list those encoders on every machine, so the listing alone is not trusted. On a box without the matching GPU or driver you get a single `Hardware encoder probe failed for …` log line and software encoding. To explicitly force CPU encoding (skip GPU probing entirely):

```yaml
hardware:
  enabled: false
```

## Taichi (optional GPU dependency)

Taichi powers the animated title screen renderer (particle effects, gradient animations, SDF text). It is an **optional** dependency:

```bash
# Install with GPU title support
pip install "immich-memories[gpu]"

# Or install without it (CPU-only titles)
pip install immich-memories
```

When Taichi is not installed, title screens are rendered with PIL (static gradient + text). The video output is functionally identical: same title text, same timing, same encoding.

Taichi also has a CPU backend. To keep Taichi but force it off the GPU (a broken driver, or
comparing timings), set `IMMICH_FORCE_CPU=1`. On `linux/arm64` (Raspberry Pi, the arm64 Docker
image) the `gpu` extra skips Taichi altogether — titles are always PIL-rendered there.

## Performance expectations

On a modern CPU (4+ cores), expect roughly:

- **Encoding**: 0.2-0.5x realtime for 1080p H.264 (a 3-minute video takes 6-15 minutes)
- **Analysis**: Similar speed (most analysis is CPU-bound regardless of GPU)
- **Title rendering**: the dominant cost — see below
- **Transitions**: Negligible difference for typical clip counts

### Title rendering is the bottleneck, not encoding

This page used to say title rendering was near-instant on CPU. It is the opposite, and the
number is worth knowing before you size a box.

Measured 2026-08-23 in the container with `--cpus=2`, generating 18 seconds of output:
**title rendering took ~263 s of a ~339 s assembly**, and assembly was ~94% of the whole run.
Titles are seconds of video, but every frame of them is composed pixel by pixel on the CPU,
while the clips around them are a decode-and-encode the CPU is comparatively good at.

The practical consequences:

- A **shorter or simpler title** is the cheapest large win available on a CPU-only box.
- Rendering cost scales with title **duration and resolution**, not with how many clips the
  memory has — a 12-clip memory and a 40-clip memory pay nearly the same title bill.
- Hardware encoding helps the encode, which is the smaller half. Buy a GPU for the titles
  before you buy one for the encoder.

## Preflight check

Run the hardware check to see what the pipeline detects:

```bash
immich-memories hardware
```

If no GPU is found, you will see:

```
No hardware acceleration detected

Video encoding will use CPU (libx264).
```

This is a warning, not an error. The pipeline will work fine.
