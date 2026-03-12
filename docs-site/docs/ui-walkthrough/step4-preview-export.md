---
sidebar_position: 5
title: "Step 4: Preview & Export"
---

# Step 4: Preview & Export

Hit generate and watch it work.

## Output Filename

The filename is auto-generated from your selections: person names, date range, and memory type. Examples:

- `sam_emile_march_2026_memories.mp4` (Multi-Person, March 2026)
- `emile_summer_2025_memories.mp4` (Season preset)
- `sam_2025_memories.mp4` (Year in Review)
- `everyone_jan-apr_2026_memories.mp4` (Custom, no person)

You can edit the filename before generating.

## Month Dividers

For memories spanning 4+ months, the video includes month divider cards between clips (e.g., "January", "February"). Shorter ranges (3 months or fewer) skip dividers entirely: there's not enough content to justify them.

## Generation Progress

Progress is shown in real-time as the tool:

1. Downloads selected clips from Immich
2. Processes and encodes each clip (with hardware acceleration if available)
3. Generates music (if enabled)
4. Assembles everything into the final video with transitions, title screens, and audio mixing

## Download

When generation completes, you get a download button. The video is also saved to `~/Videos/Memories/` in a timestamped directory.

## Re-generating

Not happy with the result? Go back to Step 2 or 3, adjust your clip selection or settings, and generate again. The analysis cache means you won't have to re-analyze anything.
