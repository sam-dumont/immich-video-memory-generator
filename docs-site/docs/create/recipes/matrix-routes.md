---
sidebar_position: 5
title: Render a Matrix of Routes
---

# Render a Matrix of Routes

When selection changes, one film tells you almost nothing. You need one of each kind (a month, a
person, a trip, a year, an album) rendered the same way, uploaded to the same place, watched back
to back. `scripts/matrix_routes.py` is the driver that does that through the public CLI. It adds no
selection logic of its own: every case is an ordinary `immich-memories generate` call.

## Two files, and why

**The routes are public.** `examples/matrix-routes.example.json` lists eleven routes: one per
memory type, plus a repeat of the monthly route. Each entry names a memory type, a target duration
(`null` means "let the memory type decide"), a bundled music loop, and a scope whose values are
`@placeholders`:

```json
{
  "id": "monthly-highlights",
  "memory_type": "monthly_highlights",
  "duration_seconds": 60,
  "music": "nostalgic/nostalgic_acoustic_1.opus",
  "scope": {"year": "@year", "month": "@month"}
}
```

**One route needs a date pinned.** `on_this_day` is scoped to whatever today is, so on any other
day it asks a different question and cannot replay off the bank. Give it a `target_date` and the
case becomes reproducible:

```json
{"id": "on-this-day", "memory_type": "on_this_day",
 "scope": {"years_back": "@years_back", "target_date": "@on_this_day_date"}}
```

Pick a day the bank already holds and the route replays with no provider calls, like the other
ten. `generate` accepts a chosen date only from the automation runner, and only alongside the rest
of that runner's identity, so the driver expands the pin into all four flags rather than one.

**The values are private.** Which year, which person, which album: those never enter the
repository. They live in an overlay file of your own, anywhere outside it:

```json
{
  "schema": "matrix-routes-private-v1",
  "config_path": "~/.config/immich-memories/config.yaml",
  "database_path": "~/.immich-memories/cache.db",
  "editorial_runs": "~/.immich-memories/cache/editorial-runs",
  "output_root": "~/matrix/2026-09-10",
  "album_name": "Memories matrix 2026-09-10",
  "upload": true,
  "values": {
    "monthly-highlights": {"year": 2024, "month": 6},
    "person-spotlight": {"person": "…", "year": 2024}
  },
  "reference": {
    "monthly-highlights": {
      "plan_sha256": "…",
      "attempt_path": "~/.immich-memories/cache/editorial-runs/…/attempts/…"
    }
  }
}
```

A placeholder with no value in the overlay is a hard error at `build` time. It is never skipped:
rendering nine routes and quietly dropping the tenth is how you end up grading a matrix that has a
hole in it and not knowing.

The `reference` block is optional and holds what you already approved: the plan hash of the
accepted run, and ideally the attempt directory it was written in. `report` compares against it.

## Running it

```bash
python scripts/matrix_routes.py build \
  --routes examples/matrix-routes.example.json \
  --private ~/matrix/private.json \
  --manifest ~/matrix/manifest.private.json

python scripts/matrix_routes.py run     --manifest ~/matrix/manifest.private.json
python scripts/matrix_routes.py collect --manifest ~/matrix/manifest.private.json
python scripts/matrix_routes.py report  --manifest ~/matrix/manifest.private.json
```

`build` joins the two files, resolves the bundled loop against the installed music package, and
renders each route's full argument list. Every flag it produces is checked against the live Click
tree for `generate`, so a renamed option fails here rather than three hours into a batch.

Every case gets the same fixed flags: `--include-photos --include-live-photos --add-date
--add-place --quiet --resolution 1080p --format h265`. The only thing that varies between films is
the route.

`run` is serial and resumable. It holds an exclusive lock on the manifest, re-checks the frozen
config's SHA-256 before every case, refuses to start below the free-disk floor (50 GiB unless the overlay sets `min_free_gib`), and writes each case's log
next to its film. A case that already produced a film is skipped, but only after its recorded
digest still matches, so an artifact that changed under you is an error, not a silent skip. Add
`--only <id>` for one route, `--retry-failed` to pick up the ones that broke.

`collect` decides whether a film is watchable, and never trusts the exit code. Per case: exactly
one `.mp4` in the case's own directory, 1920×1080, an audio stream present, the
`Audio mixed successfully` line in the run log, and a full `ffmpeg -xerror … -f null -` decode of
both streams. Then it harvests the run's editorial attempt (carriers, content seconds, duration
realization, LLM calls, plan hash) and the tracked run row. **If the batch asked for an upload and
no Immich asset id was recorded, the case is `failed`, not `ready`**: a film nobody can find in
Immich has not been delivered.

On its own, `collect` verifies whatever `run` has just rendered. `--only <id>` re-verifies one case
whatever state it is in: that is how you retry a verification that failed.

## The album convention

One dated album per matrix, named in the overlay: `Memories matrix 2026-09-10`. Every route in
the batch uploads there, so the whole set is one scroll in Immich and old batches never mix into a
new one. Make a new album for the next matrix rather than reusing this one.

The eleventh route, `monthly-supersede`, exists to exercise one behaviour: uploading a memory whose
recipe already exists in the album should trash the older copy rather than sit beside it. Immich
matches on the uploaded file's name, and with `--output` set that name is the driver's, so the
supersede route deliberately writes to the same file name as `monthly-highlights`. `build` refuses
to let it run unless every other argument matches too: otherwise it would trash a different memory.
After the batch, that album should hold ten films, not eleven.

## Reading the report

`report` prints two tables and writes a private copy (`…-report.private.json` and `.private.md`,
mode 0600) beside the manifest. The console form carries counts only; asset ids, attempt paths and
film paths stay in the file.

The first table is what came out: carriers, content seconds, whether the duration landed
(`near_target`, `search_limited`, `editorial_shortfall`), LLM calls over cache hits, film length,
and whether Immich took it.

The second is the one that decides anything:

| Verdict | Means |
|---|---|
| `identical` | The plan bytes match the accepted run. Your previous grade still stands; you do not need to watch it again. |
| `same-carriers` | Different plan, same clips in the same order. Something around selection moved; the cut did not. |
| `changed; owner approval not transferred` | A different cut. Watch it. |
| `no reference` | Nothing banked for this route yet. |
| `not collected` | `collect` has not run, or it failed. |

Alongside the verdict: how many carriers were added, how many removed, how the content seconds
moved, and how many of the reference run's favourites survived. Favourite retention only appears
when the overlay names the reference `attempt_path`: the plan file there is what knows which
carriers were starred.
