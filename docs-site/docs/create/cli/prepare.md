---
sidebar_position: 2
sidebar_label: "Preparing a library"
title: Preparing a library
---

# Preparing a library

Four commands that run against the library rather than against one memory: `prepare` does the
expensive pixel work up front, `people` works out who is in it, `discover-days` finds the days worth
remembering, and a handful of small ones answer questions before you generate anything.

## `prepare`

Preparation is the part of a cut that looks at pixels: a preview for every eligible picture, its
measurements, the encoder and eight context heads, both detectors, and on the `full` tier a caption.
Everything after it (grouping, reading, selection, render) is text and arithmetic.

It is banked per picture, so a picture prepared today is free for every later cut until a producer's
version changes. That is why it is worth doing on its own:

```bash
immich-memories prepare --year 2024 --month 6
```

That prepares the month and stops. No selection, no video. The scope flags are `generate`'s
(`--year`, `--year --month`, `--start --end`, `--start --period`) and it prepares exactly the
pictures a cut over that scope would: no archived or hidden assets, no forwarded or re-encoded
media, none of the films this app already uploaded, Live Photo components handled the same way.
Both ask one function for the scope, so they cannot drift apart. A person film over the same scope
prepares less than that at film time: only the pictures it can select, their capture runs and their
Live Photo families, so `prepare` is the way to fill the rest of the window ahead. "Free for every later cut" still
holds only until a producer's version changes: a store prepared before a new head shipped pays that
head at the next cut. Each run resumes where the last stopped, so a
`for month in 1 2 3 …` loop works through a year.

