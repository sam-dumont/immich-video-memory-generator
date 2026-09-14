---
sidebar_position: 6
title: discover-days
---

# discover-days

Finds the days in your library that something happened on, and writes them down so a
memory can arrive years later without you asking for it.

Every other memory type answers a question you posed: this month, that trip, that person.
This one is for the memory nobody requested: ten years to the day since the wedding,
five since the race. That only works if the days were found in advance, so this is a
command you run occasionally, not one that runs per video.

## What makes a day stand out

Volume does not. In a real library the densest single day is 166 photos of a work shoot
taken inside one hour; another day holds 413 of one street performer. Neither is an occasion.

What separates them is how long the day stayed alive:

| day | photos | active hours | occasion |
|---|---|---|---|
| a birth | 289 | 18 | yes |
| a wedding party | 48 | 12 | yes |
| a track day | 133 | 7 | yes |
| an apartment viewing | 258 | 5 | no |
| a street performer | 413 | 3 | no |
| a work shoot | 166 | 1 | no |

No overlap, but the rule is loose on its own, since 22% of days in that library clear six
hours. So it is a filter, not a verdict: it keeps the model off the other 78% of days, which
is what makes asking about the rest affordable at all. There is a second bar alongside it, a
20-photo floor, so a quiet day spread over an afternoon is not asked about either. What passes
goes to the model with a sample of the day's pictures, and it is asked the question a person
would ask.

A day also ends when the photographs stop for five hours, not at midnight. A wedding that
runs past one, or a birth that starts with contractions at ten in the evening, is one
occasion; the calendar disagrees.

Two things are skipped: days inside a detected trip, because a trip memory already tells
that story end to end, and holidays, which have their own memory type.

A holiday is only skipped when the day's pictures agree that it was one: that is, when
they were taken around home, where the holiday is actually kept. A day that merely lands
on the same date and was spent 67 km away at a race circuit is not that holiday, and it
goes to the model like any other candidate. A day that recorded no coordinates at all is
skipped on the date alone, as it always was.

Both filters read the thresholds under `trips:` (your homebase, how far from it counts as
away, and how trips are grouped), so this command and the rest of the app agree on what
"away" means.

## The part of the day that was the event

Some days are an event; some days contain one. A track day put most of its pictures in one
place inside a couple of hours of a long day (the rest of that day is a cat on a balcony),
and the memory should start at the circuit. So a discovered day can carry a window: the
hours the thing that happened actually ran.

Two things look for it. The model is asked, in the same question, for the clock times the
event ran between. It reads those off the per-picture lines, which is why it can answer at
all: a circuit's coordinates are identical from the moment the car is parked to the moment
it leaves, so the map cannot tell arrival from the start of the race and the pictures can.

Where the model declines, the geometry is the fallback: the first and last picture of the
place that dominates the day. That window is kept only when trimming to it removes at least
45 minutes and at least 15% of the day, and only when what is left runs at least half an
hour.

A day that was all one thing has nothing to trim and gets no window, which is the right
answer for a wedding that ran fifteen hours in one place.

## Fast eyes, then a considered answer

With `llm.thinking` at `low`, `high` or `max` the question runs in two steps: one fast call that
looks at the sampled thumbnails and writes a line per picture, then a
text-only call that reasons over those lines together with the times, places
and recognised names. With `thinking: disabled`, it stays the single vision call it
has always been, so nothing changes on a server that cannot reason.

Measured across 14 candidate days, one vision call said "special" to all fourteen, and one call
that both looked and reasoned truncated 6 of them past parsing. Two calls was the only shape that
told an occasion from an ordinary Tuesday, and it invents less: the two-step title describes what
is in the pictures. With thinking on, a day costs two calls plus at most one retitle, and the scan
asks about a handful of days per year.

The per-picture lines the judgement read are the record to check first when a day you
expected comes back ordinary. They are logged at `DEBUG`: run `immich-memories -v discover-days`
to see them.

## Running it

```bash
immich-memories discover-days
```

It walks year by year, prints what it finds, and writes a catalogue to
`~/.immich-memories/special-days.json`.

