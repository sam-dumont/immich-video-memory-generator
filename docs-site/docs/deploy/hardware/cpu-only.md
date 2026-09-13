---
sidebar_position: 6
title: CPU-Only Mode
---

# CPU-Only Mode

**Everything this box does has a CPU fallback.** Preparation, selection, assembly and encoding all
run without a GPU; what you give up is animated titles and the hardware encoder, not a step of the
pipeline.

What a CPU-only box cannot do is hold the vision reader: roughly 17 GB resident, and not in this
container. On a cheap VPS that means a second machine, not a slower first one. Read
[one machine or two](../self-hosting.md#one-machine-or-two) before you size anything.

The *caption* server is a different matter: set
[`editorial.preparation.tier: no_captions`](../../reference/config-reference.md#preparation-tiers) and
this box prepares every producer the audience gate reads without one. On a four-core Celeron that
is 3 h 41 min for a 10,793-picture library instead of four days.

## What changes without a GPU

| Feature | With GPU | Without GPU | Impact |
|---------|----------|-------------|--------|
| Title screens | Animated GPU-rendered (Quadrants/Taichi: bokeh particles, gradient animation, SDF text) | Static PIL-rendered (gradient background, text overlay) | Simpler visuals, same text. And the dominant cost of a run (see below) |
| Video encoding | NVENC / VideoToolbox / VAAPI / QSV | libx264 / libx265 (software) | Slower encoding: the smaller half of a run |
| SDF text rendering | GPU kernels + FreeType atlas | PIL text drawing | No SDF glow/shadow effects |
| Video scaling | GPU-accelerated (scale_cuda, scale_vaapi) | FFmpeg swscale (CPU) | Slower for resolution changes |

**What runs on this CPU, identically to a GPU box:**
- Clip discovery from Immich
- The six context heads over the pinned ONNX encoder, and the two detectors (both are CPU-only by
  construction: the Docling one pins `CPUExecutionProvider`, the Marqo one just sets thread count)
- Burst and near-duplicate collapsing
- Audio ducking and music mixing
- Assembly, and all CLI and UI functionality

**What runs on a model server you host, GPU or not:**
- The captions, one per candidate picture
- Every reading the editor makes of the period, and the picture requests inside it

## Configuration

No configuration is needed. The pipeline auto-detects available hardware and falls back to CPU automatically. A hardware encoder (NVENC, Quick Sync, VAAPI) is only used if it passes a one-frame test encode at startup: FFmpeg builds such as Debian's list those encoders on every machine, so the listing alone is not trusted. On a box without the matching GPU or driver you get a single `Hardware encoder probe failed for …` log line and software encoding. To explicitly force CPU encoding (skip GPU probing entirely):

```yaml
hardware:
  enabled: false
```

## Title kernels: Quadrants by default, Taichi fallback

The animated title screen renderer (particle effects, gradient animations, SDF text) runs on a GPU
kernel library. Since 0.80 that library is
[Quadrants](https://github.com/Genesis-Embodied-AI/quadrants), and it installs with the app. There
is no extra to remember:

```bash
pip install immich-memories
```

Quadrants is the maintained fork of Taichi. Taichi 1.7.4 (July 2025) is the last release its
upstream will make; Quadrants has the same kernel API, renders the same frames (measured across
five styles and nine moods: worst pixel 1/255, ten pixels of 7.8 million), ships a smaller wheel,
and publishes `linux/arm64` wheels that Taichi never did. That last one is why the swap is worth
it here: **`linux/arm64` hosts (a Raspberry Pi, the arm64 Docker image) now get GPU-rendered titles
instead of the PIL fallback.**

Taichi is still supported. `immich-memories[gpu]` installs it, and the config picks it:

```yaml
advanced:
  hardware:
    title_kernel_backend: auto   # auto (default) | quadrants | taichi
```

`auto` takes Quadrants when it is installed and Taichi otherwise. Naming one pins it, and an
install that does not have the one it named falls back to the other rather than losing titles.
`IMMICH_MEMORIES_TITLE_KERNELS=quadrants` overrides the config for a single run. Whichever wins,
one line at the start of a render says so:

```
Title kernels: quadrants 1.3.0 (auto) on the Metal backend
```

Only one of the two is ever loaded. They each carry their own copy of LLVM and register the same
options with it, so a process that imports both aborts. This is a choice per run, never a mix.

With neither installed, title screens are rendered with PIL (static gradient + text). The video
output is functionally identical: same title text, same timing, same encoding, simpler visuals.

Both libraries also have a CPU backend. To keep the GPU renderer but force it onto the processor (a
broken driver, or comparing timings), set `IMMICH_FORCE_CPU=1`.

## Performance expectations

The one end-to-end measurement is in the [NAS-only guide](../common-setups/nas-only.md#performance-expectations):
a 14-clip monthly, 62 s of 1080p out, cold cache, 4 cores and no GPU took 10 min 08 s with
`preset: fast` and 15 min 42 s on the default profile. The analysis phase was 7.4 of those
10 minutes.

Read that for the render, not for preparation: the Analysis column measured the retired per-clip
scorer, which is not the work this product does any more, and preparation on the current route is
[measured only on a NAS](../common-setups/nas-only.md#preparation-measured). What is true by
construction rather than by measurement is that the heads and the detectors have no GPU path here,
so a card does not shorten them, and that every producer banks its answer, so a second cut over
the same period skips them. Title rendering is the part a GPU would actually take off your hands.

### Title rendering is the bottleneck, not encoding

This page used to say title rendering was near-instant on CPU. It is the opposite, and the
number is worth knowing before you size a box.

Measured 2026-08-23 in the container with `--cpus=2`, generating 18 seconds of output:
**title rendering took ~263 s of a ~339 s assembly**.
Titles are seconds of video, but every frame of them is composed pixel by pixel on the CPU,
while the clips around them are a decode-and-encode the CPU is comparatively good at.

The practical consequences:

- A **shorter or simpler title** is the cheapest large win available on a CPU-only box.
- Rendering cost scales with title **duration and resolution**, not with how many clips the
  memory has: a 12-clip memory and a 40-clip memory pay nearly the same title bill.
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
