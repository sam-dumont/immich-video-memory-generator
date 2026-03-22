---
sidebar_position: 6
title: CPU-Only Mode
---

# CPU-Only Mode

The pipeline is designed around GPU acceleration and ML-powered features for the best results, but **every feature has a CPU fallback**. You can generate memory videos on a headless server, a cheap VPS, or any machine without a GPU.

## What changes without a GPU

| Feature | With GPU | Without GPU | Impact |
|---------|----------|-------------|--------|
| Video encoding | NVENC / VideoToolbox / VAAPI / QSV | libx264 / libx265 (software) | 3-10x slower encoding |
| Title screens | Animated GPU-rendered (Taichi: bokeh particles, gradient animation, SDF text) | Static PIL-rendered (gradient background, text overlay) | Simpler visuals, same text |
| Frame blending (transitions) | Taichi GPU kernel | NumPy CPU blending | Slightly slower crossfades |
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
- Smart clip ordering
- All CLI and UI functionality

## Configuration

No configuration is needed. The pipeline auto-detects available hardware and falls back to CPU automatically. To explicitly force CPU mode:

```yaml
hardware:
  backend: "none"
```

## Taichi (optional GPU dependency)

Taichi powers the animated title screen renderer (particle effects, gradient animations, SDF text). It is an **optional** dependency:

```bash
# Install with GPU title support
pip install immich-memories[gpu]

# Or install without it (CPU-only titles)
pip install immich-memories
```

When Taichi is not installed, title screens are rendered with PIL (static gradient + text). The video output is functionally identical: same title text, same timing, same encoding.

## Performance expectations

On a modern CPU (4+ cores), expect roughly:

- **Encoding**: 2-5x realtime for 1080p H.264 (a 3-minute video takes 6-15 minutes)
- **Analysis**: Similar speed (most analysis is CPU-bound regardless of GPU)
- **Title rendering**: Near-instant with PIL (no GPU kernel compilation)
- **Transitions**: Negligible difference for typical clip counts

The biggest slowdown without a GPU is video encoding. If encoding speed matters, consider a machine with hardware encoding support.

## Preflight check

Run the hardware check to see what the pipeline detects:

```bash
immich-memories hardware
```

If no GPU is found, you will see:

```
Hardware: No GPU acceleration. Video encoding will use CPU (slower).
```

This is a warning, not an error. The pipeline will work fine.
