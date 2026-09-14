---
sidebar_position: 0.5
title: The Curator
---

# The Curator

The production selector reads the period as a story. It prepares descriptions,
people and place context, and picture facts for the whole source. It then decides
which stories matter and which distinct moments show them, before allocating the
film's duration. This is the route used by the UI, CLI and scheduled runs.

The configured preparation tier controls which facts are required. A model reader
interprets them; [rules mode](./rules-mode.md) uses deterministic decisions with less
context. Neither can guarantee that a cut matches your judgment of the period.

## It looks before it picks

Required image facts cover the whole eligible source period. Capture groups help
organize it, then the model distinguishes what happened: two toasts at a party
can be separate moments, while several pictures of the same toast are variants.
The story reading comes before the duration allocation. A short and a long
memory can therefore share an understanding of the period while showing different
amounts of it.

Two properties of the looking:

- **Favourites carry your judgment.** They help establish a story's importance
  and lead choices within a moment, subject to source and audience eligibility.
- **Matching facts are reused.** Descriptions and model decisions are cached
  by their producer and input. New or changed evidence can require fresh work.
  See [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md).

## Moments and picture choices

The model reader uses descriptions and picture evidence to distinguish moments:

- **Duplicates use several checks.** Similar previews nominate pictures for comparison;
  model comparisons can distinguish another view of the same moment from a separate
  event. These judgments can be wrong. See [Twins and Near-Duplicates](./duplicate-detection.md).
- **Picture choices follow moments.** The planner chooses a representation
  of each selected moment. Extra variants do not count as extra events.
- **Missing evidence is reported.** Required source and annotation coverage
  is checked before selection, and an incomplete run is not reported as complete.

## The rules

The editor applies these constraints:

**Chronological order.** The final cut follows capture time. Editing changes
what appears and how long it stays, not the order of events.

**Favourites lead their moment.** A favourite wins against an ordinary variant,
subject to source and audience eligibility. This does not promise that every
favourite appears in a short cut.

**The audience is FAMILY.** A shirtless baby is ordinary family content and can be
included. Eight findings exclude a carrier when identified, at any audience: breastfeeding or expressing milk, bathing, toileting or
changing, intimate hygiene, graphic medical procedures, identifying records, sexual
content, and adult changing. The model is told that newborn care is ordinary family
content (that keeps it from filing a bath as something worse), and the code holds
all eight out of the cut when identified, regardless of what the model was told. This
is a detection policy, not a guarantee that every sensitive picture is recognized.

**A day's title claims only what the evidence shows.** The title a special day
carries is checked against the evidence lines it was written from, and a claim
those lines do not support is dropped rather than printed. (Trip titles are a
different path: they are written from dates and place names, with no such
check.)

**Special days need a catalogue entry.** A day without a title or description
cannot render as a special-day memory. An empty catalogue produces instructions
for building one.

The [special days scan](../cli/discover-days.md) examines eligible days without
a search phrase. It records occasions that automation can propose on a later
anniversary; see [Special Days](../memory-types/special-days.mdx).

## Rendering

Selected videos and Live Photos use available source motion windows.
[HDR output](./hdr.md) depends on the chosen codec and HDR mode. A monthly memory opens with a
month title, a yearly can get month dividers, and a single day gets one intro card
when title screens are enabled.

## How a longer film gets more depth

More time lets an important story show more distinct moments: arrival, the main
activity, people together and how the day ended. It does not make every extra
frame a new event. Picture choice follows the moment inventory, and the final
material stays in chronological order.

## The mechanics

The shipped design (the source model, the annotation store and its banks, the
six stages, the two readings, the structure and story planners, carriers and
durable attempts) is written up in
[Story-first selection: the shipped design](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/docs/designs/2026-09-10-story-first-selection.md)
in the repository. The runtime cost of every stage is in the
[Pipeline Overview](./pipeline-overview.md).
