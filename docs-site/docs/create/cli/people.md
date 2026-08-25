---
sidebar_position: 7
title: people
---

# people

Works out who is in your library from the numbers Immich already holds, writes it to a
file you can edit, and never overwrites an answer you gave it.

```bash
immich-memories people scan     # build or refresh the file
immich-memories people show     # read it back
```

Nothing here looks at a pixel and nothing here asks you a question. Counts, names, birth
dates, the months each person appears in, and how often two people appear together are
enough — which is the point: curation you already did inside Immich has to pay off
somewhere.

## What the graph reads

**Volume is a burst, continuity is a relationship.** That one rule does most of the work.
A person with 160 pictures spread over four active months across scattered years was at
four events with you. A person with the same 160 pictures spread over forty months is
part of your life. Pictures ÷ active months is the discriminator that volume alone is
not, and the tiers fall out of it:

| tier | shape |
|---|---|
| `inner` | dozens of active months, years of span, present in most months between |
| `recurring` | a dozen months or more, but not most of them |
| `episodic` | a handful of months across a long stretch |
| `event` | four active months or fewer at twenty-plus pictures each — a burst |

On top of the tiers the scan looks for four things.

**Onset — when somebody entered your library.** The first month with three more active
months inside the following year. One picture in 2011 and a real presence from 2018 makes
the onset 2018, not 2011. Anything stamped before 2003 is dropped as broken EXIF rather
than treated as an appearance.

**Tight dyads.** Two people who are each a quarter or more of *each other's* pictures.
Mutual is the whole point: everybody in a household appears in the busiest person's
frames, so a one-sided overlap says only that the other person is busy. It is called a
tight dyad and not a couple on purpose — a parent and a small child make the same shape,
and telling those apart needs cues this pass does not have.

**Twins.** Two people with the same family name and the same birth date. Worth flagging
because face recognition merges identical faces: one twin's record ends up holding nearly
every picture and the other a handful of hand-tagged ones. Neither count means anything
alone, so the graph reads the pair as one unit and marks both `counts_reliable: false`.

**Duplicates.** One name on two person records is a split face cluster in Immich. The
graph cannot fix it — it flags it so you can merge them where they live.

A birth date changes the reading. Someone born after your library started cannot have a
span longer than their age, so span ≈ age means they have been here since day one; if
they also appear in most months since, they are inner circle regardless of how short the
span is. That is a two-year-old, not a friend you met two years ago.

## Who holds the camera

Co-occurrence undercounts every pair containing you, because you are behind the camera.
Measured on a real library: in the quarter the owner met their partner, the partner
appears twenty-five times and they share **zero** frames. The first shared frame comes
months later, when somebody else takes the picture.

So the owner's pairs are never asked about at all — no query is spent on them — and the
owner's closest person is found from month curves instead: somebody present at the
owner's own scale whose active months track the owner's from the day they arrive.

The owner is identified three ways, in descending order of certainty, and the file records
which one was used:

1. `--owner "Their Name"` (or `IMMICH_MEMORIES_OWNER`) — `identified: told`
2. the name on your Immich account, matched against the roster — `identified: account`
3. failing both, the person with the longest span and the most pictures — `identified: inferred`

If it says `inferred`, check it. The photographer correction hangs off getting this right.

## The file

`~/.immich-memories/people.yaml`, written readable only by you, and gitignored the same
way the special-days catalogue is — it holds the names of everyone in your library.

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

The contract, which the builder is not allowed to break:

- a refresh **never** writes into a `confirmed:` field, whatever it now thinks;
- a person carrying anything confirmed is **never** dropped by a refresh, even if they
  fall off the roster — a merged person record or one unreachable API call is not a reason
  to delete an answer you gave;
- where the two disagree, consumers are to prefer `confirmed:`.

Fill `confirmed:` by editing the file, or from the settings page below. Both write the
same schema, through the same writer.

## The editor — Settings → People

The web UI has the same file as a page, at **Settings → People** (`/settings/people`).
It is the file, not a second copy of it: everything the page writes lands in
`people.yaml` immediately, and everything you have edited by hand shows up there.

Each person is one card: their Immich face crop, their name, the tier the scan put them
in with the evidence behind it in one line, and the edges the scan found.

**What you can confirm.**

| control | writes | what it means |
|---|---|---|
| Role | `confirmed.role` | pick from partner, child, parent, sibling, family, friend, acquaintance — or type your own. The list is only what inference can suggest; a role only you can name is what the free text is for |
| ✓ / ✗ on a link | `confirmed.links[]` | yes they are, or no they are not. Pressing the answer you already gave takes it back — undecided is a real state, and it writes nothing |
| Notes | `confirmed.notes` | anything you want to remember about this person |

Nothing is saved behind a button: each control writes as you change it, and a rescan then
copies all of it through untouched.

**Birth dates are read-only here.** They are mirrored from Immich and must never diverge
from it, so the card shows the date with an *edit in Immich* link next to it rather than
a field. The same link opens any person's record in your Immich install.

**Curation, at the top of the page.** Two things the scan can spot but only Immich can
fix, so the page names them and points you there:

- **Twins** — same family name, same birth date. Face recognition merges identical faces,
  so one record ends up holding nearly all the pictures and the other almost none
  (measured on a real library: 576 against 20). Neither count means anything alone, and
  both cards carry a *counts unreliable* badge. Merge them in Immich or keep them apart —
  it is your call, and the graph reads the pair as one unit either way.
- **Same-name duplicates** — one name on two person records is a split face cluster.
  Merge those records in Immich.

Both are guesses, and both can be wrong: two people really can share a surname and a
birthday, or a name. Press ✗ on the link in either person's card and the flag stops
appearing — a prompt you have already answered is nagging, not curation.

**Rescan the library** runs exactly what `people scan` runs, in the background, and
redraws the page when it finishes. Your confirmations survive it — that is the whole
contract above.

Unnamed faces do not appear here at all, however often they show up: the graph skips
them, because naming a face is work that belongs in Immich.

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

`people scan` prints tier counts and the file path, not the roster — a real inner circle
is your household by name, and a scan should not read it out into a terminal that might be
a log or a shared session. `people show` prints it because you asked.

Unnamed faces are skipped entirely. The graph has nothing useful to say about a face
nobody has claimed, and naming them is work that belongs in Immich.

## What uses it

Nothing yet. The graph ships before its consumers on purpose — selection weights,
tie-breaks between two equally good moments, person-rotation fairness and the automation's
person priors are all next, and each of them needs a stable file and a stable contract to
read from first.
