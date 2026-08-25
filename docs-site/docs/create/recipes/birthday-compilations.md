---
sidebar_position: 1
title: Birthday Compilations
---

# Birthday Compilations

A birthday compilation is the year of someone's life that **ends** on the birthday you are celebrating, plus that birthday in earlier years. For a 21 July birthday and `--year 2025` it runs 22 July 2024 through 21 July 2025 — the party itself is in the video, and last year's party belongs to last year's video.

## Set the birth date in Immich

Immich is where the date lives. Open **People**, pick the person, edit, and fill in the birth date. Every birthday memory then anchors on it — the CLI, the wizard and the nightly automation alike — and you never type it again.

Without one, a birthday memory refuses rather than guessing a date and quietly rendering the wrong twelve months.

## What the video covers

| Window | Span | Why |
|---|---|---|
| The rolling year | day after the previous birthday → the birthday | The year being celebrated |
| Each earlier birthday | ±1 day around it | The "look how small you were" cutaways |

Five earlier birthdays by default, the same reach On This Day and Holiday use. `--years-back` changes it, up to thirty.

Most of those single days hold nothing, and that is expected — the run prints one summary line for them (`history: 2 of 5 earlier windows hold material`) instead of a warning per year. An empty **rolling year** does still get a warning: that one means something is wrong.

## CLI

With the birth date in Immich, `--birthday` takes no value:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2025 \
  --birthday \
  --duration 600
```

To override it for one run — the stored date is missing or wrong — give the date:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2025 \
  --birthday 07-21 \
  --duration 600
```

`--year` names the birthday being celebrated, not a calendar year to run forward from.

Give the date as `MM-DD` — that form has only one reading. A slashed `DD/MM` works too and is read day-first, the same way `--start` and `--end` read `DD/MM/YYYY`.

29 February is celebrated on the 28th in a year that has no 29th, and the ±1 day cutaways still reach the 29th in the years that do.

## UI

There is no "Birthday" card. Two paths get you a birthday-anchored range:

- **Person Spotlight** card: pick the person, then tick **Birthday to birthday**. If Immich
  has a birth date on that person the checkbox turns itself on when you select them; without
  one it stays greyed out and says so.
- **Custom** card → **Year** tab → **From Birthday**, which gives you a Birthday date field
  and computes the rolling year from it. This one is the year only — no earlier-birthday
  cutaways.

See [Step 1: Configuration](../web-ui/step1-configuration.mdx).

## Tips

- **10 minutes** (600 seconds) is a good target duration for a party slideshow. Long enough to feel complete, short enough that people don't lose interest.
- **Enable music** if you've set up a backend. A soundtrack makes birthday videos way more watchable.
- **Run analysis ahead of time** so you're not waiting at the party. Generate the video the night before.
- If the person has a common name in your Immich library, double-check the face recognition is matching the right person before generating.
