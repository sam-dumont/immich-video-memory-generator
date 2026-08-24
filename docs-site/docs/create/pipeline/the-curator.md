---
sidebar_position: 0.5
title: The Curator
---

# The Curator

Every photo app has an automatic memories feature, and they all work the same
way: rank the pixels — sharpness, faces, smiles — pick the winners, add music.
The result is a highlight reel. Technically fine, emotionally random, and
after the third one you stop watching them.

This pipeline is built on a different premise: **selection is an editing job,
not a ranking job.** Before anything is scored, a vision model looks at your
material and describes what is actually happening in it. Every decision after
that — what makes the cut, what counts as a duplicate, what the title is
allowed to claim — is made against those descriptions, not against pixel
statistics. This page explains how that works and the rules it follows. It is
the core of the product; everything else is delivery.

## It looks before it picks

Candidates are grouped into **moments** — photos and clips taken together —
and the model looks at one representative per moment rather than a fixed
handful of metadata winners. Before this worked properly, a month with 295
photos would get exactly 3 of them in front of the model, chosen by metadata,
so the model could only confirm what the metadata already decided. Now the
looking is sized by how many moments the month actually contains.

Two properties of the looking:

- **A favourite fronts its moment.** If you flagged a photo in Immich, it is
  the one the model sees for that moment. Your flag is the strongest signal in
  the library — the pipeline treats it as your judgment already made, not as
  one weight among many.
- **Every look is paid once, ever.** Descriptions are cached per asset. The
  first run over a library is the slow one; every later memory reuses what is
  already known. See [LLM Content Analysis](llm-content-analysis) for the
  model setup.

## It judges what things show, not how they look

Descriptions do the discriminating work that scores can't:

- **Duplicates are judged on content.** Two clips of the same cake, eight
  minutes apart, are one moment — keep the better one. Two toasts at the same
  party are two moments. Perceptual hashing can't tell these apart; a sentence
  about each can.
- **The final review sees the whole cut.** After selection, the model reads
  the full set and removes what weakens it, with a stated reason per drop.
  These are real reasons from real runs — one clip went because it
  *"records an object rather than a moment."* That is an editor's sentence,
  and it is the level the review works at.
- **Nothing fails silently.** If the reviewer is unreachable, the run says so
  loudly instead of quietly shipping an unreviewed cut, and every run prints
  how much of the candidate pool was actually looked at — so "the model chose"
  and "the model never saw it" are never confused.

## The rules

These are constraints the pipeline obeys, not preferences it weighs.

**Always chronological.** A memory plays in the order things happened. No
model may resequence a cut for drama: chronology is the one thing you can
check against your own recollection, and a reordered memory is subtly a lie
about the day. The editorial decisions are what to include and how long to
dwell — never when.

**Favourites win their moment.** Where you have flagged a photo, the pipeline
does not overrule you with a score.

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

Cuts land on musical beats. Speech is never cut mid-sentence — voice-activity
detection moved mid-speech cuts from 34% of clips to 9%, measured on real
libraries. HDR footage stays HDR end to end. A monthly memory opens with a
month title, a yearly gets month dividers, a single day gets one intro card —
because those are different shapes of story.

## Where this is heading

The current work — visible in the open issues — is event-level understanding:
the model reads a whole moment as one contact sheet, names the event, and
ranks photos by how well they show *that* rather than by generic priors like
"has a face". The goal is stated in one line: curation based on moment →
entirety. Follow along in
[#707](https://github.com/sam-dumont/immich-video-memory-generator/issues/707).

## The mechanics

The arithmetic that serves these judgments — budgets, weights, and caps — is
documented in [Clip Selection & Scoring](clip-selection-scoring), and the
runtime cost of every stage in the
[Pipeline Overview](pipeline-overview).
