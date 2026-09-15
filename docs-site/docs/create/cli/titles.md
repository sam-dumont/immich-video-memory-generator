---
sidebar_position: 3
title: titles
---

# titles

Preview a title card, and manage the fonts. What the cards look like and where they get inserted
is [Title Screens & Maps](../pipeline/title-screens-and-maps.md).

## Date and place captions

`generate --add-date --add-place` burns small translucent context onto clips: 48 px on a 1080p
frame at 85% opacity, date and place appearing independently as each one changes. Missing metadata
on a clip does not restart a caption that is already running. Captions stay clear of dissolves, so
the outgoing and incoming labels cannot overlap, and a clip too short to hold a window keeps its
caption for its whole length instead of losing it.

Places you are at constantly stay unlabelled, because the name of your own town over every third
clip is noise. Familiarity is measured on GPS observations within **250 m of the asset**, across
the accessible library's history, and a place qualifies on either pattern:

- 12 distinct visit weeks across six months, in each of two years;
- 3 distinct visit weeks across three months, in each of five years.

Several photos in one week count as one visit, so an annual summer holiday never qualifies on its
own. These are conservative recurrence rules, not proof of residence, and they never suppress an
entire city: the 250 m circle is the unit.

`trips.homebase_latitude` and `trips.homebase_longitude` mark the home circle as well. Its GPS
history supplies the home country, which is then left out of domestic captions while foreign
countries stay visible; where the country is unknown or conflicting, the original caption remains.
A picture with no GPS cannot be matched to a familiar place by city name alone. Maps keep the
original location data either way.

The first render with place captions reads library metadata and downloads no photos for it. The
answer is cached under `cache.directory/familiar-places/`, keyed separately per server and
credential scope, for seven days; delete that directory to pick up metadata edits or removals
sooner. Privacy mode, and any render without place captions, skips the scan.

## titles test

Renders one standalone title card to a file, so you can look at a style before paying for a full
run. Every flag is in the [CLI reference](../../reference/cli-reference.md#titles-test).

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

Before you spend time here: titles are Montserrat. Every preset and every mood-derived style
hardcodes it, and Outfit is used only for the date and place captions burned onto clips. Raleway,
Josefin Sans and Quicksand ship and are never selected.

```bash
# List fonts and their status (bare command or --list)
immich-memories titles fonts
immich-memories titles fonts --list

# Download all fonts
immich-memories titles fonts --download

# Clear the font cache
immich-memories titles fonts --clear
```

All five families are OFL-1.1 and live inside the package (`titles/bundled_fonts/`), so titles
render correctly on a fresh install with no network: `get_font_path()` checks the bundled copies
before the cache, and the bundle already carries every weight the renderer asks for. `--download`
mirrors the same files from the Fontsource CDN into `~/.immich-memories/fonts/`. It is there for
inspecting what ships, not for making titles work, and not for replacing them: for any of the five
known families the bundled file wins before the cache is consulted, so a TTF you drop in there is
never loaded.

The listing reads the cache directory only, so on a fresh install it says **Not downloaded** for
all five while titles render perfectly from the bundle. Read that column as "is there a copy in the
cache", not as "will this work" and not as "is this what renders".
