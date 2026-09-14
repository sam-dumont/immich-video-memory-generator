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
is 3 h 41 min for a library of about ten thousand pictures instead of four days.

## What changes without a GPU

| Feature | With GPU | Without GPU | Impact |
|---------|----------|-------------|--------|
| Title screens | Animated GPU-rendered (bokeh particles, gradient animation, SDF text) | Static PIL-rendered (gradient background, text overlay) | Simpler visuals, same text. And the dominant cost of a run (see below) |
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

## Title kernels

The animated title screen renderer (particle effects, gradient animations, SDF text) runs its work
on the GPU through [Quadrants](https://github.com/Genesis-Embodied-AI/quadrants), which installs
with the app. There is no extra to remember and nothing to configure:

```bash
pip install immich-memories
```

One line at the start of a render says what the titles are being drawn by:

```
Title kernels: quadrants 1.3.0 on the Metal backend
```

Metal on Apple Silicon, CUDA or Vulkan on a card, and a CPU backend everywhere else. To keep the
GPU renderer but force it onto the processor (a broken driver, or comparing timings), set
`IMMICH_FORCE_CPU=1`.

### CPUs without AVX

The CPU backend needs AVX. On a processor without it (Celeron J-series such as the J4125 in a
Synology DS423+, and older Atom) the kernel library dies with SIGILL, exit 132, and takes the
interpreter with it. On the J4125 that happens as the library loads, before a single kernel is
compiled, so `python -c "import quadrants"` is already the crash. It used to kill the run at title
generation, after the whole selection had been paid for.

A child process now does the loading and the first kernel dispatch, and the app never touches the
library until that child has come back alive. A machine that fails renders static titles through
PIL, and says so in `immich-memories preflight` before you start:

```
Title rendering       WARNING   kernel backend crashed on this CPU: illegal
                                instruction; titles fall back to the PIL renderer
```

Losing the kernels costs animation, not time. The profile in #900 measured the PIL renderer at
4.4 s for a content-backed title against 19.8 s for the kernel renderer on a CPU backend: on a box
with no AVX, PIL is both the only renderer and the faster one.

### Where the GPU kernels exist

Verified against PyPI for Quadrants 1.3.0 (`pip index versions quadrants`, and the `urls` list in
its PyPI JSON). There is **no source distribution**, so a platform without a wheel gets no kernels
at all rather than a long build:

| Platform | Wheels |
| --- | --- |
| Linux x86_64 (manylinux 2.27+) | Python 3.10, 3.11, 3.12, 3.13 |
| Linux aarch64 (manylinux 2.27+) | Python 3.10, 3.11, 3.12, 3.13 |
| macOS arm64 (macOS 13+) | Python 3.10, 3.11, 3.12, 3.13 |
| Windows AMD64 | Python 3.10, 3.11, 3.12, 3.13 |
| **macOS x86_64 (Intel)** | **none** |
| **Python 3.14 and later** | **none** |

This app needs Python 3.11 or later, so the usable range here is **3.11 to 3.13**.

On the two platforms with no wheel, title screens fall back to the PIL renderer. That loses the
animated kernels (bokeh particles, the gradient animation, the slow-motion deblur of a
content-backed card) and the SDF text path; you still get the same title text, the same timing and
the same encoding on a static gradient. The fallback is logged once at startup, and
`immich-memories preflight` says which renderer a machine will use before you start a long run:

```
Title rendering       PIL renderer: static title screens, no animation and no SDF text
                      quadrants publishes no wheel for darwin/x86_64 on Python 3.12.
```

## Performance expectations

The one end-to-end measurement is in the [NAS-only guide](../common-setups/nas-only.md#preparation-tiers-what-the-nas-pays):
a 14-clip monthly, 62 s of 1080p out, cold cache, 4 cores and no GPU took 10 min 08 s with
`preset: fast` and 15 min 42 s on the default profile. The analysis phase was 7.4 of those
10 minutes.

Read that for the render, not for preparation: the Analysis column measured the retired per-clip
scorer, which is not the work this product does any more, and preparation on the current route is
[measured only on a NAS](../common-setups/nas-only.md#preparation-tiers-what-the-nas-pays). What is true by
construction rather than by measurement is that the heads and the detectors have no GPU path here,
so a card does not shorten them, and that every producer banks its answer, so a second cut over
the same period skips them. Title rendering is the part a GPU would actually take off your hands.

### Title rendering used to be the bottleneck

This page once said title rendering was near-instant on CPU, then said the opposite. Both
were true of their day, and the number is worth knowing before you size a box.

Measured 2026-08-23 in the container with `--cpus=2`, generating 18 seconds of output:
**title rendering took ~263 s of a ~339 s assembly**. Titles are seconds of video, but every
frame of them was composed pixel by pixel on the CPU, while the clips around them are a
decode-and-encode the CPU is comparatively good at.

Profiling that (#900) found 85% of a title frame in one Gaussian blur, at a radius of a tenth
of the frame height, recomputed at full resolution on every frame. It now runs on a
quarter-size copy of the picture and is held between frames while the background holds still.
Measured on the CPU backend pinned to two threads, over a 3.5 s opening and a 7 s ending:

| | before | after |
| --- | --- | --- |
| Opening title, 720p | 186 ms a frame | 32 ms a frame |
| Ending screen, 720p | 184 ms a frame | 27 ms a frame |
| Opening title, 1080p | 578 ms a frame | 64 ms a frame |

The picture is the same one: the largest per-channel difference between the two renders stays
under the film grain the renderer lays over the result anyway, which is what
`make test-integration-titles` asserts on every run.

The practical consequences:

- Rendering cost still scales with title **duration and resolution**, not with how many clips
  the memory has: a 12-clip memory and a 40-clip memory pay nearly the same title bill.
- Hardware encoding helps the encode, which is now the larger half again on a CPU-only box.

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
