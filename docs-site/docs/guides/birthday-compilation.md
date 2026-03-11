---
sidebar_position: 2
title: Birthday Compilations
---

# Birthday Compilations

A birthday compilation spans from one birthday to the next — for example, Jul 21, 2024 through Jul 20, 2025. It captures a full year of someone's life, which makes for a great video to play at their birthday party.

## CLI

```bash
immich-memories generate \
  --person "Emma" \
  --birthday 2024-07-21 \
  --duration 10
```

The `--birthday` flag sets the start date. The end date is automatically one year later (minus one day).

## UI

In Step 1, select the "Birthday" time period option. Pick the birthday date and the tool calculates the range for you.

## Tips

- **10 minutes** is a good target duration for a party slideshow. Long enough to feel substantial, short enough that people don't lose interest.
- **Enable music** if you've set up a backend. A soundtrack makes birthday videos way more watchable.
- **Run analysis ahead of time** so you're not waiting at the party. Generate the video the night before.
- If the person has a common name in your Immich library, double-check the face recognition is matching the right person before generating.
