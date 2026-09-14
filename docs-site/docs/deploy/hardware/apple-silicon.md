---
sidebar_position: 3
title: Apple Silicon
---

# Apple Silicon

A native macOS install can use VideoToolbox for video encoding and Metal for title kernels.
The reader and caption services can also run locally, provided there is enough memory for them.
The [self-hosting guide](../self-hosting.md) covers the tested model setup.

## Install

Install FFmpeg first, then:

```bash
uv tool install "immich-memories[all-mac]"
```

`all-mac` includes Apple framework bindings, editorial preparation, bundled music and Demucs.
OIDC needs the separate `auth` extra. The smaller `mac` extra supplies framework bindings only;
use `editorial` too for local preparation on `full` or `no_captions`.

```yaml
hardware:
  enabled: true
  encoder_preset: balanced
```

VideoToolbox is detected automatically. `hardware.enabled: false` selects software video encoding;
it does not disable Metal titles or a separately running model server.

## Memory

The tested model reader uses about 17 GB for weights, plus context and server overhead. The
caption service and app need their own room. The documented all-local model setup uses a 32 GB
Mac; smaller machines can run the app with the rules reader or use remote model services.
Unified memory does not make those allocations free.

## Titles and photos

Quadrants ships with the base package on supported macOS arm64 Python versions and can render
animated titles on Metal. No GPU extra is needed. An Intel Mac uses PIL because the pinned
kernel package has no wheel for that platform. See [Title kernels](./cpu-only.md#title-kernels).

Photo pans use the face boxes already returned by Immich. The app does not run a separate
Apple Vision face detector for this path.
