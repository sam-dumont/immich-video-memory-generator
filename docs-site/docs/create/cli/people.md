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
enough, which is the point: curation you already did inside Immich has to pay off
somewhere.

## What the graph reads

**Volume is a burst, continuity is a relationship.** That one rule does most of the work.
A person with 160 pictures spread over four active months across scattered years was at
four events with you. A person with the same 160 pictures spread over forty months is
part of your life. Pictures ÷ active months is the discriminator that volume alone is
not, and it is what picks out `event`. The other three come from how many months, how
long a span, and how continuously present:

| tier | shape |
|---|---|
| `inner` | dozens of active months, years of span, and present in at least a third of the months between |
| `recurring` | a dozen months or more, but failing one of the `inner` conditions |
| `episodic` | everything that is not one of the other three: no span condition of its own |
| `event` | four active months or fewer at twenty-plus pictures each: a burst |

On top of the tiers the scan looks for four things.

**Onset: when somebody entered your library.** The first month with three more active
months inside the following year. One picture in 2011 and a real presence from 2018 makes
the onset 2018, not 2011. Anything stamped before 2003 is dropped as broken EXIF rather
than treated as an appearance.

**Tight dyads.** Two people who are each a quarter or more of *each other's* pictures.
Mutual is the whole point: everybody in a household appears in the busiest person's
frames, so a one-sided overlap says only that the other person is busy. It is called a
tight dyad and not a couple on purpose: a parent and a small child make the same shape,
and telling those apart needs cues this pass does not have.

**Twins.** Two people with the same family name and the same birth date. Worth flagging
because face recognition merges identical faces: one twin's record ends up holding nearly
every picture and the other a handful of hand-tagged ones. Neither count means anything
alone, so the graph reads the pair as one unit and marks both `counts_reliable: false`.

**Duplicates.** One name on two person records is a split face cluster in Immich. The
graph cannot fix it: it flags it so you can merge them where they live.

A birth date changes the reading. Someone born after your library started cannot have a
span longer than their age, so span ≈ age means they have been here since day one; if
they also appear in most months since, they are inner circle regardless of how short the
span is. That is a two-year-old, not a friend you met two years ago.

## Who holds the camera

Co-occurrence undercounts every pair containing you, because you are behind the camera.
Measured on a real library: in the quarter the owner met their partner, the partner
appears twenty-five times and they share **zero** frames. The first shared frame comes
months later, when somebody else takes the picture.

So co-appearance is not how the owner's closest person is found. The pairs are still
queried like everyone else's; what changes is that the dyad heuristic ignores the answer
and reads month curves instead: somebody present at the owner's own scale whose active
months track the owner's from the day they arrive.

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

The contract, which the builder is not allowed to break:

- a refresh **never** writes into a `confirmed:` field, whatever it now thinks;
- a person carrying anything confirmed is **never** dropped by a refresh, even if they
  fall off the roster: a merged person record or one unreachable API call is not a reason
  to delete an answer you gave;
- where the two disagree, consumers are to prefer `confirmed:`.

Fill `confirmed:` by editing the file, or from the settings page below. Both write the
same schema, through the same writer.

## The editor

The same file is a page in the web UI, **People** in the sidebar: one card per person with their
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

The editor. Every cut loads `people.yaml` and renders a `people` block onto the wall the
text model reads: id, name, relationship, where that relationship came from, birth date,
first appearance, onset and tier. So who somebody is to you is part of what the model
weighs, not just a label in a settings page.

What does not read it yet: selection weights, tie-breaks between two equally good moments,
person-rotation fairness and the automation's person priors.
