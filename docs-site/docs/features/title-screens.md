---
sidebar_position: 6
title: Title Screens
---

# Title Screens

Memory videos get auto-generated title cards — a text overlay at the start that says something like "Summer 2025" or "March Memories." It's a small thing but it makes the difference between a random clip dump and something that feels intentional.

## What gets generated

- **Title text** based on the date range of the included clips
- **Rendered as a video segment** that gets prepended to the final compilation
- **Hardware-accelerated encoding** when available (same backend as the rest of the pipeline)

## How it works

Title screens are rendered using FFmpeg's text drawing filters. The text, font, position, and timing are configured, then encoded as a short video segment. This segment is concatenated with the rest of the clips during final assembly.

No external image editors or template engines required — it's all done in the FFmpeg pipeline.
