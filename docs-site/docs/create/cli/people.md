---
sidebar_position: 7
title: people
---

# people

Builds relationship context from Immich metadata and writes it to a file you can review.
Scans preserve confirmed answers when the existing file is readable; see the file warning below.

```bash
immich-memories people scan     # build or refresh the file
immich-memories people show     # read it back
```

The scan uses names, counts, birth dates, active months and co-appearance. It does not download
photos or call a model. Its tiers and relationships are inferences, so check them before relying
on them as context.

## What the graph reads

Tiers use the number of active months, the span of the record and how often the person appears.
A dense burst can indicate an event guest; repeated appearances over years suggest continuity.
Neither proves a real relationship.

| tier | shape |
|---|---|
| `inner` | at least 24 active months across 3 years, present in at least 35% of the months between |
| `recurring` | a dozen months or more, but failing one of the `inner` conditions |
| `episodic` | everything that is not one of the other three: no span condition of its own |
| `event` | four active months or fewer at twenty-plus pictures each: a burst |

On top of the tiers the scan looks for four things.

**Onset: when somebody entered your library.** The first month with three more active
months inside the following year. One picture in 2011 and a real presence from 2018 makes
the onset 2018, not 2011. Anything stamped before 2003 is dropped as broken EXIF rather
than treated as an appearance.

**Tight dyads.** Each person appears in at least a quarter of the other's pictures.
That can fit partners, a parent and child, or other close relationships; it is not labelled
as a couple.

**Twins.** Two people with the same family name and the same birth date. Worth flagging
because face recognition merges identical faces: one twin's record ends up holding nearly
every picture and the other a handful of hand-tagged ones. Neither count means anything
alone, so the graph reads the pair as one unit and marks both `counts_reliable: false`.

**Duplicates.** Repeated names are flagged for review in Immich. They may be split face
records or different people with the same name; check before merging.

A birth date changes the reading. Someone born after your library started cannot have a
span longer than their age, so span ≈ age means they have been here since day one; if
they also appear in most months since, they are inner circle regardless of how short the
span is. This lets a young child qualify without requiring a long history.

## Who holds the camera

The library owner may be missing from pictures they took. For pairs involving the owner,
the heuristic uses overlapping month curves rather than shared-frame counts.

The owner is identified three ways, in descending order of certainty, and the file records
which one was used:

1. `--owner "Their Name"` (or `IMMICH_MEMORIES_OWNER`) writes `identified: told`
2. the name on your Immich account, matched against the roster, writes `identified: account`
3. failing both, the person with the longest span and the most pictures, written `identified: inferred`

If it says `inferred`, check it. The photographer correction hangs off getting this right.

## The file

`~/.immich-memories/people.yaml`, written readable only by you, and gitignored the same
way the special-days catalogue is: it holds the names of everyone in your library.

```yaml
version: 1
generated: '2026-08-25T09:12:03'
owner:
  person_id: 5f2c…
  name: Alex Example
  identified: account
people:
  - ids: [5f2c…]
    name: Alex Example
    birth_date: '1988-04-02'
    inferred:
      tier: inner
      counts_reliable: true
      evidence:
        count: 4210
        active_months: 180
        first_month: '2009-06'
        last_month: '2026-08'
        span_years: 17.2
        onset: '2009-06'
        concentration: 23.4
        continuity: 0.87
      links:
        - kind: tight-dyad
          with: 91ab…
          confidence: 0.51
          via: co-occurrence
    confirmed:
      role: null
      links: []
      notes: null
```

## Confirmed beats inferred

Everything under `inferred:` is the scan's reading and gets recomputed every time you run
it. Everything under `confirmed:` is yours.

For a valid existing file:

- a refresh **never** writes into a `confirmed:` field, whatever it now thinks;
- a person carrying anything confirmed is **never** dropped by a refresh, even if they
  fall off the roster: a merged person record or one unreachable API call is not a reason
  to delete an answer you gave;
- where the two disagree, consumers are to prefer `confirmed:`.

Fill `confirmed:` by editing the file, or from the settings page below. Both use the same schema.

Back up `people.yaml` before hand-editing it. An unreadable or malformed file is currently treated
as empty; a subsequent scan or save can replace it and lose confirmations. If the command logs
"starting fresh", repair or restore the file before saving again.

## The editor

The same file is a page in the web UI, **Settings → People**: one card per person with their
face crop, tier and evidence, a role select, notes, and the links the scan found with a ✓ / ✗ on
each. Everything the page writes lands in `people.yaml`; everything you edit by hand shows up
there. See [Settings](../web-ui/settings.mdx#people-confirming-whos-who).

## Options

`people scan`

| flag | default | what it does |
|---|---|---|
| `--min-assets` | `25` | pictures a named person needs before the graph has an opinion |
| `--owner` | `$IMMICH_MEMORIES_OWNER` | the name of the person whose library this is |
| `--out` | `~/.immich-memories/people.yaml` | where to write |

`people show`

| flag | default | what it does |
|---|---|---|
| `--file` | `~/.immich-memories/people.yaml` | the file to read |
| `--tier` | all | show only `inner`, `recurring`, `episodic` or `event` |

`people scan` prints tier counts and the file path, not the roster: a terminal may be a log.
`people show` prints it because you asked. Unnamed faces are skipped; naming them is work that
belongs in Immich.

## What uses it

The model editor loads `people.yaml` from its default path and includes a `people` block in
its text input: id, name, relationship, where that relationship came from, birth date,
first appearance, onset and tier. Confirmed relationships and labelled derived relationships give the model context. A scan
written with `--out` elsewhere is not picked up automatically by the editor.

What does not read it yet: selection weights, tie-breaks between two equally good moments,
person-rotation fairness and the automation's person priors.
