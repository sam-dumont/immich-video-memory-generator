---
sidebar_label: "Discovery & utility"
---

# Discovery and utility commands

Four small commands that exist mostly to answer questions before you generate anything. They were reachable only through `--help` and the generated [CLI reference](../../reference/cli-reference.md) until now.

## `people` — who Immich knows

```bash
immich-memories people
```

Lists every named person in your Immich library. Useful because `--person` matches on the name Immich holds, and "Emma" versus "Emma S." is the difference between a memory and an empty pool.

`people` also has subcommands now — `people scan` and `people show` build and read the
[people graph](./people.md), which works out who is who from counts and dates. Calling
`immich-memories people` on its own still does exactly what it did before.

## `years` — where the material is

```bash
immich-memories years
```

Lists the years that actually contain video, so you are not guessing at `--year`. On a library imported from old backups this is often surprising.

## `analyze` — warm the cache deliberately

```bash
immich-memories analyze --year 2024
immich-memories analyze --year 2024 --force
```

Runs analysis over a year and caches the results without generating anything. Generation does this on demand, so `analyze` buys you nothing except **control over when it happens** — which is the whole point on a NAS. A cold year is the expensive part of a run; doing it overnight means the memory you ask for in the morning is a warm run.

`--force` re-analyses videos that are already cached. You want it after changing something that would alter how clips are scored; without it, cached results are reused as-is.

## `export-project` — a snapshot of what would be selected

```bash
immich-memories export-project --year 2024 --output project.json
immich-memories export-project --year 2024 --person "Emma" --output project.json
```

Writes a JSON file describing the assets in scope: the year, the person if you named one, and every clip with its asset id, filename, date and duration, each marked `selected: true`.

:::note Nothing reads this file back
There is no import command and no flag that consumes the JSON. `export-project` is a one-way
snapshot — useful for inspecting or scripting against what a scope contains, not for editing a
selection and feeding it back in. Treat "for later editing" in its help text as an
aspiration rather than a description.
:::

If you want to see how a selection was actually *reached* — including which stage dropped what — use [`generate --trace-selection`](./generate.md#why-did-selection-drop-that-clip) instead. That reports on a real run.
