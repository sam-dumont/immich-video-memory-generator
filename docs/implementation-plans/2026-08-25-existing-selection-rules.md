# What the Current Selector Knows

> Companion to `2026-08-25-contact-sheet-editing-process.md`. That plan replaces the
> selector. This page is the list of what the selector already gets right, so the
> replacement is measured against it instead of rediscovering it by accident.

## Why this page exists

Three rules were lost in the first six tasks of the replacement, and every one of
them was found only because a real library happened to expose it — never by a test,
a review, or a reading of the code:

- `drop_live_photo_components` — one month admitted 729 candidates for 543
  photographs. Restored in `b8ecc10`.
- `not_shot_here` / `from_the_camera_roll` — **52% of one month and 54% of another**
  is material that never came off this library's camera. A period read from the
  unfiltered corpus produced a thesis about a festival the owner never attended.
- `SubjectCategory.SCREEN` / `_is_never_worth_padding` — the replacement's Cull
  vocabulary was entirely technical, so a photograph of a TV showing a stock
  wallpaper was inexpressible and survived untouched while Cull reported zero
  rejects. Restored as `photograph_of_a_screen` in `c67d098`.

None was a judgement call anyone made. They happened because the replacement builds
its pool, its groups and its vocabulary from scratch, and **a rule that lives in a
module the new path does not import is indistinguishable from a rule that does not
exist.**

`moment_grouping.moments_to_read` had already recorded this exact failure and its
fix — *"a wedding, a fresh tattoo and a grid comparing chihuahuas to muffins"* read
as remarkable days, solved by putting the filter at **the one door** into
grouping-for-reading. The replacement added a second door
(`group_by_time_and_place`, "without applying the legacy source filter") and the
guarantee was gone. **A one-door rule is only a rule while there is one door.**

## Status key

| | |
|---|---|
| **CARRIED** | the replacement already implements it |
| **RESTORED** | was lost, fixed, commit named |
| **AT RISK** | not implemented, and *not mentioned anywhere in the plan* |
| **SUPERSEDED** | deliberately replaced; what replaces it is named |

---

## A. Provenance and population — facts about the file, before any judgement

These decide what the corpus *is*. None of them is a quality opinion, so none of
them belongs to a pass. They run before Pass 0 or they do not run.

| Rule | Where | Status |
|---|---|---|
| A Live Photo's motion component is not a second visual | `live_photo_pipeline.drop_live_photo_components` | **RESTORED** `b8ecc10` |
| Material that never came off this camera is not source | `source_filter.not_shot_here` | **RESTORED** (this branch) |
| A star overrides provenance | inside `not_shot_here` | **CARRIED** — the star settles it, as it settles every hard gate |
| Untagged frames of a fetched burst come too | removed 2026-08-29 | **REMOVED** — exact person tags are the source boundary |
| Untagged Live Photos beside tagged ones come too | removed 2026-08-29 | **REMOVED** — exact person tags are the source boundary |
| An image with a still goes to the photo scorer, not the video one | `selection_quality.looks_like_a_photograph` | **AT RISK** — Task 8 |
| A photograph of a screen is a thing, not a moment | `subject_policy.SubjectCategory.SCREEN`, `clip_backfill._is_never_worth_padding` | **RESTORED** `c67d098` |

**Decision 2026-08-29 on the two burst/neighbour rules:** removed. A person
memory takes only assets carrying the requested person tag; temporal proximity
cannot widen that source boundary. Live Photo burst merging still runs later as
a rendering decision over tagged assets that actually entered the pool.

**Two of these were listed CARRIED and were not.** Place and named people both
sat in the CARRIED column while `grep` says the new path never imported either.
That is the same failure as the three at the top of this page, found the same
way -- by looking at what the model actually receives rather than at what a
column says. **A status on this page is a claim, and a claim about a rule is
worth exactly one grep.** Re-check the CARRIED rows, not just the AT RISK ones:
AT RISK is honest about being unbuilt, CARRIED is the one that can be wrong.

**On `looks_like_a_photograph`:** its docstring records the cost of getting it
wrong — a burst carrier sent to the video analyser "fails in milliseconds, is
marked attempted, and is never looked at again… it ships undescribed."

---

## B. Protection — what must never be dropped

| Rule | Where | Status |
|---|---|---|
| Favourites are exempt from the quality gate | `selection_quality.judge_offenders` | **CARRIED** — `_protect_favourites` |
| A period's last voice is kept unless it is *unusable* | `selection_quality.spare_last_voices` | **AT RISK** — zero mentions in the plan |
| A coverage clip is untouchable at every drop site | `clip_refiner._trim_non_favorites` | **AT RISK** |

**`spare_last_voices` is the most valuable rule on this page and the plan does not
mention it.** Its docstring is the argument:

> The judge removes offenders from the pool for good, so read as "this clip is bad"
> its verdict costs a month whose only clip scored low. Read as "we can do better
> than this clip" it costs nothing, because when there is nothing better the clip
> stays.
>
> Unusable is different from weak, and the distinction is what makes this safe: a
> shot of the ground or a pocket is not worth a month, and stays dropped however
> alone it is.
>
> **A correction, not an exemption.** Exempting has to guess in advance which clips
> carry a period, and both ways of guessing are wrong.

That "correction, not exemption" shape matters: it is applied *after* the judgement,
so the gate never has to be switched off. `_trim_non_favorites` records the same
lesson from the other side — it was "the last drop site that did not" treat a
coverage clip as untouchable, so the one clip standing for a whole month could be
cut for scoring low, "which is exactly why it was added in the first place."

