---
sidebar_label: "Discovery & utility"
---

# Discovery and utility commands

Four small commands that exist mostly to answer questions before you generate anything. They were reachable only through `--help` and the generated [CLI reference](../../reference/cli-reference.md) until now.

## `people`: who Immich knows

```bash
immich-memories people
```

Lists every named person in your Immich library. Useful because `--person` matches on the name Immich holds, and "Emma" versus "Emma S." is the difference between a memory and an empty pool.

`people` also has subcommands now: `people scan` and `people show` build and read the
[people graph](./people.md), which works out who is who from counts and dates. Calling
`immich-memories people` on its own still does exactly what it did before.

## `years`: where the material is

```bash
immich-memories years
```

Lists the years that actually contain video, so you are not guessing at `--year`. On a library imported from old backups this is often surprising.

## `analyze`: counts videos, nothing more

```bash
immich-memories analyze --year 2024
```

It fetches the videos for a year and prints how many there are. It does not prepare annotations,
it does not warm any bank, and it never looks at photos. `--force` is accepted and ignored.

There is no command that pre-warms the editor's banks: only a cut fills them. On a NAS, run the
cut you want overnight and let the second one be cheap.

## `export-project`: a snapshot of what a scope contains

```bash
immich-memories export-project --year 2024 --output project.json
immich-memories export-project --year 2024 --person "Emma" --output project.json
```

Writes a JSON file describing the **videos** in scope (photos are not included): the year, the person if you named one, and every clip with its asset id, filename, date, duration and a full-length `segment`, each marked `selected: true` because no selection ran.

:::note Nothing reads this file back
There is no import command and no flag that consumes the JSON. `export-project` is a one-way
snapshot: useful for inspecting or scripting against what a scope contains, not for editing a
selection and feeding it back in. Treat "for later editing" in its help text as an
aspiration rather than a description.
:::

If you want to see how a selection was actually *reached* (including which stage dropped what), use [`generate --trace-selection`](./generate.md#why-did-selection-drop-that-clip) instead. That reports on a real run.
