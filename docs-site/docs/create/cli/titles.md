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

Every flag is in the [CLI reference](../../reference/cli-reference.md#titles-test).

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

Worth knowing before you spend time here: titles are Montserrat. Every preset and every mood-derived style hardcodes it, and Outfit is used only for the date and place captions burned onto clips. Raleway, Josefin Sans and Quicksand ship and are never selected.

```bash
# List fonts and their status (bare command or --list)
immich-memories titles fonts
immich-memories titles fonts --list

# Download all fonts
immich-memories titles fonts --download

# Clear the font cache
immich-memories titles fonts --clear
```

You do not have to run any of this. Titles render correctly on a fresh install with no network, because `get_font_path()` checks the bundled copies before the cache and the bundle already carries every weight the renderer asks for. `--download` mirrors the same files from the CDN into `~/.immich-memories/fonts/`; it is there for inspecting what ships, not for making titles work, and not for replacing them: for any of the five known families the bundled file wins before the cache is consulted, so a TTF you drop in there is never loaded.

The listing reads the cache directory only, so on a fresh install it says **Not downloaded** for all five while titles render perfectly from the bundle. Read that column as "is there a copy in the cache", not as "will this work" and not as "is this what renders".
