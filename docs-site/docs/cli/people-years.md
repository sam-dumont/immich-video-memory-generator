---
sidebar_position: 3
title: people & years
---

# people & years

Two quick lookup commands that query your Immich server. Useful before running `generate` so you know what's available.

## people

Lists all named people from Immich's face recognition.

```bash
immich-memories people
```

Output looks like:

```
┌─────────────┬──────────┐
│ Name        │ ID       │
├─────────────┼──────────┤
│ Emma        │ a7b3c... │
│ John        │ f2d1e... │
│ Sarah       │ 9c4a1... │
└─────────────┴──────────┘

Total: 3 named people
```

Only people with names show up. If someone's face is recognized but not named in Immich, they won't appear here. Go name them in Immich first.

You can then use any of these names with `--person` in the `generate` command:

```bash
immich-memories generate --year 2024 --person "Emma"
```

## years

Lists every year that has at least one video in your Immich library.

```bash
immich-memories years
```

Output:

```
Years with video content:
  - 2020
  - 2021
  - 2022
  - 2023
  - 2024
```

Both commands require a working Immich connection. If you haven't configured one yet, run `immich-memories config` first.
