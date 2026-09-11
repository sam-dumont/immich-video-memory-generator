---
sidebar_position: 0.5
title: The Curator
---

# The Curator

Every photo app has an automatic memories feature, and they all work the same
way: rank the pixels — sharpness, faces, smiles — pick the winners, add music.
The result is a highlight reel. Technically fine, emotionally random, and
after the third one you stop watching them.

The production selector reads the period as a story. It prepares descriptions,
people and place context, and picture facts for the whole source. It then decides
which stories matter and which distinct moments show them, before allocating the
film's duration. This is the route used by the UI, CLI and scheduled runs.

## It looks before it picks

Descriptions and image facts cover the whole source period. Capture groups help
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

## It judges what things show, not how they look

Descriptions do the discriminating work that scores can't:

- **Duplicates are judged on content.** Two clips of the same cake, eight
  minutes apart, are one moment — keep the better one. Two toasts at the same
  party are two moments. Perceptual hashing can't tell these apart; a sentence
  about each can.
- **Picture choices follow moments.** The planner chooses a representation
  of each selected moment. Extra variants do not count as extra events.
- **Missing evidence is reported.** Required source and annotation coverage
  is checked before selection, and an incomplete run is not reported as complete.

## The rules

These are constraints the pipeline obeys, not preferences it weighs.

**Always chronological.** A memory plays in the order things happened. No
model may resequence a cut for drama: chronology is the one thing you can
check against your own recollection, and a reordered memory is subtly a lie
about the day. The editorial decisions are what to include and how long to
dwell — never when.

**Favourites win their moment.** Where you have flagged a photo, the pipeline
does not overrule you with a score.

**The audience is FAMILY.** A shirtless baby is ordinary family content and can be
included. Eight findings are not, at any audience, and a carrier that draws one is
replaced rather than shown: breastfeeding or expressing milk, bathing, toileting or
changing, intimate hygiene, graphic medical procedures, identifying records, sexual
content, and adult changing. The model is told that newborn care is ordinary family
content — that keeps it from filing a bath as something worse — and the code still
holds the last four *and* the first four out of the cut.

**Titles claim only what the evidence shows.** A title is generated from what
the model actually saw and is not allowed to invent specifics. If the material
can't support a claim, the title doesn't make it.

**Refuse over fake.** A day the model could not name does not get a generic
"Memories of June 12th" card — it doesn't render. An empty special-days
catalogue produces instructions for building one, not an invented occasion.
When the honest option and the impressive option differ, the pipeline takes
the honest one.

**Emergent, not queried.** Nothing searches your library for "beach" or "dog".
The [special days catalogue](../cli/discover-days) is built by looking at what
your days actually contain and asking whether anything happened — which is how
it finds the day that mattered with 30 photos, not just the day with 300. A day
it found comes back years later as a
[Special Day memory](../memory-types/special-days.mdx) nobody asked for.

## The craft you don't see

Selected videos and Live Photos use available source motion windows. HDR
footage stays HDR end to end. A monthly memory opens with a
month title, a yearly gets month dividers, a single day gets one intro card —
because those are different shapes of story.

## How a longer film gets more depth

More time lets an important story show more distinct moments: arrival, the main
activity, people together and how the day ended. It does not make every extra
frame a new event. Picture choice follows the moment inventory, and the final
material stays in chronological order.

## The mechanics

The shipped design — the source model, the annotation store and its banks, the
six stages, the two readings, the structure and story planners, carriers and
durable attempts — is written up in
[Story-first selection: the shipped design](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/docs/designs/2026-09-10-story-first-selection.md)
in the repository. The runtime cost of every stage is in the
[Pipeline Overview](pipeline-overview).
