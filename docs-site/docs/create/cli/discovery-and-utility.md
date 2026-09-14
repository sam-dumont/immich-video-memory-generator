---
sidebar_label: "Discovery & utility"
---

# Discovery and utility commands

Use these before a cut to check what Immich exposes or prepare a period's annotations.

## `people`: names you can select

```bash
immich-memories people
```

Lists named people in your Immich library. Use those names with `generate --person`.
[`people scan` and `people show`](./people.md) build and review relationship context for the editor.

## `years`: years containing video

```bash
immich-memories years
```

Lists years containing videos. Photo-only years do not appear here; their absence does not mean
that a photo memory would be empty.

## Prepare or inspect a cut

- [`prepare --year 2024 --month 6`](./prepare.md) fills missing annotations for the month without selecting or rendering.
- [`generate --year 2024 --month 6 --dry-run`](./generate.md#two-ways-to-skip-the-video) discovers the source and reports missing preparation, without doing it.
- `generate --year 2024 --month 6 --no-render` runs selection and stops before rendering.
- [`runs story` and `runs why`](./runs.md#runs-story) explain a saved cut. `generate --trace-selection selection.txt` also writes a text report and JSON companion at a chosen path.

For connection and hardware checks, see [Health, logs and cache](../../deploy/maintenance/health-logs-cache.md).
