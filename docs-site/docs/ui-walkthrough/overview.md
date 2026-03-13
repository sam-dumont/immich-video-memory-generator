---
sidebar_position: 1
title: UI Overview
---

# UI Overview

Immich Memories ships with a NiceGUI web interface. Launch it and it opens at `http://localhost:8080`.

```bash
immich-memories ui
```

The UI is a 4-step wizard:

1. **Configuration**: Connect to Immich, pick a memory type (or custom date range), and select people.
2. **Clip Review**: See what the analysis found, deselect clips you don't want. Live photos show up with a purple "Live" badge.
3. **Generation Options**: Choose orientation, resolution, transitions, and music.
4. **Preview & Export**: Generate the video and download it.

Each step builds on the previous one. You can go back and change things without losing your selections.

## Theme

The UI supports dark mode, light mode, and system-follow. Toggle it from the sidebar (bottom left). The sidebar itself stays dark in both modes, matching the Immich aesthetic.

## Memory type presets

Step 1 shows 6 preset cards that auto-configure date ranges, target duration, and person filters:

- **Year in Review**: full calendar year for one person or everyone
- **Season**: spring/summer/autumn/winter of a given year
- **Person Spotlight**: one person, one year
- **Multi-Person**: 2+ people, filters for clips containing all of them
- **Monthly Highlights**: a single month
- **On This Day**: today's date across all years in your library
- **Trip**: a specific trip or vacation (date range with auto-calculated duration)

Or pick **Custom** for full control over the date range.

The UI is the recommended way to get started. Once you know what settings you like, you can switch to the [CLI](../cli/overview.md) for automation.