**Where this lands in the replacement:** Fine Cut (Task 11) is the pass that removes
things last, so the correction belongs there. Structure (Task 9) is where a moment
can vanish entirely.

---

## C. Structure and coverage

| Rule | Where | Status |
|---|---|---|
| At least one clip per period, across the whole range | `clip_refiner._ensure_temporal_coverage` | **AT RISK** — zero mentions in the plan |
| Granularity adapts: daily ≤1 month, weekly ≤3 months, monthly ≤1 year, quarterly beyond | same | **AT RISK** |
| No moment gives more than a quarter of the cut | `clip_refiner._clips_per_moment` | **AT RISK** |
| An occasion already in the cut covers a candidate | `clip_backfill._is_same_occasion` | **PARTIAL** — Selects kills redundancy by construction; the *window* rule is not carried |

**On `_clips_per_moment`:** the quarter cap is what "keeps a deduplicated slot from
being refilled by its own duplicate." Its docstring also records a measured failure
of the obvious implementation — `ceil()` alone "handed every memory two per moment:
a real December shipped an eight-clip month with the same group photo twice."

The replacement's answer is that Selects picks one frame per moment, so redundancy
dies by construction. **That covers the duplicate case but not the proportion
case:** nothing stops Structure spending a whole memory on one very good afternoon.
Chronology is preserved, but the shape is not.

---

## D. Superseded by design — with the reasoning worth keeping

These the plan deletes deliberately (Task 14). Listed so the *judgement* inside them
is not thrown out with the mechanism.

| Rule | Replaced by | Reasoning to preserve |
|---|---|---|
| `apply_subject_quotas` — keep people, ration animals and objects | Cull evidence + Selects | *"a new car is a memory and a lawnmower is not, and the only thing separating them is whether the clip is actually any good"* |
| `quota_for` — quotas as a share of finished length | duration as a convergence bound | a ten-minute video should not get a sixty-second video's allowance |
| `arithmetic_funnel`, numeric rank | the five passes | numbers may order, never decide |
| `clip_backfill` | shorter strong cuts beat filler | but see `_is_never_worth_padding` below |
| `selection_review` | Fine Cut (Task 11) | one question the judge answers |

**Cull is two questions, not one.** Measured on a real month: *"remove the junk, remove
the failed pictures, protect the favourites."* The replacement implemented only the
second. A vocabulary of ways pixels can fail cannot reject a sharp, well-exposed
photograph of a bank contract, and the record lane was actively shielding it.

The junk half is decidable by looking, with no comparison and no score: **the photo
was taken as a note rather than as a memory.** A screen, a document, an object shot
to record what it is. Encoded as `photograph_of_a_screen`,
`paperwork_not_a_moment`, `reference_shot_not_a_moment` — and those three
**override a record mark**, because a mark argues about how a picture LOOKS and has
no standing over what it IS. A mark on something Cull removed goes with it.

What Cull must NOT do is choose between similar frames. A real month held ~170
near-duplicates in runs of 8 to 35; that is Selects' and Structure's work, and
letting Cull near it is how it becomes a taste pass.

**Junk is not the same as defective.** The replacement's Cull vocabulary described
only ways pixels can fail — obstruction, motion blur, exposure, corruption. A TV
wallpaper is sharp, exposed and uncorrupted, so every one of them is false of it and
the pass had no way to say what was wrong. The old selector was blunt about this:
*"there is no gap worth a photograph of a monitor."* The lesson generalises past
screens: **a closed vocabulary decides what a pass is able to notice**, so a gap in
it reads as silence rather than as an error.

**`_is_never_worth_padding` carries a rule that must survive its module:** an
*unlabelled* clip is kept, because "a third of a real pool has no analysis yet and
reading that silence as junk would empty the quiet months." Absence of evidence is
not evidence of junk. The replacement inherits this risk directly — anything the
model cannot read must fail toward keeping.

---

## E. Evidence signals the judge is entitled to

Not membership rules; inputs a pass may weigh. Losing them costs judgement quality
rather than correctness.

| Signal | Where | Status |
|---|---|---|
| Front camera / selfie, from the EXIF lens name | `selection_review._front_camera` | **AT RISK** |
| Subject class from Immich face tags + model label | `subject_policy.classify_subject` | **CARRIED** as evidence-only |
| Who Immich recognised, by name | `Asset.people` | **RESTORED** `278c073b` — `subject-evidence` collapsed everyone present to one enum value, so 39 named face tags on one month reached the model as the word "people" |
| Place as city/state/country, not a caption | `selection_review._place_for_llm` | **RESTORED** `278c073b` — was listed CARRIED and was not: that helper had no caller on the #764 path, and the episode scan sent resolution and exposure but no place |
| Which moment a clip belongs to | `selection_review._clips_block` | **CARRIED** |

---

## The standing check

Before any slice merges into the feature trunk:

1. **Does this slice build a pool, a group, or a corpus from scratch?** If so, name
   every rule the legacy path applies at that stage and say, per rule, whether it is
   carried, superseded, or deliberately dropped.
2. **Did it add a second door to a one-door rule?** Grep the legacy caller list of
   any helper it replaces. A rule with four callers and none of them yours is a rule
   you have lost.
3. **Does the gate corpus look like the owner's life?** A judgement measured on the
   wrong corpus tells you nothing. Half of one month was other people's material,
   and four rounds of prompt tuning were spent on what that corpus caused.
