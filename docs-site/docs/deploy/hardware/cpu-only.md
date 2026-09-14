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
| Title screens | GPU kernels: bokeh particles, SDF text, the animated deblur of a content-backed card | PIL: the same animated gradient and text, without the kernel effects | Simpler visuals, same text and timing |
| Video encoding | NVENC / VideoToolbox / VAAPI / QSV | libx264 / libx265 (software) | Slower encoding: the smaller half of a run |
| SDF text rendering | GPU kernels + FreeType atlas | PIL text drawing | No SDF glow/shadow effects |
| Video scaling | GPU-accelerated (scale_cuda, scale_vaapi) | FFmpeg swscale (CPU) | Slower for resolution changes |

**What runs on this CPU, identically to a GPU box:**
- Clip discovery from Immich
- The six context heads over the pinned ONNX encoder, and the two detectors (all three are ONNX
  sessions, and with no card to take they open on `CPUExecutionProvider`)
- Burst and near-duplicate collapsing
- Audio ducking and music mixing
- Assembly, and all CLI and UI functionality

**What runs on a model server you host, GPU or not:**
- The captions, one per candidate picture
- Every reading the editor makes of the period, and the picture requests inside it

## Configuration

No configuration is needed. The pipeline auto-detects available hardware and falls back to CPU automatically. A hardware encoder (NVENC, Quick Sync, VAAPI) is only used if it passes a one-frame test encode at startup: FFmpeg builds such as Debian's list those encoders on every machine, so the listing alone is not trusted. On a box without the matching GPU or driver you get a single `Hardware encoder probe failed for …` log line and software encoding. To force software encoding and skip the encoder probe:

```yaml
hardware:
  enabled: false
```

That is the video encoder only. It does not touch the title kernels, which have their own probe
and their own switch (`IMMICH_FORCE_CPU=1`), and it does not move any model off a device.

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

Losing the kernels costs effects, not time. The profile in #900 measured the PIL renderer at
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
kernel effects (bokeh particles, the slow-motion deblur of a content-backed card) and the SDF text
path. It does not lose animation as such: `renderer_pil.py` composes every frame and draws an
animated gradient behind the text unless `animated_background` is off, which `preset: fast` turns
off on every renderer. Same title text, same timing, same encoding.

The fallback is logged once at startup, and `immich-memories preflight` says which renderer a
machine will use before you start a long run. Its row overstates the loss:

```
Title rendering       PIL renderer: static title screens, no animation and no SDF text
                      quadrants publishes no wheel for darwin/x86_64 on Python 3.12.
```

## Performance expectations

The end-to-end numbers are on [Running modes](../running-modes.md#what-to-expect-on-a-first-run),
measured on the same month across a Mac, a Synology DS423+ and a Kubernetes cluster. The CPU-only
row to read is the NAS: **1,483 s to render a 54-second film on four Celeron cores**, against 81 s
for a film the same length on the Mac, and 1.4404 s per picture of preparation at `no_captions`,
about 90 % of it the two detectors and the six heads. The cluster cells, also encoding on the CPU,
took 362 to 380 s per film on a real month.

Losing the hardware encoder is the smaller part of that. Measured on one cluster node with only the
encoder changed, software encoding cost 15 % of the render: 257.8 s against 218.6 s, because
downloading the originals from Immich was half of both. The
[hardware overview](./overview.md#what-the-card-is-actually-worth) has the phase table.

What is true by construction rather than by measurement: there is no card on this box to put the
heads and the detectors on, and every producer banks its answer, so a second cut over the same
period skips them entirely. If the classifiers are the bill, the way out is not a faster CPU but
the [inference service](../installation/inference-service.md) on a box that has a card.

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
