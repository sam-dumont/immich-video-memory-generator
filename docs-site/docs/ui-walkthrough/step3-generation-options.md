---
sidebar_position: 4
title: "Step 3: Generation Options"
---

# Step 3: Generation Options

This step controls what the final video looks and sounds like.

![Step 3: Generation Options](/img/screenshots/step3-options.png)

## Orientation

- **Landscape** (16:9): Standard widescreen. Best for TV playback.
- **Portrait** (9:16): Vertical. Good for phone viewing or Instagram stories.
- **Square** (1:1): Works everywhere, wastes some frame space.

The tool uses face-aware smart cropping when converting between orientations, so faces stay centered even when the source video doesn't match the output aspect ratio.

## Resolution

- **720p**: Fast to render, small file size.
- **1080p**: Good default. Looks sharp on most screens.
- **4K**: If you have the source material and the patience.

## Transitions

Choose the transition style between clips. Crossfade is the default and works well for most content.

## Music

Three options:

- **AI-generated**: Uses your configured backend (ACE-Step, MusicGen, or both) to generate a soundtrack that matches the video's mood. See [AI Music Overview](../music/overview.md).
- **Upload custom**: Drag and drop your own music file.
- **None**: Just the original clip audio, no background music.

## Hardware Acceleration

Auto-detected based on your system. If you have an NVIDIA GPU, Apple Silicon, or Intel QSV, the tool will use it for encoding. You'll see what was detected in this step.

