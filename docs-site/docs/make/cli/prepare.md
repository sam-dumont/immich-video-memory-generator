---
sidebar_position: 2
sidebar_label: "prepare, people, discover-days"
title: Preparing a library
---

# Preparing a library

Reader: power user.

Four commands that run against the library rather than against one film: `prepare` does the pixel work up
front, `people` works out who is in it, `discover-days` finds the days worth remembering, and a few small ones
answer questions before you generate anything. All of them work on a plain NAS; `prepare --overviews` is the one
that needs a model.

## `prepare`

Preparation is the part of a cut that looks at pixels: a preview, its measurements, the encoder and eight
context heads, the two detectors, and on the `full` tier a caption. Everything after it (grouping, reading,
selection, render) is text and arithmetic.

A film prepares only the pictures it can reach: the ones selection can pick, their Live Photo clips and the
shots taken in the same run. `prepare` does the whole scope instead, so every later film over it starts warm:

```bash
immich-memories models fetch                 # once, before the first run
immich-memories prepare --year 2024 --month 6
```

It prepares the month and stops: no selection, no video. The scope flags are `generate`'s (`--year`,
`--year --month`, `--start --end`, `--start --period`), and the scope is the one a cut would read: no archived
or hidden assets, no forwarded or re-encoded media, none of the films this app uploaded. `--month` needs
`--year`. Each run resumes where the last stopped, so a loop over twelve months works through a year.

Results are banked per picture, and stay free for every later cut until a producer's version changes (a new
head is paid for once, at the next run).

```text
ℹ Preparing 1,440 pictures over 1 window(s)
producer        pending   s/picture   share    elapsed
previews           1440      0.0180    1.9%       26 s
pixels             1440      0.1250   13.2%      3 min
public_heads       1440      0.6070   64.0%     15 min
detectors          1440      0.1980   20.9%      5 min
total              1440      0.9480    100%     23 min

At this rate 10,000 pictures would take 2 h 38 min.
```

`--library-size 10000` prints the last line. `s/picture` is the number to compare between machines; `share`
says which producer to move to a faster box. With [the inference service](../../better/inference.md) the heads
and detectors run elsewhere, and a `remote_facts` row appears.

Exit 0 means every producer a cut needs finished for every picture. Exit 1 means facts are still missing, and
the run names the producer and the count. On the `full` tier the usual cause is a caption server that is not
running (a 401 or 403 names `advanced.editorial.preparation.caption_api_key`). Rerunning is cheap: run it until
it exits 0.

Preparation is the only stage that can send pixels anywhere, and only to endpoints you set. On the default
install it sends nothing. See [Privacy](../../run/privacy.md).

### `--overviews` (needs a reader)

```bash
immich-memories prepare --year 2024 --month 6 --overviews
```

Make it better (optional). With a model reader configured, `--overviews` also reads each 90-minute episode once
and writes one account per calendar month: a couple of sentences saying what the month was. A model cut of that
month reads it as its thesis instead of paying for it during the cut. It is banked by the readings it
summarises and the model that wrote them, so a rerun over an unchanged month asks nothing. You never have to run
it: a model cut that finds no account writes its own. It is worth it when you would rather pay for a year of
months overnight. With `advanced.editorial.reader: rules` it is refused by name. What the account is for is on
[What a model adds](../../how-it-chooses/what-a-model-adds.md).

## `people`

Works out who is in your library from the numbers Immich already holds, writes it to a file you can edit, and
never overwrites an answer you gave it. It reads counts and dates only, and asks you nothing.

```bash
immich-memories people scan     # build or refresh the file
immich-memories people show     # read it back, --tier narrows it
```

The one rule doing most of the work: volume is a burst, continuity is a relationship. 160 pictures over four
active months is four events; the same 160 over forty months is part of your life.

| tier | shape |
|---|---|
| `inner` | dozens of active months, years of span, present in at least a third of the months between |
| `recurring` | a dozen months or more, failing one of the `inner` conditions |
| `episodic` | everything that is not one of the other three |
| `event` | four active months or fewer at twenty-plus pictures each: a burst |

