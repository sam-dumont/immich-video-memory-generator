---
sidebar_position: 6
title: discover-days
---

# discover-days

Finds the days in your library that something happened on, and writes them down so a
memory can arrive years later without you asking for it.

Every other memory type answers a question you posed: this month, that trip, that person.
This one is for the memory nobody requested — ten years to the day since the wedding,
five since the race. That only works if the days were found in advance, so this is a
command you run occasionally, not one that runs per video.

## What makes a day stand out

Volume does not. In a real library the busiest single day is 166 photos of a work shoot
taken inside one hour, and the second busiest is 413 of one street performer. Neither is
an occasion.

What separates them is how long the day stayed alive:

| day | photos | active hours | occasion |
|---|---|---|---|
| a birth | 289 | 18 | yes |
| a wedding party | 48 | 12 | yes |
| a track day | 133 | 7 | yes |
| an apartment viewing | 258 | 5 | no |
| a street performer | 413 | 3 | no |
| a work shoot | 166 | 1 | no |

No overlap — but the rule is loose on its own, since about a fifth of days in that library
clear six hours. So it is a filter, not a verdict: it keeps the model off the other 78% of
days, which is what makes asking about the rest affordable at all. What passes goes to the
model with a sample of the day's pictures, and it is asked the question a person would ask.

A day also ends when the photographs stop for five hours, not at midnight. A wedding that
runs past one, or a birth that starts with contractions at ten in the evening, is one
occasion; the calendar disagrees.

Two things are skipped: days inside a detected trip, because a trip memory already tells
that story end to end, and holidays, which have their own memory type.

A holiday is only skipped when the day's pictures agree that it was one — that is, when
they were taken around home, where the holiday is actually kept. A day that merely lands
on the same date and was spent 67 km away at a race circuit is not that holiday, and it
goes to the model like any other candidate. A day that recorded no coordinates at all is
skipped on the date alone, as it always was.

Both filters read the thresholds under `trips:` — your homebase, how far from it counts as
away, and how trips are grouped — so this command and the rest of the app agree on what
"away" means.

## Fast eyes, then a considered answer

With `llm.thinking: true` the question runs in two steps: one fast call that
looks at the sampled thumbnails and writes a line per picture, then a
text-only call that reasons over those lines together with the times, places
and recognised names. Without `thinking`, it stays the single vision call it
has always been, so nothing changes on a server that cannot reason.

Splitting it is not a preference. Measured across 14 candidate days, one
vision call answered "special" to all fourteen, and a single call that both
looked and reasoned truncated 6 of them past the point of parsing, because the
reasoning runs into the answer. Two calls was the only shape that told an
occasion from an ordinary Tuesday — and the one it called ordinary turned out
to be right.

It also invents less. Where the fast answer named a specific event that had
not happened, the two-step version gave the same day a title that described
what was in the pictures instead.

The cost is roughly 40 seconds and about 3,000 completion tokens for each day
it asks about. The scan asks about a handful of days per year.

The per-picture lines the judgement read would be the record to check first when a
day you expected comes back ordinary. They are written to the log at `DEBUG`, and
the CLI has no flag or environment variable that raises the log level that far, so
today there is no way to see them without editing `configure_logging()`.

## Running it

```bash
immich-memories discover-days
```

It walks year by year, prints what it finds, and writes a catalogue to
`special-days.json`.

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
| `--out` | `special-days.json` | where to write the catalogue |
| `--rescan` | off | start over, ignoring and replacing the existing catalogue |

The scan takes hours across twenty years, so it resumes by default: years already in the
catalogue are not scanned again, and a run that finds nothing will not replace a catalogue
that has something in it. `--rescan` is how you say you meant to start over.

Each year costs `--per-year` calls to your model, plus one metadata query per month.
Raising `--per-year` finds more and costs proportionally more.

## Checking what is due

```bash
immich-memories days-due
```

Prints the discovered days whose anniversary falls within three days of today, roundest
first — ten years reads louder than nine, which is the whole appeal of arriving
unannounced.

```
10 years ago  2015-06-12  A long evening out
```

`--on YYYY-MM-DD` checks a different date, and `--catalogue PATH` reads a different file.
Anniversaries either side of New Year are found: a day at the end of December is due in
early January.

## What you need

An LLM configured under `llm:` — see [Configuration](/docs/deploy/configuration/config-file).
A vision model is worth having: with pictures the model sees the day, and without them it
reasons from times, places and recognised names alone. That is the difference between
"Driving through somewhere" and knowing what was being driven.

Titles are checked against what the day actually recorded before they are kept. A title
naming a place the day was never in is dropped rather than shown — a title card is the
wrong place for a plausible invention.
