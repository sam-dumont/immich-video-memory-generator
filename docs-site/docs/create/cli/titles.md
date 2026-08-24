---
sidebar_position: 3
title: titles
---

# titles

Title screens are the intro cards, month dividers, and ending screens that get inserted into your generated videos. The `titles` command group lets you preview styles and manage fonts.

## titles test

Generate a standalone title screen to preview how it looks before committing to a full video generation.

```bash
immich-memories titles test [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--year` | `-y` | int | current year | Year to display |
| `--birthday-age` | — | int | — | Age for birthday title (e.g., `1` for "1st Year") |
| `--person` | `-p` | string | — | Person name for subtitle |
| `--month` | `-m` | int | — | Month number 1-12 (for month divider) |
| `--type` | — | choice | `title` | `title`, `month`, or `ending` |
| `--orientation` | — | choice | `landscape` | `landscape`, `portrait`, `square` |
| `--resolution` | `-r` | choice | `1080p` | `720p`, `1080p`, `4k` |
| `--locale` | `-l` | choice | `en` | `en` or `fr` |
| `--style` | `-s` | choice | `random` | `modern_warm`, `elegant_minimal`, `vintage_charm`, `playful_bright`, `soft_romantic`, `random` |
| `--output` | `-o`, `-O` | path | `./title_screen_preview.mp4` | Output file |
| `--download-fonts` | — | flag | — | Download fonts before generating |
| `--no-animated-background` | — | flag | — | Use static gradient instead of animation |

Examples:

```bash
# Simple year title
immich-memories titles test --year 2024

# Birthday title for a person
immich-memories titles test --birthday-age 2 --person "Emma" --year 2024

# Month divider
immich-memories titles test --month 6 --year 2024 --type month

# Portrait for social media
immich-memories titles test --year 2024 --orientation portrait

# French locale with vintage style
immich-memories titles test --year 2024 --locale fr --style vintage_charm
```

## titles fonts

Manage the fonts used for title screens. All five are OFL-1.1 licensed and ship inside the package (`titles/bundled_fonts/`); anything downloaded on top comes from the Fontsource CDN and is cached in `~/.immich-memories/fonts/`.

```bash
# List fonts and their status (bare command or --list)
immich-memories titles fonts
immich-memories titles fonts --list

# Download all fonts
immich-memories titles fonts --download

# Clear the font cache
immich-memories titles fonts --clear
```

You do not have to run any of this. Titles render correctly on a fresh install with no network, because `get_font_path()` checks the bundled copies before the cache and the bundle already carries every weight the renderer asks for. `--download` mirrors the same files from the CDN into `~/.immich-memories/fonts/`; it is there for inspecting or replacing what ships, not for making titles work.