```
2019: 3854 assets
  2019-06-12  A long evening out
2020: 2971 assets
  2020-02-29  Somebody's leap day
```

| option | default | what it does |
|---|---|---|
| `--since` | 2007 | first year to scan |
| `--until` | this year | last year to scan |
| `--per-year` | 6 | how many of the busiest candidate days to ask the model about |
| `--also-skip` | – | a holiday name or `MM-DD` your library keeps that the defaults miss |
| `--out` | `~/.immich-memories/special-days.json` | where to write the catalogue |
| `--rescan` | off | start over, ignoring and replacing the existing catalogue |

The scan takes hours across twenty years, so it resumes by default: years already in the
catalogue are not scanned again, and a run that finds nothing will not replace a catalogue
that has something in it. `--rescan` is how you say you meant to start over.

Each year costs up to `--per-year` days' worth of model calls, which with thinking on is two
calls per day plus a possible retitle, and a paged metadata fetch per month (the densest months
are exactly the ones a single query would truncate). Raising `--per-year` finds more and costs
proportionally more.

## Checking what is due

```bash
immich-memories days-due
```

Prints the discovered days whose anniversary falls within three days of today, roundest
first: ten years reads louder than nine, which is the whole appeal of arriving
unannounced.

```
10 years ago  2015-06-12  A long evening out  18:40-23:55  9h
```

The clock times are the day's window, when it found one. The `9h` is how many hours of
the clock the day put pictures in: the number the scan measured to decide the day was
worth asking about at all, now kept in the catalogue with the times the day's run started
and ended. A run is grouped by the date it began and ends when the pictures stop for five
hours, so a night that ran to three in the morning ends on the following date, and its
extent says so where the date alone cannot. Catalogues written before any of this existed
simply have none of it, and still read.

`--on YYYY-MM-DD` checks a different date, and `--catalogue PATH` reads a different file.
Anniversaries either side of New Year are found: a day at the end of December is due in
early January.

## What happens to a day once it is found

The catalogue is not the point; it is what the point is made of. A day sitting in it
becomes a video three ways:

- **Automation proposes it on its anniversary.** `auto run` reads the catalogue like any
  other detector and puts a due day in the queue, scored by how round the anniversary is,
  one per run at most. It passes a date and nothing else: the title stays in the file.
  See [auto](./auto.md#how-a-candidate-is-chosen).
- **The Memory page's Surprise me type offers all of them.** Due anniversaries first, then
  every other day the catalogue holds, because you asked for it rather than being
  interrupted. See [the Memory page](../web-ui/memory.mdx#memory-types-and-their-parameters).
- **You name one yourself**:
  `immich-memories generate --memory-type special_day --day 2016-06-12`.

All three scope the memory to the day's window when it recorded one, take the runtime
from how long the day stayed awake, and read the title out of the catalogue rather than
off the command line. A day the model could not name is refused rather than rendered
under a generic date. See [Special Days](../memory-types/special-days.mdx).

## What you need

An LLM configured under `llm:`; see [Configuration](/docs/deploy/configuration/config-file).
A vision model is worth having: with pictures the model sees the day, and without them it
reasons from times, places and recognised names alone. That is the difference between
"Driving through somewhere" and knowing what was being driven.

Titles are checked against what the day actually recorded before they are kept. A title
naming a place the day was never in is dropped rather than shown, and so is one claiming a
distance or a race ("the 10K") that nothing the model was shown mentions. A title card is
the wrong place for a plausible invention, and a number reads exactly as true as a real one.

A dropped title is asked for once more, with the claim it just made quoted back and the
rule stated as what a title *may* say rather than as another prohibition. That is usually
enough (the model can generally write a grounded title on the second try), and it costs
one extra call on the handful of days a year where it happens. Never a third.

If the second attempt is no better, the day falls back to the plainest true thing left:
`A day in <place>` where its pictures recorded one, or what the model said the day was
where that reads as a title ("Children's camp activities") rather than as a description of
it. A day where neither is available is left out of the catalogue rather than written down
with an empty title, because every reader of the file falls back to the description when
the title is empty, which is how "Six images captured between 07:32 and 16:06, tracing a
route from weathered apar" ended up on a card in place of a name.