`--month` needs `--year`, and is refused without one. Before
[#1056](https://github.com/sam-dumont/immich-video-memory-generator/pull/1056), which landed after
the release of `0.102.0`, `prepare` dropped `--month` without a word and prepared the whole calendar
year, so a "month" timed on an older build is a year.

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

`--library-size 10000` is what prints that last line. `s/picture` is the number to compare between
machines. `pending` is what the producer still had to do: the whole scope on a cold run, a handful
on a rerun. `share` says which producer to move to a faster machine; with
[the inference service](../../deploy/installation/inference-service.md) the heads and detectors can
run elsewhere, and the table then shows a `remote_facts` row with a `service s/pic` column beside the
wall clock, saying how much of the wait was the classifiers deciding rather than the wire.

Exit 0 means every producer a cut needs finished for every picture. Exit 1 means facts are still
missing and the run says which producer and how many; a caption server that is not running is the
usual cause, and one that is running but answers 401 or 403 says so and names
`advanced.editorial.preparation.caption_api_key`. Rerunning is cheap, so "run it until it exits 0"
is the intended loop.

### `--overviews`: what the month was about

```bash
immich-memories prepare --year 2024 --month 6 --overviews
```

Preparation banks facts about pictures. `--overviews` goes one step further and banks meaning: it
reads each 90-minute episode of the scope once, then writes one account per calendar month: a
couple of sentences saying what that month was, written over the episode readings rather than over
captions. A cut of that month reads it as its thesis instead of working one out, which is what
[the thin model layer](../pipeline.md) needs to run at all.

It is banked like everything else: the account is keyed by the readings it summarises and by the
model that wrote them, so running it twice over an unchanged month asks nothing, and one changed
episode reopens that month and no other. A month holding a single episode is copied up with no call
at all.

The episode readings it banks are the ones every later cut of that scope reads, so `--overviews`
also pays that cost up front. A banked answer is only ground truth for the question that was
asked, so a change to the episode prompt expires every reading it produced and the next run over
that scope reads its episodes once more.

```text
✓ Banked 37 episode readings, 1 month and 1 year account(s).
```

You do not have to run it. A cut of a whole calendar month, a whole year or several years that
finds no account writes its own, from the readings its own event pass has just paid for. `--overviews` is worth running when
you would rather pay for a year of months overnight than during the first cut of each one.

It needs a model reader: `--overviews` with `advanced.editorial.reader: rules` is refused by name,
because an account is a reading and the rules reader does not read.

Preparation is the only stage that sends pixels anywhere, and it sends them only where you point it.
Both endpoints default to `localhost` and nothing asks a second time once you point one elsewhere:
read [Network and privacy](../../deploy/configuration/network-and-privacy.md#the-two-picture-seats)
first. Before the first run, `immich-memories models fetch` puts the pinned encoder and detector
files on disk.

## `people`

Works out who is in your library from the numbers Immich already holds, writes it to a file you can
edit, and never overwrites an answer you gave it.

```bash
immich-memories people scan     # build or refresh the file
immich-memories people show     # read it back, --tier narrows it
```

Nothing here looks at a pixel and nothing asks you a question. Counts, names, birth dates, the
months each person appears in and how often two people appear together are enough, which is the
point: curation you already did inside Immich has to pay off somewhere.

**Volume is a burst, continuity is a relationship.** That one rule does most of the work. A person
with 160 pictures spread over four active months across scattered years was at four events with you.
The same 160 pictures over forty months is part of your life. Pictures divided by active months is
the discriminator that volume alone is not.

| tier | shape |
|---|---|
| `inner` | dozens of active months, years of span, present in at least a third of the months between |
| `recurring` | a dozen months or more, failing one of the `inner` conditions |
| `episodic` | everything that is not one of the other three |
| `event` | four active months or fewer at twenty-plus pictures each: a burst |

On top of the tiers the scan reads four things. **Onset** is the first month with three more active
months inside the following year, so one picture in 2011 and a real presence from 2018 makes the
onset 2018. **Tight dyads** are two people who are each a quarter or more of *each other's*
pictures; mutual is the point, because everybody appears in the busiest person's frames. **Twins**
are two people with the same family name and birth date, flagged because face recognition merges
identical faces and one record ends up holding nearly every picture (both are marked
`counts_reliable: false`). **Duplicates** are one name on two person records, a split face cluster
in Immich, which the graph flags rather than fixes.

A birth date changes the reading: someone born after your library started cannot have a span longer
than their age, so span roughly equal to age means they have been here since day one. That is a
two-year-old, not a friend you met two years ago.

Co-occurrence undercounts every pair containing you, because you are behind the camera. Measured on
a real library: in the quarter the owner met their partner, the partner appears twenty-five times
and they share zero frames. So the dyad heuristic ignores co-appearance for the owner and reads
month curves instead. The owner is identified three ways, and the file records which: `--owner
"Their Name"` or `IMMICH_MEMORIES_OWNER` writes `identified: told`, the name on your Immich account
writes `identified: account`, and failing both, the person with the longest span and most pictures
is written `identified: inferred`. If it says `inferred`, check it.

The file is `~/.immich-memories/people.yaml`, readable only by you and gitignored, because it holds
the names of everyone in your library. Everything under `inferred:` is the scan's reading and gets
recomputed every run. Everything under `confirmed:` is yours, and the contract is that a refresh
never writes into a `confirmed:` field, never drops a person carrying anything confirmed even if
they fall off the roster, and consumers prefer `confirmed:` where the two disagree.

```yaml
people:
  - ids: [5f2c…]
    name: Alex Example
    birth_date: '1988-04-02'
    inferred:
      tier: inner
      counts_reliable: true
      evidence: {count: 4210, active_months: 180, span_years: 17.2, onset: '2009-06', continuity: 0.87}
      links:
        - {kind: tight-dyad, with: 91ab…, confidence: 0.51, via: co-occurrence}
    confirmed:
      role: null
      links: []
      notes: null
```

The same file is the **People** page in the web UI: one card per person with their face crop, tier
and evidence, a role select, notes, and a check or cross on each link the scan found. Both write the
same schema through the same writer.

`people scan` prints tier counts and the file path rather than the roster, because a terminal may be
a log. The flag worth setting is `--min-assets` (25). Unnamed faces are skipped either way; naming
them is work that belongs in Immich.

Every cut loads `people.yaml` and renders a `people` block onto the wall the text model reads: id,
name, relationship, where that relationship came from, birth date, first appearance, onset and tier.
So who somebody is to you is part of what the model weighs. What does not read it yet: selection
weights, tie-breaks, person-rotation fairness and the automation's person priors.

## `discover-days`

Finds the days something happened on and writes them down, so a memory can arrive years later
without you asking. Run it occasionally, not per video. What it produces and how a day comes back is
on [Memory types](../memory-types.mdx#special-day-surprise-me).

Volume is not what makes a day stand out. In a real library the densest single day is 166 photos of
a work shoot inside one hour, and another holds 413 of one street performer. What separates them is
how long the day stayed alive:

| day | photos | active hours | occasion |
|---|---|---|---|
| a long occasion | 289 | 18 | yes |
| a wedding party | 48 | 12 | yes |
| a track day | 133 | 7 | yes |
| an apartment viewing | 258 | 5 | no |
| a street performer | 413 | 3 | no |
| a work shoot | 166 | 1 | no |

No overlap, but the rule is loose on its own: 22 % of days in that library clear six hours. So it is
a filter, not a verdict, and it keeps the model off the other 78 %, which is what makes asking about
the rest affordable. A 20-photo floor sits beside it. What passes goes to the model as text: one
line per sampled picture, with its capture time and whatever the library records about it, and never
as pixels.

The question it is asked is whether the day was an occasion, the kind of day the people in it would
tell other people about afterwards: a birth, a wedding, a race, a festival, a concert, a first, a
day of a trip, a ceremony. A good day is not an occasion. An afternoon at home, a walk, a meal, a
park or a day spent photographing one subject is ordinary however pleasant it was and however many
pictures it left.

A day ends when the photographs stop for five hours, not at midnight, so a wedding that runs past
one is one occasion. Days inside a detected trip are skipped, because a trip memory already tells
that story, and so are holidays, which have their own type. A holiday is only skipped when the day's
pictures agree it was one, taken around home: a day that merely lands on the same date and was spent
67 km away at a race circuit goes to the model like any other candidate. Both filters read the
thresholds under `trips:`, so this command and the rest of the app agree on what "away" means.

Some days are an event and some contain one. A track day put most of its pictures in one place
inside a couple of hours of a long day, and the memory should start at the circuit, so a discovered
day can carry a window. A window is only recorded when it holds at least half the day's pictures,
and the row says how many it holds. Clock time is the wrong measure of that: the track day spends
2.3 hours of a 10.6-hour day in one place and still holds 92 % of its pictures, while a five-hour
window on a 21-hour day once held 24 of its 379 and the film cut from it was refused for want of
material. The model is asked for the clock times in the same question, reading them off
the per-picture lines: a circuit's coordinates are identical from the moment the car is parked to the
moment it leaves, so the map cannot tell arrival from the start of the race and the pictures can.
Where the model declines, the fallback is the first and last picture of the place that dominates the
day, kept only when trimming to it removes at least 45 minutes and 15 % of the day and leaves at
least half an hour.

```bash
immich-memories discover-days
```

```
2019: 3854 assets
  2019-06-12  A long evening out
```

| option | default | what it does |
|---|---|---|
| `--since` | 2007 | first year to scan |
| `--until` | this year | last year to scan |
| `--per-year` | 6 | how many of the busiest candidate days to ask the model about |
| `--also-skip` | – | a holiday name or `MM-DD` your library keeps that the defaults miss |
| `--out` | `~/.immich-memories/special-days.json` | where to write the catalogue |
| `--rescan` | off | start over, replacing the whole catalogue |
| `--replace` | off | re-scan `--since`..`--until` and replace every row those years hold |

The scan takes hours across twenty years, so it resumes by default: years already in the catalogue
are not scanned again, and a run that finds nothing will not replace a catalogue that has something
in it. Each year costs up to `--per-year` days' worth of model calls and a paged metadata fetch per
month.

### What a day is judged on

Text, and only text. A day the caption bank has been over is judged from those captions. Any other
day is judged from what the library already records about it: capture times, place names,
coordinates, recognised names, which pictures are favourites, which are videos, and whatever
captions the day does have. A day whose lines say nothing at all beyond the hour is recorded as
unjudged instead of being asked about, because a reader handed a column of bare clock times answers
from the calendar date. Unjudged days are written to the catalogue under `unjudged` rather than
`day`, so nothing offers them as a memory and a later run can see they were reached.

Run `immich-memories -v discover-days` to see the lines a judgement read when a day you expected
comes back ordinary.

### Rebuilding a catalogue that drifted

Every row records the prompt version and the app version that judged it. A catalogue that filled up
over several releases holds rows judged against questions this build no longer asks, and `days-due`
marks those `stale`.

```bash
immich-memories discover-days --replace --since 2024 --until 2024
```

That re-scans 2024 and replaces every row those years hold: days, unjudged days and the year markers
that make a resume skip them. Days that no longer qualify are simply not written back. It prints how
many rows it will replace and how many it is keeping before it starts, and it never touches a year
outside `--since`..`--until`. Nothing is deleted on an ordinary run: without `--replace` or
`--rescan` the catalogue is only ever added to.

Rows written before version stamps existed have none, so they read as stale until a `--replace` run
covers their years.

Titles are checked against what the day actually recorded. A title naming a place the day was never
in is dropped, and so is one claiming a distance or a race that nothing the model was shown
mentions: a number on a title card reads exactly as true as a real one. A dropped title is asked for
once more with the claim quoted back, never a third time, and a day with no true title left is left
out of the catalogue rather than written down with an empty one.

```bash
immich-memories days-due              # anniversaries within three days, roundest first
immich-memories days-due --on 2026-12-24
```

```
10 years ago  2015-06-12  A long evening out  18:40-23:55  9h
```

The clock times are the day's window when it found one, and the `9h` is how many hours of the clock
the day put pictures in. Anniversaries either side of New Year are found. Catalogues written before
any of this existed have none of it and still read. A day judged by an older scan is printed with a
`stale` marker, and the count of them is repeated at the end with the command that re-asks a
period.

It needs an LLM configured under `llm:`. A vision model is not needed and is not used: the scan is a
reader of text. What makes it see a day well is preparation, which is where the captions come from,
so `prepare` over a period before scanning it is the single biggest improvement available.

## Small questions

```bash
immich-memories people          # every named person Immich knows
immich-memories years           # the years that actually contain video
immich-memories analyze --year 2024
immich-memories export-project --year 2024 --output project.json
```

`people` (bare, no subcommand) lists names as Immich holds them, and "Emma" versus "Emma S." is the
difference between a memory and an empty pool. `years` saves you guessing at `--year`, which on a
library imported from old backups is often surprising. `analyze` fetches the videos for a year and
prints how many there are: it prepares nothing, warms no bank, never looks at photos, and `--force`
is accepted and ignored. `export-project` writes a JSON snapshot of the **videos** in scope, each
marked `selected: true` because no selection ran.

Nothing reads that JSON back. There is no import command and no flag that consumes it, so treat "for
later editing" in its help text as an aspiration. To see how a selection was actually reached, use
[`generate --trace-selection`](./generate.md#what-a-run-leaves-behind).
