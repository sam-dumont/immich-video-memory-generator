---
sidebar_position: 3
title: titles
---

# titles

Title screens are the intro cards, month dividers, and ending screens that get inserted into your generated videos. The `titles` command group lets you preview styles and manage fonts.

## Date and place captions

`generate --add-date --add-place` adds small, translucent context to clips:
48 px on a 1080p frame, at 85% opacity. Date and place appear independently when
they change. Missing metadata does not restart a repeated caption.

Home and familiar locations stay unlabelled. Familiarity uses GPS observations
within **250 m of the asset**, across the accessible library's history. It never
suppresses an entire city. A place qualifies with either:

- At least 12 distinct visit weeks across six months in each of two years.
- At least three distinct visit weeks across three months in each of five years.

Repeated photos in one week do not add visits. An annual summer holiday alone
does not qualify. These are conservative recurrence rules, not proof of residence.
The configured `trips.homebase_latitude` and `trips.homebase_longitude` also identify
the home circle. Its GPS history supplies the home country, which is omitted from
domestic captions; foreign countries remain visible. If the country is unknown
or conflicting, the original caption remains. Missing GPS cannot identify a
familiar place by city name alone. Maps retain the original location data.

The first geographic-caption render reads library metadata, without downloading
photos for this step. A private cache under `cache.directory/familiar-places/`
separates server and credential scopes and lasts seven days. Delete that directory
to refresh sooner after metadata edits or removals. Repeat renders read the cache.
Privacy mode and renders without place captions skip this scan.

## titles test

Generate a standalone title screen to preview how it looks before committing to a full video generation.

```bash
immich-memories titles test [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--year` | `-y` | int | current year | Year to display |
| `--birthday-age` | n/a | int | n/a | Age for birthday title (e.g., `1` for "1st Year") |
| `--person` | `-p` | string | n/a | Person name for subtitle |
| `--month` | `-m` | int | n/a | Month number 1-12 (for month divider) |
| `--type` | n/a | choice | `title` | `title`, `month`, or `ending` |
| `--orientation` | n/a | choice | `landscape` | `landscape`, `portrait`, `square` |
| `--resolution` | `-r` | choice | `1080p` | `720p`, `1080p`, `4k` |
| `--locale` | `-l` | choice | `en` | `en` or `fr` |
| `--style` | `-s` | choice | `random` | `modern_warm`, `elegant_minimal`, `vintage_charm`, `playful_bright`, `soft_romantic`, `random` |
| `--output` | `-o`, `-O` | path | `./title_screen_preview.mp4` | Output file |
| `--download-fonts` | n/a | flag | n/a | Download fonts before generating |
| `--no-animated-background` | n/a | flag | n/a | Use static gradient instead of animation |

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

Manage the fonts that ship for title screens. All five are OFL-1.1 licensed and live inside the package (`titles/bundled_fonts/`); anything downloaded on top comes from the Fontsource CDN and is cached in `~/.immich-memories/fonts/`.

Title presets and mood-derived styles use Montserrat. Date and place captions use Outfit.
Raleway, Josefin Sans and Quicksand are bundled but not selected by these styles.

```bash
# List fonts and their status (bare command or --list)
immich-memories titles fonts
immich-memories titles fonts --list

# Download all fonts
immich-memories titles fonts --download

# Clear the font cache
immich-memories titles fonts --clear
```

Fonts work offline from the bundled files. `--download` adds copies to the cache; it does not
replace bundled fonts, which take precedence. `--clear` removes only those cached copies.

The listing checks the cache, so **Not downloaded** on a fresh install is expected. It does
not mean a font is missing from the package.
