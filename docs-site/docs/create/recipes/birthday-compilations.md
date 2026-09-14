---
sidebar_position: 1
title: Birthday Compilations
---

# Birthday Compilations

A birthday compilation is the year of someone's life that **ends** on the birthday you are celebrating, plus that birthday in earlier years. For a 21 July birthday and `--year 2025` it runs 22 July 2024 through 21 July 2025: the scope includes the birthday being celebrated. Selection still decides which pictures
make the video.

## Set the birth date in Immich

Immich is where the date lives. Open **People**, pick the person, edit, and fill in the birth date. The CLI, Memory page and automation can all use that date.

If the date is missing, set it in Immich or pass a one-run override below.

## What the video covers

| Window | Span | Why |
|---|---|---|
| The rolling year | day after the previous birthday → the birthday | The year being celebrated |
| Each earlier birthday | ±1 day around it | The "look how small you were" cutaways |

Five earlier birthdays by default. `--years-back` changes that reach. On This Day has a
different default: 30 years.

Most of those single days hold nothing, and that is expected: the run prints one summary line for them (`history: 2 of 5 earlier windows hold material`) instead of a warning per year. An empty **rolling year** gets a warning; check the dates, person and available material.

## CLI

With the birth date in Immich, `--birthday` takes no value:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2025 \
  --birthday \
  --duration 600
```

To override it for one run (the stored date is missing or wrong), give the date:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2025 \
  --birthday 07-21 \
  --duration 600
```

`--year` names the birthday being celebrated, not a calendar year to run forward from.

Give the date as `MM-DD`: month first, matching every other date in the project (RFC 3339 order). Slashed forms are rejected rather than guessed.

29 February is celebrated on the 28th in a year that has no 29th, and the ±1 day cutaways still reach the 29th in the years that do.

## UI

There is no "Birthday" memory type. Two paths get you a birthday-anchored range:

- **Person Spotlight**: pick the person, then tick **Birthday to birthday**. If Immich
  has a birth date on that person the checkbox turns itself on when you select them; without
  one it stays greyed out and says so.
- **Custom date range** → **Year** tab → **From Birthday**, which gives you a Birthday date field
  and computes the rolling year from it. This one is the year only, no earlier-birthday
  cutaways.

See [the Memory page](../web-ui/memory.mdx#memory-types-and-their-parameters).

## Before showing it

The default target is 600 seconds. Adjust `--duration` for your audience, and render ahead of
time so you can check the excerpts and music. If names are duplicated in Immich, confirm the
selected face record before generating.
