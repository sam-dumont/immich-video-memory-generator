---
sidebar_label: "prepare"
---

# prepare

Prepare annotations for a date range without selecting clips or rendering a video. This is
useful for filling the cache overnight on a slow machine.

```bash
immich-memories prepare --year 2024 --month 6
```

The preparation tier determines the work: `metadata_only` uses metadata and pixel facts;
`no_captions` adds the encoder, context heads and detectors; `full` also asks the caption server.
See [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md) for the
required models and endpoints.

## Scope and reuse

Use `--year`, `--year --month`, `--start --end`, or `--start --period`:

```bash
immich-memories prepare --start 2024-06-01 --period 1m
```

Preparation uses the normal source eligibility checks, including visibility, camera metadata
and configured filename exclusions. It has no person, album or generation inclusion flags,
so it may prepare more pictures than a particular cut needs.

Annotations are stored per picture in the configured annotation database. Later preparation and
generation runs reuse matching records. A changed producer version or missing preview can
require work again; preparation does not cache the later story selection or rendering.

Work through a library a month at a time:

```bash
for month in 1 2 3 4 5 6 7 8 9 10 11 12; do
  immich-memories prepare --year 2024 --month "$month" || break
done
```

## Timing and failures

```bash
immich-memories prepare --year 2024 --month 6 --library-size 10000
```

The table reports pending pictures, seconds per picture, elapsed time and each producer's share
of the work. `--library-size` only changes the timing projection; it does not change the scope.
A warm run's rate is not an estimate of a cold library's cost.

Exit **0** means preparation finished, or the scope was empty. Exit **1** means producers failed
or required facts are still missing. Read the reported producer and error, fix its configuration
or service, and rerun. Caption authentication errors name
`advanced.editorial.preparation.caption_api_key`.

## Where pictures go

Previews are fetched from your configured Immich server. Pixel facts run locally. Heads and
detectors run locally unless `advanced.inference.facts_base_url` points to an inference service,
which receives previews needing those facts.

The `full` tier sends a 400 px JPEG tile to
`advanced.editorial.preparation.caption_base_url`, default `http://localhost:8092/v1`, for
pictures needing captions. It sends the configured `caption_api_key` as a bearer token.
The separate text reader does not run during `prepare`.

Check [Network & Privacy](../../deploy/configuration/network-and-privacy.md) before pointing
services at another machine. On local model setups, download the pinned artifacts first:

```bash
immich-memories models fetch
```