The scan also flags tight pairs (two people who are each a quarter or more of each other's pictures), twins
(same family name and birth date, marked `counts_reliable: false` because face recognition merges them) and one
name on two person records. You are behind the camera, so pairs with you are read from month curves, not shared
frames. The owner comes from `--owner` or `IMMICH_MEMORIES_OWNER` (`identified: told`), else your Immich account
name (`account`), else the longest-running person (`inferred`: check it).

The file is `~/.immich-memories/people.yaml`, readable only by you. Everything under `inferred:` is recomputed on
each scan; everything under `confirmed:` is yours and never overwritten, and wins where the two disagree:

```yaml
people:
  - ids: [5f2c…]
    name: Alex Example
    birth_date: '1988-04-02'
    inferred:
      tier: inner
      evidence: {count: 4210, active_months: 180, span_years: 17.2, onset: '2009-06', continuity: 0.87}
    confirmed:
      role: null
      links: []
```

It is the same file as the **People** page in the web UI. The roles you confirm there decide who counts as close
family, and selection reads that on every tier: the family seat, the big-story rule, and the relations a model
sees. Setting it up is on [Teach it your family](../../get-started/who-is-who.md); how selection uses it is on
[Family, audience and duplicates](../../how-it-chooses/family-audience-duplicates.md).

## `discover-days`

Finds the days something happened on and writes them to `~/.immich-memories/special-days.json`, so a film can
arrive years later without you asking ("five years ago today"). Run it once, then now and then. Films from it
are the **Surprise me** type on [Memory types](../memory-types.mdx#special-day-surprise-me).

```bash
immich-memories discover-days --since 2015
```

A day ends when the pictures stop for five hours, not at midnight. Days inside a trip are skipped (the trip film
tells that story), and so are holidays spent at home, which have their own type.

**On a plain NAS** (the default) nothing is asked. A day counts when one recorded fact is loud: most of its
located pictures away from home, at least three favourites, at least three videos making half the day, or a
long day (20 pictures over six active hours) with your close family on it. Each year keeps its strongest
`advanced.automation.special_days_per_year` (6): days away first, the furthest first, then favourites, then
video share, then family presence. The title is "A day in" the place. Without a `people.yaml`, a long day at
home is not found.

**With a reader** (optional), every run of activity is read a month at a time as one line of recorded facts
(time, place, counts, who Immich recognised, close family by role, up to three captions), and the model names
the occasions: the kind of day people tell others about afterwards. A good afternoon at home is not one. No
yearly cap. Titles are checked against what the day recorded: a place it never went or a claim nothing supports
gets the title asked for once more, then the day is dropped.

Either way a day is kept only if a film of it can run 30 seconds, and a day can carry a window (the stretch at
the circuit inside a long day) when that window holds at least half its pictures.

The scan resumes by default: years already in the catalogue are skipped. `--rescan` starts over.
`--replace --since 2024 --until 2024` re-scans those years and replaces what they hold, which is how you clean
rows `days-due` marks `stale` (judged by an older version of the question). Without either flag the catalogue
is only ever added to.

```bash
immich-memories days-due              # anniversaries within three days, roundest first
immich-memories days-due --on 2026-12-24
```

## Small questions

```bash
immich-memories people          # every named person Immich knows
immich-memories years           # the years that contain video
immich-memories preflight       # can it reach Immich, the models, the renderer
```

Bare `people` lists names exactly as Immich holds them: "Emma" versus "Emma S." is the difference between a
film and an empty pool. `years` saves you guessing at `--year` on a library imported from old backups.

`analyze` and `export-project` are older commands. `analyze` counts a year's videos and prepares nothing (use
`prepare`); `export-project` writes a JSON list of the videos in scope that nothing reads back. To see how a
cut was reached, use [`runs why`](./runs.md#runs-why).
