---
title: Glossary
---

# Glossary

Reader: power user. Every word the editor's records, logs and these pages use, in the order a cut
meets them. Where the code and this page disagree, the code wins.

```mermaid
flowchart LR
  picture --> moment --> episode --> story --> shot["shot (carrier)"] --> film
```

## Units of a library

| Word | Meaning | Where |
|---|---|---|
| **Moment** | pictures within 10 minutes and 2 km of each other; one moment is what one shot shows | `moment_grouping.py` |
| **Capture run** | captures each within five minutes of the one before; it spaces shots and is the unit of an exposure chain | `MIN_GAP_IN_CAPTURE_GROUP_SECONDS` |
| **Episode** | the block a moment sits in (an afternoon at the beach, a party), cut at a 90-minute gap or 2 km | `selection_source_groups.py` |
| **Happening** | a group of moments close in time and place; the unit the worthiness reading judges | `RuleStructureReader.worthiness` |
| **Story** | days grouped into one thing that happened: a week at home, a stretch away, a trip | `editorial_rule_reader.py`, `editorial_story_grouping.py` |
| **Reach** | the pictures a film can select, plus their Live Photo siblings and capture runs; only these get prepared | `editorial_film_reach.py` |

## Preparation

| Word | Meaning | Where |
|---|---|---|
| **Producer** | anything that writes a fact about a picture: heads, detectors, caption server, pixel and motion readers | `editorial_preparation*.py` |
| **Heads** | eight small classifiers over one pinned DINOv2 encoder: location, people, children, activity, venue, frame_kind, screen, uncovered_person | `editorial_preparation_heads.py` |
| **Detectors** | `nsfw_marqo` (exposure, read on up to eight frames of a video) and `doc_docling` (documents) | `editorial_preparation_detectors.py` |
| **Caption** | one description per picture and one motion line per video, written at ingest by SmolVLM2 500M on the `full` tier | `editorial_description_contract.py` |
| **Tier** | `no_captions` (heads and detectors, the default with no model), `full` (plus captions), `metadata_only` (no heads) | `advanced.editorial.preparation.tier` |
| **Scene print** | the pooled DINOv2 vector of a preview; two prints at a cosine of 0.65 or more are the same scene | `editorial_scene_prints.py` |
| **Residual** | the motion left in a clip once the camera's own movement is removed; 1.5 or more plays as motion | `RESIDUAL_MIN`, `editorial_motion_facts.py` |
| **Bank** | an answer stored under its exact inputs and producer version, so the next run asks nothing; no row means nobody asked | `annotations.sqlite`, `structure-banks/` |
| **Pictures are read once** | a model looks at a picture only at ingest; no film-time step sends a picture to any model, on any tier | `tests/test_editorial_demanded_previews.py` |

## Building the cut

| Word | Meaning | Where |
|---|---|---|
| **Reader** | who plans the film: `rules` (no model), `model`, or `auto` (rules when `llm.model` is blank) | `advanced.editorial.reader` |
| **Draft** | the film the no-model reader cuts from facts; with no model, it is the film | `editorial_rule_reader.py` |
| **Worthiness** | remarkable, maybe or background, read per happening from facts | `RuleStructureReader.worthiness` |
| **Weight** | a story's size: `dominant`, `major`, `minor`, `glimpse`, `none`; `none` gets no shot | `editorial_story_slots.weight_caps` |
| **Big story** | dense (twice the median day) and mostly close family (30 %); weighs `major` | `_big_stories` |
| **Carrier** | the picture admitted to carry one moment of a funded story; a shot before it is rendered | `editorial_story_carriers.py` |
| **Carrier rule** | a picture kept as evidence and never a shot: a document, a screen, a screenshot, a face close-up | `excluded_carrier_sources` |
| **Standing** | whether a picture stands on its own, scored 0 to 2 from its facts, never asked of a model | `editorial_standing_facts.py`, `StandingGate` |
| **Look-alike** | a story's next shot must not repeat one it holds (hash within 10 bits) | `editorial_story_lookalike.py` |
| **Depth** | a story with slots left spends them inside moments it already shows, up to 3 frames each | `editorial_story_depth.py` |
| **Family seat** | one shot for a close family member the cut left out | `editorial_family_seat.py` |
| **Close family** | partner or spouse, child, parent, as confirmed in `people.yaml`; in a person film, that person's too | `people/relationships.py` |
| **Owner-required** | a picture you ticked; added after the draft, kept through the trim and the duplicate review | `editorial_owner_required.py` |

## The gate and the checks

| Word | Meaning | Where |
|---|---|---|
| **Verdict** | `share`, `family_only`, `just_us` or `do_not_show`; the strictest wins | `editorial_shareability.py` |
| **Sharing level** | who a film is for: just us (plays up to `just_us`), family (up to `family_only`, the default), shareable (`share` only) | `allowed`, `defaults.sharing` |
| **Detector hold** | an exposure flag on a still, a video frame or a Live clip; `family_only`, never lifted by a reading, only by you | `floors_under` |
| **Your decision** | per picture: hold cleared for a level, or never use; kept in the annotation store, read by every tier | `store/owner_decisions.py` |
| **Exposure chain** | a capture run at least half flagged, with three or more flagged captures, held whole | `editorial_exposure_chains.py` |
| **Laya** | an optional local model answering the audience check's activity question from the caption | `editorial_laya_reader.py` |
| **Review list** | shots with an exposure probability between 0.2 and 0.5, listed for you; changes nothing | `review-before-sharing.private.json` |
| **Filler** | a shot with no indicator that the `frame_kind` head reads as showing nothing; leaves a no-model film | `editorial_unvouched_filler.py` |
| **Finished-cut check** | the cut read once against every promise; warns, changes nothing | `editorial_cut_invariants.py` |

## The model tier

| Word | Meaning | Where |
|---|---|---|
| **Episode reading** | a model's answer about one episode: what happened, a representative, moments worth a record | `text_episode_reader.py` |
| **Record** | a picture an episode reading named as the record of something that happens once (an arrival, a milestone with its occasion visible, a change you can see, text naming the occasion), judged on the episode's own lines; protected from the vote | `catalogue_runtime.banked_notable_records` |
| **Account** | what the library says a period was about, written once from episode readings and reused | `library_catalogue.py` |
| **Thesis** | the account's statement of what the period was, up to 150 words | `editorial_story_grouping.py` |
| **Polish / thin layer** | the model reads the draft once, names the shots that add nothing, fills the seats | `editorial_thin_layer.py` |
| **Block vote** | every model yes or no: at most 12 rows, asked in two orders; both orders is firm, one is a maybe | `editorial_block_votes.py` |
| **Seat** | a slot the polish may fill: N (a record with no shot), R (replaces a voted-out shot), T (replaces a gate refusal), D (a swap) | `editorial_thin_refill.py` |
| **Fill on demand** | a film reads only the episodes its shots sit in; reading a whole scope ahead is optional | `episode_demand.py`, `prepare --overviews` |
| **Route** | A: no model; B: draft plus polish, for any one-window film; C: the model plans a multi-window film whole | [What a model adds](./what-a-model-adds.md) |
