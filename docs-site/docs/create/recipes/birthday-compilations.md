---
sidebar_position: 1
title: Birthday Compilations
---

# Birthday Compilations

A birthday compilation spans from one birthday to the next: for example, Jul 21, 2024 through Jul 20, 2025. It captures a full year of someone's life, which makes for a great video to play at their birthday party.

## CLI

If Emma's birthday is set in Immich, just use the `--birthday` flag — it auto-detects:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2024 \
  --birthday \
  --duration 600
```

Or specify the date manually:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2024 \
  --birthday 07-21 \
  --duration 600
```

The `--birthday` flag makes the year run from birthday to birthday (e.g., Jul 21, 2024 through Jul 20, 2025) instead of January to December.

Give the date as `MM-DD` — that form has only one reading. A slashed `DD/MM` works too and is read day-first, the same way `--start` and `--end` read `DD/MM/YYYY`.

## UI

There is no "Birthday" card. Two paths get you a birthday-to-birthday range:

- **Person Spotlight** card: pick the person, then tick **Birthday to birthday**. If Immich
  has a birth date on that person, the checkbox turns itself on when you select them.
- **Custom** card → **Year** tab → **From Birthday**, which gives you a Birthday date field
  and computes the range from it.

Either way the range runs birthday to birthday rather than January to December. See
[Step 1: Configuration](../web-ui/step1-configuration.mdx).

## Tips

- **10 minutes** (600 seconds) is a good target duration for a party slideshow. Long enough to feel complete, short enough that people don't lose interest.
- **Enable music** if you've set up a backend. A soundtrack makes birthday videos way more watchable.
- **Run analysis ahead of time** so you're not waiting at the party. Generate the video the night before.
- If the person has a common name in your Immich library, double-check the face recognition is matching the right person before generating.
