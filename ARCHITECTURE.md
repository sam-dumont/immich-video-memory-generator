# Architecture Guide

> This document is optimized for LLM consumption. Reference it from CLAUDE.md
> to avoid re-reading the full codebase each session.

## Overview

Immich Memories generates video compilations from an Immich photo library.
The public lifecycle of one run (`operations/phases.py`, `OperationalPhase`):
**discovery -> download -> analysis -> selection -> render -> music -> delivery -> complete**.
Selection is the story-first editorial route, the only one: `generate` (or the Memory page's
Cut) -> `build_smart_pipeline(editorial_context)` (`analysis/editorial_runtime.py`) ->
`SmartPipeline.run_editorial_source()` -> `RuntimeEditorialPlanner.plan_source()`, which reports
five stages: **Reading dates, places and people -> Reading event evidence
-> Building editorial cards -> Editing the memory -> Validating selected source timing**. Every
attempt is durable under `<cache>/editorial-runs/<key>/attempts/<id>/`
(`operations/editorial_attempt.py`, an OS lease tells interrupted from slow); the facts and banks
it reads live in `<cache>/annotations.sqlite` (`store/`). The design is summarised in
`docs/designs/2026-09-10-story-first-selection.md`.

Selection carries the exact episode reading identities into its audit lineage. The former
pre-card period-insight pass only supplied audit prose and no selection decisions; it is no
longer requested. Existing database rows are left untouched; no new period-insight rows are written.

The period account (`analysis/editorial_story_reading.py`) reads the banked 90-minute episode
readings, one page per calendar month, cut into parts only at a day boundary. No page carries
anything from the page before it, so a month's prompt is a pure function of that month's rows: the
months read in parallel through `reader_map`, the judgment bank answers a month it has already read,
and one changed asset invalidates one month instead of every page after it. The one-day rule is
applied to the answer (`_split_by_day`), not asked for in the prompt.

Before the weighing, `editorial_story_trips.py` runs the app's trip detection over the film's pool
and folds every trip's day episodes into one story; the trip reserves
`round(slots / 2 * sqrt(trip days / film days))` pictures at its turn in the presence pass. After
the weighing, `editorial_story_threads.py` asks the reader whether stories of one place and era
that its own words link are one recurring activity, and folds each confirmed group. At
carrier admission, `editorial_story_lookalike.py` answers the repetition question from the cached
preview hashes (`hash_pair_relation`, on every tier; no pair's pixels go to a model) before a story
takes a further picture, bounded at twice the slots; a refusal frees the slot for another moment.
`editorial_story_places.py` covers the case next to the thread question, one place inside one day
or one stay: per scope (a journey film, or one story) it gives each place the pictures
`trip_allowance` would give a trip of the same share of that scope, and a picture over the bound
joins the look-alike ledger, so it frees its slot first and returns only if nothing else takes it.
A scope of one place is never bounded. The synthesis card also carries `arrivals`, the
`editorial_person_period_facts.py` projection of who the library first holds in that episode's own
month, so the thesis and the weighing can read an arrival that the relation counts flatten away.

The family-viewing gate has two floors under the reader's answer and a list beside it. The exposure
head `nsfw_marqo` decides a still on its preview and a video on up to eight frames
across its length (`editorial_preparation_detector_frames.py`, through the motion line's byte-range
keyframe reader), keeping the strongest frame: that is `det-v3`, so an existing bank re-reads that
head for every source, and videos stay out of an inference-service offload for it. A Live Photo's
clip is read the same way: it is no candidate, so `acquire_clip_companions`
(`editorial_preparation_model_facts.py`) reads it for the exposure head alone and banks it under the
clip's own id, and `load_detector_heads` puts those rows in the gate's `companion_detectors`, which
had been empty for every Live Photo. A flagged clip holds its still. The same sampled frames of a video
go through the `frame_kind` head (`prepare_clip_frames` in `editorial_preparation_heads.py`), and
`editorial_clip_frames.py` banks the clip's `clip_frames` fact: a clip that shows its moment in
fewer than three frames of four reads `frames=subject_often_missing` on its line, which the rules
reader scores 0 (a favourite still wins), and `StandingGate` refuses on every tier.
The same frames give a video its measured motion: `editorial_video_motion.py` runs the Live Photo
optical-flow residual (`flow_residual` in `editorial_motion_facts.py`) over them and banks it in
`motion_residuals` under its own producer; `UnitBuilder._video_unit` carries it, and
`measured_motion` holds a measured video to the 1.5 bar, so `BankedMotionLines` withholds a still
clip's sentence (never a favourite's) and counts it `unsupported`. No detector hold is ever lifted
by a later reading (`_head_hold` in `editorial_shareability.py`); only the owner's clearance does.
`editorial_exposure_chains.py` then holds a whole five-minute capture run that is at least half
flagged with at least three flagged captures in it, under the reason `exposure_chain`; a Live
Photo is one capture there, flagged when its still or its banked clip is. Neither the reduced
tier's `_hold()` nor the model check can clear it. `editorial_review_list.py` writes
`review-before-sharing.private.json`: the finished cut's shots between 0.2 and 0.5 that nothing
else already holds, counted in the run summary and named by `runs why`. It changes no shot.

Large period accounts page their episode evidence at 48,000 request characters. Story weighing
also caps each page at 60 stories / 48,000 characters, repeats the whole-period thesis and central
candidates, and keeps join-compatible stories together. Both orders of every page must validate
before any weights, titles or joins change; central confirmation must hold across pages. Smaller
story tables retain their existing two requests and cache keys.
When the shared candidates crowd out a page, compare them first in both orders and repeat only
the resulting central candidates. This preliminary comparison asks only for at most two
distinct, offered central-story keys; it does not request weights or edits that would be
discarded. Both orders use bounded answer recovery, and every story still receives its final
weighting decision in both orders.
If a large page still omits decisions after its repairs, or its text completion remains truncated
after transport recovery, it is split between join-compatible groups
and read again with the same central context. An indivisible group still fails visibly; partial
weights and edits never carry into the recovered page.

`editorial_rule_banked_facts.py` lets the no-model draft read what a model already answered about
this library without asking anything: audience
refusals recorded by earlier cuts of the same film for the same audience, and the representatives
and culls of any banked episode reading of the same pictures. A withheld picture is not offered to
its moment, unless it is the owner's favourite or the moment has nothing else; a named
representative leads its episode's order. Every read is named the way the writing side named it;
episode readings are matched on the episode and the exact pictures read, minus this run's own
producer, because a cull is a refusal and a representative still has to win the rules order.
Standing is not read from any bank: the facts answer it on every tier. A library nothing has read answers None, False or () everywhere, and the draft is the
one it always cut. Every rules run records what it found in `banked-facts.private.json`, so a draft
that read nothing says so.

## Editorial glossary

The private words `analysis/` uses, in roughly the order a cut meets them. Each one is defined by
the code named beside it; if the two disagree, the code wins and this entry is stale.

**Units of a library**

- **Moment**: pictures taken within 10 minutes of each other and close in place
  (`MOMENT_WINDOW_MINUTES`, `moment_grouping.py`). One moment is what one shot of the film shows.
- **Episode**: the block a moment sits in (an afternoon at a circuit, a party), cut at a
  90-minute gap (`EPISODE_WINDOW_MINUTES`, `selection_source_groups.py`).
- **Episode reading**: a model's answer about one episode: what happened, its representatives,
  its cull decisions and its notable moments, banked by exact membership and producer
  (`store/episode_readings.py`, `text_episode_reader.py`). The rules reader writes factual
  episode cards instead and never banks them as answers (`editorial_rule_episodes.py`).
- **Story**: day episodes grouped into one thing that happened, then weighed in words and turned
  into picture slots (`editorial_story_grouping.py`, `editorial_story_weighing.py`,
  `editorial_story_slots.py`). Trips and recurring threads fold into one story
  (`editorial_story_trips.py`, `editorial_story_threads.py`).
- **Capture run**: captures each taken within five minutes of the one before
  (`MIN_GAP_IN_CAPTURE_GROUP_SECONDS`). It spaces shots, and it is the unit of an exposure chain.

**The period**

- **Account**: what the library says happened in a period, written once from banked episode
  readings and read back by every later cut. One per month, one per year, and for a long window
  one per calendar year it touches plus one over those years (`library_catalogue.py`,
  `catalogue_runtime.py`, `prepare --overviews`). The rules reader writes none: an account is a
  reading.
- **Thesis**: the up-to-150-word statement of what a period was about. The story synthesis writes
  it (`editorial_story_grouping.py`) and the account carries it to the thin layer
  (`editorial_thin_catalogue.py`), where it sits above every block the model votes on.
- **Notable record**: a picture an episode reading named as a moment worth a place of its own,
  with the reason (`banked_notable_records` in `catalogue_runtime.py`). Only a story holding one
  can earn an N seat.

**Preparation**

- **Producer**: anything that writes a fact about a picture: the caption server, the heads, the
  detectors, the motion and pixel readers (`editorial_preparation*.py`). Preparation runs the
  producers a film still owes before selection starts (`editorial_runtime_evidence.py`).
- **Heads**: eight small linear classifiers over one pinned DINOv2 ONNX embedding: location,
  people, children, activity, venue, frame_kind, screen, uncovered_person
  (`triage/bundled_heads/public-8heads-v4.npz`, `editorial_preparation_heads.py`). Beside them sit
  two detectors, `nsfw_marqo` (exposure) and `doc_docling` (documents)
  (`editorial_preparation_detectors.py`).
- **Tiers**: two separate knobs. `editorial.reader` is `rules` or `model` (`auto` means rules when
  `llm.model` is blank); rules is the NAS path, a whole film from dates, places, people and
  preparation facts with no model called. `editorial.preparation.tier` is `full` (captions, heads,
  detectors), `no_captions` (heads and detectors, the default with no model configured) or
  `metadata_only` (nothing looks at pixels, so every shot is held to the family)
  (`config_models_editorial*.py`, `editorial_shareability_tiers.py`).
- **Reach**: the pictures a film can actually select (for a person film, the ones that person is
  in), plus their Live Photo siblings and capture runs. Only those get prepared; the rest of the
  window is read as Immich metadata (`editorial_film_reach.py`).
- **Fill on demand**: a film reads only the episodes its shots sit in, and banks them; reading a
  whole scope ahead is optional (`prepare --overviews`). A model-tier film left short by S seconds
  reads at most 2 * ceil(S / 3.5) more unread episodes (`episode_demand.py`,
  `editorial_thin_short.py`).
- **Bank / banked**: an answer stored under its exact inputs and producer identity, so the next
  run asks nothing and a changed asset invalidates only its own rows. The main ones:
  `annotations.sqlite` (`store/`), episode readings, accounts, cut measurements
  (`store/cut_measurements.py`) and the `structure-banks/*.private.json` files (thesis-fit
  votes, audience verdicts). No row means nobody asked, never "measured nothing". Two runs write them at
  once (the pipeline lock covers assembly only): SQLite banks write row by row, and every JSON
  bank merges what is on disk under `locked_file.file_lock` before its atomic replace.

**Building the cut**

- **Rules draft**: the film the no-model reader cuts (`editorial_rule_reader.py`,
  `editorial_structure_planner.py`). It may read what a model already banked about the library,
  which can only tighten it (`editorial_rule_banked_facts.py`).
- **Carrier**: the picture admitted to carry one chosen moment of a funded story, if it is free,
  in context and spaced from the shots already committed (`editorial_story_carriers.py`,
  `editorial_carrier_eligibility.py`). A carrier is a shot before it is rendered.
- **Standing**: does a picture stand by itself, and may it serve as context inside its story.
  Answered on every tier from the facts, never asked of a model (`editorial_standing_facts.py`: two
  points tables, heads alone or heads plus the ingest caption; a caption naming an animal, or a
  person Immich found a face for, is never refused; `face_evidence` reads the faces), and applied
  by `StandingGate` (`editorial_story_standing.py`).
- **Look-alike / scene print**: a story's next picture is kept only if it adds to the ones already
  kept (`editorial_story_lookalike.py`). The final review drops repeats by perceptual hash and by
  scene print, the pooled DINOv2 vector of a preview, which catches the same scene in another
  framing (`editorial_final_hash_review.py`, `editorial_scene_prints.py`).
- **Block vote**: the shape of every model yes/no. At most 12 rows, asked twice, in source order
  and in a hashed order; picked both times is firm, once is a maybe (`editorial_block_votes.py`).
- **Thin layer / thin polish**: model mode's editing for every one-window film (month, year,
  season, trip, lifetime) when `thin_model_layer` is on (the default). The model reads the finished rules draft once, names
  the shots that add nothing, and the freed seats are refilled through the same gates. Budget: 4
  calls per 12 draft shots plus 4 per seat (`editorial_thin_layer.py`, `editorial_thin_*.py`).
- **Thesis-fit vote**: the thin layer's one question, "which of these shots adds nothing to this
  film?", asked reject-only as a block vote under the period thesis. Named by both orders is bad,
  by one is weak (`editorial_thin_vote.py`).
- **Seat**: a slot the polish may fill (`editorial_thin_refill.py`). **N**: a story with a notable
  record and no shot. **R**: replaces a shot the vote named bad. **T**: replaces a shot the gates
  refused. **D**: a swap for a weak shot, which needs no room. The **family seat** is a separate
  thing: one picture for a close family member the draft left out, on every tier
  (`editorial_family_seat.py`).
- **Audience / shareability**: the family-viewing gate, judged against the film's sharing level
  (`just_us`, `family`, `shareable`: `defaults.sharing`, `generate --sharing`, the brief's **Who will
  watch it**, carried by `EditorialRunContext.audience` into `StructurePlanningInput.audience` and the
  attempt request). `allowed(verdict, level)` plays up to `just_us`, `family_only` or `share`; a
  caption reading of a household moment (bath, breastfeeding, changing, hygiene) is `just_us`
  (`at_household_level`), and a NAS shareable film clears clean evidence
  (`rule_audience_with_clean_share`). Flags hold first (`never_auto`, detector
  holds, exposure chains), then a reader answers `share`, `family_only` or `do_not_show` from the
  caption, heads and flags ingest banked. The strictest answer wins, the gate only ever tightens,
  and only the owner clears a hold (`editorial_shareability*.py`): per picture, from the pool,
  the storyboard or `pictures clear-hold`, written by `store/owner_decisions.py` as `source='owner'`
  flag rows. A cleared unit gets its clearance's level (`cleared` = share, `cleared_family` =
  family_only, `cleared_just_us` = just_us; `owner_verdict`) in `AudienceGate.verdict_of` before any
  check or banked hold,
  and `pictures never-use` writes `never_auto`, which `partition_units` keeps out of every unit
  pool. Owner rows stay off the editorial line, so a decision re-asks no reading. In a film shared outside the
  family, anything a detector head or exposure flag marked stays held whatever the text says
  (`editorial.strict_sharing`, on by default; applied per film, never banked). On the model tier
  the activity question (a bath, a nappy change, ...) may be answered by **Laya**, a local 0.4B
  text classifier over the compact caption, instead of the reader (`editorial_laya_reader.py`,
  `editorial.laya_audience`, off by default, Apple Silicon): it only adds holds, and its answers
  bank under their own answerer.
- **Pictures are read once**: a model looks at a picture only at ingest (the caption server, the
  heads, the detectors). No film-time stage sends a picture to any model, on any tier; the reader
  is text only. `refuse_pictures` in `tests/test_editorial_source_route_integration.py` wraps the
  one dispatch every model request passes through and fails on any request carrying a picture;
  `tests/test_editorial_demanded_previews.py` holds the production route to that, cold and warm.
- **Exposure chain**: a capture run at least half flagged by the exposure head, with at least three
  flagged captures, is held whole (`editorial_exposure_chains.py`).

**Words from the post-card editor, still in the code**

- **Workprint**: the evidence handed to the editor: every surviving moment in chronology with its
  representative, plus the moment cards (`StructureWorkprint` in `selection_structure.py`,
  `TextEditorialWorkprint` in `editorial_orchestration.py`).
- **Moment wall**: the moment cards rendered as compact TSV rows, at most 256 characters each and
  no ids, for the text-only editor (`editorial_moment_wall.py`, `editorial_wall_rows.py`).
- **Post-card**: after the moment cards are built ("Building editorial cards -> Editing the
  memory"). The original post-card moment editor is retired; its contracts live in
  `editorial_case.py`.

## Two Trees

`src/immich_memories/` is the app. `services/inference/immich_memories_inference/` is a second
top-level package — the inference service, which serves the encoder, the eight public heads and the
two detectors over HTTP (`/ping`, `/health`, `/facts`) in its own image with its own device
variant (`docker/Dockerfile.inference`, `docker/hwaccel.inference.yml`). It imports the app's
triage engine and detector module rather than reimplementing them, which is what keeps a fact
computed there identical to one computed in process; two import-linter contracts hold the
direction of that dependency and keep the UI and CLI out of it. The service is not a distribution:
the image puts it on `PYTHONPATH`, and `pythonpath` in `[tool.pytest.ini_options]` does the same
for the suite. Its `seeding.py` fills a cold cache volume from the app's `pinned_models.py` table,
so a fresh PVC needs no `kubectl cp`. Design:
`docs/implementation-plans/2026-09-11-phase5-inference-service.md`.

## Build System

The **Makefile** is the single source of truth for all commands:
- CI (`ci.yml`) uses `make` targets
- Pre-commit hooks use `make` targets for file-length and complexity
- Run `make check` before committing (lint + format + typecheck + file-length + complexity + test)

## Composition Pattern

Large classes compose smaller service objects instead of inheritance: no mixins anywhere.
Each service is a standalone class with a focused responsibility, injected via the constructor.
This keeps classes under the 800-line soft limit (1000 hard) while maintaining a single public API.

The four core orchestrators and their composed services:

**VideoAssembler** (processing/video_assembler.py) composes 5 services:
- `FFmpegProber` (ffmpeg_prober.py): duration/resolution probing via ffprobe
- `ClipEncoder` (clip_encoder.py): per-clip trimming and re-encoding
- `AssemblyEngine` (assembly_engine.py): strategy-based multi-clip assembly
- `AudioMixerService` (audio_mixer_service.py): background music mixing
- `TitleInserter` (title_inserter.py): title screen concatenation
  - composes `TitleBackgroundRenderer` (title_background_renderer.py) for the pre-rendered
    clip a title deblurs out of, and builds one `TitleDividerPlanner`
    (title_divider_planner.py) per memory to place month/year/location cards

**SmartPipeline** (analysis/smart_pipeline.py) is the pipeline surface the CLI and the UI
drive. On the production route `build_smart_pipeline(editorial_context)` (editorial_runtime.py)
hands it a `RuntimeEditorialPlanner`, and `run_editorial_source()` runs preparation, the two
readings, the structure and story planners and the timing certification, then projects the plan
into a `PipelineResult` (editorial_projection.py). The route's seams are Protocol-typed ports
rather than services: `EditorialRuntimePorts` (editorial_runtime_ports.py: providers, people
loader), `ProductionPostCardBackend` (editorial_runtime_backend.py), `StructurePlannerPorts`
(editorial_structure_contract.py: judges, banks, audience gate).

`SmartPipeline` composes nothing else: the only seams it reads are `PipelineConfig.hdr_only`
and the planner, and `ProgressTracker` (progress.py) is the run clock the stage reporter reads.

**ImmichClient** (api/immich.py) composes 5 services:
- `SearchService` (search_service.py): video search and time bucket queries
- `AllAssetsService` (all_assets_service.py): type-agnostic asset queries (trip detection)
- `AssetService` (asset_service.py): asset/video download operations
- `PersonService` (person_service.py): person/face operations
- `AlbumService` (album_service.py): album operations

**TitleScreenGenerator** (titles/generator.py) composes 3 services:
- `RenderingService` (rendering_service.py): GPU/CPU renderer selection, video creation
- `EndingService` (ending_service.py): fade-to-white ending generation
- `TripService` (trip_service.py): trip map and location card screens

**`assemble_streaming()`** (processing/streaming_assembler.py) is the streaming render path,
a three-stage pipe rather than a class: `make_decoder()` (streaming_frame_decoder.py) turns a
clip into normalized raw frames, `FrameBlender` (streaming_frame_blender.py) writes those
frames to a `FrameSink` and crossfades across clip boundaries, and `StreamingEncoder` (the
sink it is constructed with) pipes them into FFmpeg.

**KernelTitleRenderer** (titles/renderer_kernels.py) owns the background and the per-frame
GPU pipeline, and composes 3 services (all take Protocol-typed config/buffers):
- `ParticleField` (kernel_particles.py): bokeh drift and fireworks physics, CPU numpy only
- `TitleTextRenderer` (kernel_text.py): SDF and PIL text compositing onto the frame buffer
- `AnimatedBlur` (kernel_blur.py): the deblur's Gaussian, decimated and held between frames

**`generate_memory()`** (generate.py) is the top-level orchestrator above the four; it runs
the `OperationalPhase` lifecycle end to end (there is no `GenerationPipeline` class) with
these helper modules:
- `generate_downloads.py`: parallel asset downloads
- `generate_clips.py`: clip extraction, probing, cleanup
- `generate_photos.py`: photo rendering, budget allocation, clip merging
- `generate_music.py`: music resolution, AI generation, audio mixing
- `generate_privacy.py`: GPS anonymization, fake names/cities, trip titles
- `generate_settings.py`: assembly/title settings, assembler creation
- `generate_render.py`: local source preparation/assembly or configured worker handoff
- `processing/source_preparation.py`: bounded completion queue with worker-owned clients;
  `generate_clips.py` gives each source its own scratch directory and restores editorial order.
  `DownloadCoordinator.sources_for` shares downloaded components across workers by source ID.
- `processing/remote_render.py`: authenticated jobs, bounded polling, a SHA-256-checked download,
  and a staged film that reuses the worker's decode when the bytes match
- `processing/remote_render_plan.py`: frozen cut serialization, including certified Live material
- `generate_timeline.py`: final-duration validation and content budget guards
- `generate_delivery.py`: Immich upload of a finished artifact + delivered/pending/failed run state
- `delivery_timestamp.py`: the capture instant a film is filed under, and the container tags carrying it

## Package Structure

```
src/immich_memories/
├── api/                        # Immich server communication
│   ├── immich.py               # ImmichClient (composes 5 services)
│   ├── search_service.py       # SearchService: video search, time buckets
│   ├── all_assets_service.py   # AllAssetsService: type-agnostic queries
│   ├── asset_service.py        # AssetService: asset/video download
│   ├── person_service.py       # PersonService: person/face operations
│   ├── album_service.py        # AlbumService: album operations
│   ├── sync_client.py          # Sync wrapper for async client
│   ├── compatibility.py        # Immich API-version compatibility policy (v2/v3 resolution)
│   └── models.py               # API data models (Asset, Person, etc.)
│
├── photos/                     # Photo-to-video animation (converts stills to .mp4 clips)
│   ├── __init__.py             # Public API re-exports
│   ├── renderer.py             # Frame-by-frame renderer: Ken Burns, face_aware_pan, render_split (parked)
│   ├── animator.py             # Photo source prep: HEIC decode, downscale cap, HDR detection
│   ├── photo_pipeline.py       # Render one photograph as a Ken Burns clip, streamed to FFmpeg
│   ├── ultrahdr.py             # Ultra HDR JPEG (Android/Pixel): MPF parser, gain map, ISO 21496-1
│   └── burst_dedup.py          # One photo per burst: near-duplicates shot within minutes of each other
│
├── memory_types/               # Memory type presets & factory
│   ├── __init__.py             # Public API re-exports
│   ├── registry.py             # MemoryType enum
│   ├── presets.py              # PersonFilter, MemoryPreset
│   ├── date_builders.py        # build_season(), build_month(), build_on_this_day()
│   └── factory.py              # Registry + preset factories; Album is handled by cli/_album_generation.py
│
├── analysis/                   # Selection: the story-first editorial route (words: see Editorial glossary)
│   ├── smart_pipeline.py       # SmartPipeline: run_editorial_source() is the production entry
│   ├── editorial_runtime.py    # RuntimeEditorialPlanner + build_smart_pipeline(); _ports.py, _backend.py beside it
│   ├── editorial_runtime_evidence.py # The film-time preparation a cut waits on, and the annotation store it reads
│   ├── annotation_line_fields.py # Which parts of a picture's line are its content and which we wrote; content rules read only the first
│   ├── editorial_film_reach.py # What a film prepares: its demanded pictures, their Live families and capture runs
│   ├── editorial_orchestration.py  # TextEditorialPlanner: episodes -> cards -> edit
│   ├── editorial_rule_episodes.py  # Factual episode cards / omitted thesis; no semantic-bank writes
│   ├── editorial_rule_reader.py    # Rules for worthiness, grouping and standing; shared allocation
│   ├── editorial_rule_banked_facts.py # What a model already answered, read by the draft that asks nothing
│   ├── editorial_story_standing.py # StandingGate: does a picture stand by itself, and may it serve as context
│   ├── editorial_standing_facts.py # Standing from facts, no model: heads (+ caption words), faces and animals never refused
│   ├── editorial_final_hash_review.py # The final duplicate review every cut runs: cached preview hashes, then scene prints across stories
│   ├── editorial_scene_prints.py   # CachedScenePrints: a preview's pooled DINOv2 pack, banked, for the scene half of that review
│   ├── editorial_family_seat.py    # A close family member with no shot gets one seat, after the draft, on every tier
│   ├── editorial_unvouched_filler.py # No-model cut's last pass: filler with no indicator that shows nothing leaves, unreplaced
│   ├── editorial_cut_invariants.py # One check of the finished cut against every pass's promise (seat, favourite, eras, Live motion, holds, order); changes nothing
│   ├── editorial_story_candidates.py # Every picture of a story as a carrier row, for a stage that adds a shot
│   ├── editorial_thin_layer.py     # ThinPolish: the model reads a rules cut once instead of planning the film;
│   │                               # held to 4 calls per 12 draft shots + 4 per seat (thin_budget)
│   ├── editorial_thin_step.py      # The planner's polish step; an unpolished draft (unread period) gets the no-model passes
│   ├── editorial_thin_catalogue.py # What a polish may read of a catalogued period: account, stories, hints
│   ├── editorial_thin_gates.py     # Every draft shot put to standing, audience, spacing and the hash review
│   ├── editorial_thin_vote.py      # One closed thesis-fit vote over the whole cut, in balanced blocks,
│   │                               # source order first, the hashed order only where it decides
│   │                               # rows carry close family relations; a relative's only shot is held
│   │                               # and so is a year's only shot (voice_per_partition); a year keeps one
│   ├── editorial_thin_pages.py     # What a seat is offered: motion first, records first, the refused moment first
│   ├── editorial_thin_short.py     # A short cut reads ≤2·⌈S/3.5⌉ unread episodes of shot-less stories;
│   │                               # only a story whose reading records a moment gets a seat
│   ├── editorial_thin_refill.py    # Which seats open; each picks from 12 rows first, then only the picks
│   │                               # meet the gates, and a refused pick is picked once more
│   ├── editorial_laya_reader.py    # Laya answers the audience check's activity question from the compact
│   │                               # caption (model tier, editorial.laya_audience); only adds holds
│   ├── editorial_audience_batch.py # The audience question over 12 carriers per request in two orders,
│   │                               # one answer each; either order's hold holds
│   │                               # (advanced.editorial.thin_batched_audience, off by default)
│   ├── library_catalogue.py    # The account of a month/year (or a multi-year window: one per year
│   │                           # plus one over them), written over banked episode readings
│   │                           # (plus the no-model facts of episodes a cut did not read), keyed by
│   │                           # them plus the model that wrote them
│   ├── catalogue_runtime.py    # Who writes one: `prepare --overviews` over a window, and a film run
│   │                           # over a month, year or window the library has no account of yet
│   ├── episode_demand.py      # The draft reads the period from facts; only the episodes its shots
│   │                          # sit in are read by the model, when the polish layer asks for the account
│   ├── editorial_home_radius.py    # Where home is, and whether captures sit inside its radius
│   ├── editorial_shareability_tiers.py  # Audience evidence policy for reduced preparation tiers
│   ├── editorial_review_list.py    # The finished cut's shots in the detector's 0.2-0.5 grey zone that
│   │                               # nothing else holds; a list for the owner, never a gate
│   ├── editorial_exposure_chains.py # A five-minute capture run half flagged, with 3+ flagged in it,
│   │                                # is held whole: the hold the detector's per-picture answer misses
│   ├── editorial_preparation*.py   # Annotation preparation: captions, public heads, detectors, pixel facts,
│   │                               # motion lines (one caption-seat sentence per video, read by
│   │                               # the pick).
│   │                               # _model_facts.py plans who answers each model producer;
│   │                               # _detector_frames.py samples a video's eight frames for the
│   │                               # exposure head, through the motion line's keyframe reader
│   ├── selection_source*.py    # The canonical source model: admission, provenance, groups, invariants
│   ├── text_episode_reader.py  # Reading event evidence (paged, banked); the same reading names
│   │                           # each episode's notable moments, which the polish layer seats and protects
│   ├── text_episode_prompt.py  # What that reading is asked, and what it may take a name from
│   │                           # (a film's on-demand reading asks the lean form: no Cull, one representative)
│   ├── text_episode_paging.py  # Its request limits: an episode cut into pages, pages packed into prompts
│   ├── editorial_album_index.py # Album names by asset, one listing + one read per album, once per run
│   ├── editorial_story_*.py    # Story reading, weighing, slots, shortlist, carriers: the story planner
│   ├── editorial_story_trips.py     # Detected trips become one story each, with a reserve for their length
│   ├── editorial_story_lookalike.py # A story's further picture is refused when it repeats one it holds
│   ├── editorial_story_depth.py     # A short film's free slots as verified-different frames inside shown moments
│   ├── editorial_story_trim.py      # The allocation in reverse when the production budget is tighter
│   ├── editorial_story_threads.py   # A recurring activity at one place is one story per era, if the reader agrees
│   ├── editorial_story_places.py    # One place of a scope holds only the share of it a trip of that length would
│   ├── editorial_page_recovery.py  # Bounded ask/retry/repair for a stage that reads its own JSON envelope
│   ├── provider_failure.py     # What a 4xx/5xx means: refused, come back later, down, or a bad credential
│   ├── llm_single_flight.py    # One paid answer per judgment key, however many readers ask at once
│   ├── editorial_structure_*.py    # The structure planner: wall, subject/trip admission + standing gates, audience, record
│   │                               # _finishing.py holds PlanRun and the passes that run over a settled cut
│   │                               # (motion/timing, audience gate, duplicate review, trim)
│   ├── editorial_projection.py # Plan -> PipelineResult, and the stage reporter
│   ├── provider_health.py      # ProviderHealth: what a provider's answer says about its availability (preflight)
│   ├── selection_trace.py      # Per-stage funnel record: what each filter received and let through
│   ├── progress.py             # ProgressTracker: the run clock the stage reporter reads
│   ├── trip_detection.py       # GPS-based trip detection (clustering, injected geocoder)
│   ├── trip_place.py           # Names a trip at the scale its pictures cover (city → country)
│   ├── place_name_cache.py     # Localised names for the places one cut shows, one ask each
│   ├── trip_discovery.py       # Shared UI/CLI all-asset discovery, including year-boundary trips
│   ├── special_day.py          # Every run of activity, and a found day named from its own lines
│   ├── special_day_sequence.py # Days read a month at a time in order (close family by role on each line); 30 s film floor
│   ├── prepared_captions.py    # Exact-producer caption reads for music and special-day text calls
│   ├── special_day_title.py    # What a day may be called: the grounding guard, the re-ask, the fallback
│   ├── album_source.py         # Album mode: the album is the candidate pool, nothing is searched for
│   ├── source_filter.py        # Drop doorbell / dashcam / screen-recorder uploads by filename
│   ├── source_quality.py       # Drop messaging re-encodes: sub-1080p with no camera EXIF
│   ├── llm_failures.py         # Separate "the model could not answer" from a bug in the calling code
│   ├── request_heartbeat.py    # RequestHeartbeat: periodic log line for long-outstanding HTTP calls
│   ├── duplicate_hashing.py    # Perceptual hashing for duplicates
│   ├── thumbnail_prefetch.py   # cached_preview_bytes(): the one preview reader the editorial modules share
│   ├── apple_vision.py         # macOS Vision framework face detection (smart crops)
│   ├── apple_vision_image.py   # Vision image conversion helpers
│   ├── llm_query.py            # The live transport: one prompt, one connection, one answer
│   ├── llm_wire.py             # The two request dialects, what a reply says, and the reasoning budget
│   ├── llm_batch.py            # A stage's independent prompts as one provider batch (half price, async): the two wire dialects and their transports
│   ├── llm_providers.py        # Named providers: their URL, their adapter, the way they reason
│   ├── llm_preparation_usage.py # Caption/control/motion usage, including unmetered failed attempts
│   ├── llm_usage_record.py     # Atomic usage checkpoints, split per model/stage, with unknown usage
│   ├── live_photo_pipeline.py  # Keep a Live Photo's video half out of the video pool
│   ├── live_clock_offsets.py   # BankedClockOffsets: a burst's companion clock offsets, measured
│   │                           # once per pair (each companion fetched and decoded once) and banked
│   └── motion_rendering.py     # What a photograph could show as motion, if the memory wants it;
│                               # may_play: a join earns its length, a lone Live Photo is put to
│                               # the motion discriminant instead. The draft plans every burst on
│                               # metadata (plan_before_measuring); UnitBuilder.measured_stitch
│                               # measures only the bursts the cut keeps, when motion is resolved
│
├── processing/                 # Video processing & assembly
│   ├── video_assembler.py      # VideoAssembler (composes 5 services)
│   ├── assembly_engine.py      # AssemblyEngine: strategy-based multi-clip assembly
│   ├── assembly_config.py      # Dataclasses: AssemblySettings, AssemblyClip, etc.
│   ├── streaming_assembler.py  # StreamingEncoder + assemble_streaming(): low-memory 4K assembly
│   ├── streaming_frame_decoder.py # FrameDecoder / make_decoder(): clip -> normalized raw frames
│   ├── streaming_frame_blender.py # FrameBlender: frames -> sink, crossfades, progress/preview
│   ├── streaming_audio.py      # Streaming audio processing helpers
│   ├── ffmpeg_prober.py        # FFmpegProber: ffprobe-based duration/resolution
│   ├── clip_encoder.py         # ClipEncoder: per-clip trimming/re-encoding
│   ├── clip_probing.py         # Clip probing helpers
│   ├── clip_transitions.py     # Clip transition helpers
│   ├── clip_validation.py      # Clip validation helpers
│   ├── clips.py                # ClipExtractor: download & re-encode
│   ├── download_coordinator.py # DownloadCoordinator: bounded prefetching, one sync client per worker
│   ├── probe_cache.py          # ProbeCache: run-scoped normalized source probing (injected into FFmpegProber)
│   ├── encoding_plan.py        # EncodingPlan / resolve_encoding_plan(): immutable output encoding contract
│   ├── output_canvas.py        # Resolve the single pixel canvas used by one run
│   ├── output_contract.py      # metadata probe, render-bounded full decode check, atomic publish
│   ├── timeline_budget.py      # plan_timeline(): pure planning of content + title-screen timeline
│   ├── title_inserter.py       # TitleInserter: title screen concatenation
│   ├── title_background_renderer.py # TitleBackgroundRenderer: pre-renders the clip a title reveals into
│   ├── title_divider_planner.py # TitleDividerPlanner: month/year/location divider cards
│   ├── audio_mixer_service.py  # AudioMixerService: background music mixing
│   ├── privacy_audio.py        # Privacy mode audio processing (lowpass filter)
│   ├── clip_caption.py         # The per-clip date/place caption: text and geometry, no decoding
│   ├── caption_image.py        # Captions drawtext cannot draw (non-Latin scripts), rendered with the title fonts
│   ├── frame_sampling.py       # One cached still-frame sampler for mood, title colours and previews
│   ├── playback_keyframes.py   # A playback's index and a few keyframes by byte range, decoded from a sparse copy
│   ├── frame_preview.py        # Frame extraction for previews
│   ├── hdr_utilities.py        # HDR detection & conversion filters
│   ├── scaling_utilities.py    # Resolution, aspect ratio, smart crop
│   ├── ffmpeg_runner.py        # FFmpeg execution with progress
│   ├── hardware.py             # Hardware detection (GPU, encoders)
│   ├── hardware_detection.py   # Hardware detection backends
│   ├── hardware_encode.py      # VAAPI/QSV device init + hwupload for built commands
│   ├── rate_control.py         # CRF -> per-encoder constant-quality flags
│   └── live_photo_merger.py    # Live Photo merging with a common canvas for mixed source sizes
│
├── audio/                      # Audio processing
│   ├── mixer.py                # Audio mixing & ducking
│   ├── mixer_class.py          # AudioMixer class
│   ├── mixer_helpers.py        # Mixing helper functions
│   ├── mood_analyzer.py        # Mood detection for music matching
│   ├── mood_analyzer_backends.py # Mood analysis backends
│   ├── music_generator.py      # AI music generation orchestrator
│   ├── music_generator_client.py # Music generation client
│   ├── music_generator_models.py # Music generation data models
│   ├── music_sources.py        # Music source providers (local library)
│   ├── text_mood.py            # Banked music judgment from saved cut text; private answering-route record
│   ├── music_pipeline.py       # Multi-provider pipeline (ACE-Step -> MusicGen fallback)
│   ├── bundled_music.py        # The 28 bundled royalty-free tracks (`music` extra), used with no backend
│   ├── track_tempo.py          # Measure a bundled track's tempo (numpy onset autocorrelation, no librosa)
│   ├── beat_grid.py            # Ask the generator for a tempo whose beat divides the photo cut cadence
│   ├── mastering.py            # Loudness + high-shelf pass so a generated track sits under video
│   └── generators/             # Music generation backends
│       ├── base.py             # MusicGenerator ABC + StemSeparator Protocol
│       ├── factory.py          # Generator factory
│       ├── memory_budget.py    # Will this ACE-Step profile fit in RAM? (checked before jetsam decides)
│       ├── musicgen_backend.py # MusicGen API (generation + remote Demucs stems)
│       ├── ace_step_backend.py # ACE-Step lib/API (mode choice, captions, REST protocol)
│       ├── ace_step_runtime.py # ACE-Step in-process handlers: device, MLX/torch memory, one render
│       ├── ace_step_isolated.py # Local subprocess using the installer's separate audio environment
│       ├── ace_step_captions.py # Dense caption templates
│       └── demucs_local.py     # Local Demucs stem separation (in-process)
│
├── titles/                     # Title screen generation
│   ├── generator.py            # TitleScreenGenerator (composes 3 services)
│   ├── rendering_service.py    # RenderingService: GPU/CPU renderer selection
│   ├── ending_service.py       # EndingService: fade-to-white ending
│   ├── trip_service.py         # TripService: trip map + location cards
│   ├── _text_memory_types.py   # Memory type title helpers
│   ├── _trip_titles.py         # Trip title text generation
│   ├── convenience.py          # Convenience/factory functions
│   ├── encoding.py             # Title video encoding
│   ├── video_encoding.py       # Video encoding helpers
│   ├── text_builder.py         # Text layout & positioning
│   ├── content_background.py   # Content-aware background generation
│   ├── renderer_pil.py         # PIL-based renderer
│   ├── renderer_kernels.py     # KernelTitleRenderer: background + frame pipeline
│   ├── kernel_particles.py     # ParticleField: bokeh drift / fireworks physics
│   ├── kernel_text.py          # TitleTextRenderer: SDF + PIL text compositing
│   ├── text_layout.py          # Where the two text blocks sit, and the gate that refuses an overlap
│   ├── line_breaking.py        # Where a title breaks into lines: balanced, inside the frame, CJK-aware
│   ├── letter_case.py          # Capitals the way each script sets them, for titles and captions
│   ├── kernel_blur.py          # AnimatedBlur: quarter-res deblur Gaussian, held while it stands
│   ├── gpu_kernel_backend.py   # The only `import quadrants as ti` in the tree (behind the probe)
│   ├── kernels.py              # GPU kernels + lazy compilation (init_kernels)
│   ├── kernel_backend_probe.py # The gate: which arch can dispatch here, asked without loading
│   ├── kernel_video.py         # GPU title video creation
│   ├── ffmpeg_pipe.py          # Feed raw frames to FFmpeg without deadlocking on an unread stderr
│   ├── safe_zones.py           # Keep vertical titles clear of the Reels/Shorts/TikTok button rail
│   ├── map_animation.py        # Satellite map fly-over (van Wijk zoom)
│   ├── map_renderer.py         # Map tile rendering (staticmap + PIL overlay)
│   ├── backgrounds.py          # Background generation
│   ├── backgrounds_animated.py # Animated gradient backgrounds
│   ├── animations.py           # Text animations
│   ├── styles.py               # Visual style presets
│   ├── colors.py               # Color utilities
│   ├── fonts.py                # Bundled title families (nothing downloaded at run time)
│   ├── font_chain.py           # ChainFont: per-letter Noto fallback, bidi run order
│   ├── script_fonts.py         # Pinned Noto script fonts, `titles fonts --install`
│   ├── llm_titles.py           # LLM-generated titles
│   ├── title_source.py         # TitleSource: which source produced the opening title
│   ├── sdf_font.py             # SDF font rendering
│   ├── sdf_font_rendering.py   # SDF rendering helpers
│   └── sdf_atlas_gen.py        # SDF atlas generation
│
├── cli/                        # Command-line interface (Click)
│   ├── __init__.py             # Main CLI group + `ui` command
│   ├── generate.py             # `generate`
│   ├── generate_options.py     # `generate`'s flags, grouped; group order is the --help order
│   ├── generate_resolution.py  # What those flags mean against the config, presets and conflicts
│   ├── _analyze_export.py      # `analyze`, `export-project`
│   ├── config_cmd.py           # `config`, `years`, `preflight`
│   ├── people_cmd.py           # `people` scan/show
│   ├── models_cmd.py           # `models fetch`
│   ├── prepare_cmd.py          # `prepare`
│   ├── scheduler_cmd.py        # `scheduler list/status/start`
│   ├── auto_cmd.py             # `auto suggest/run/history/status/install/test-notification`
│   ├── special_days_cmd.py     # `discover-days` and `days-due`: the days worth a memory of their own
│   ├── cache_cmd.py            # `cache stats/export/import/backup`
│   ├── titles.py               # `titles test`, `titles fonts`
│   ├── runs.py                 # `runs list/show/story/why/stats/storage/delete`
│   ├── pictures_cmd.py         # `pictures show/clear-hold/never-use/undo/list`: the owner's word on one picture
│   ├── music_cmd.py            # `music search/analyze/add`
│   ├── hardware_cmd.py         # `hardware` info display
│   ├── _helpers.py             # Shared console/print utilities
│   ├── _generation_preview.py  # Plain-text summary for read-only generation planning (--dry-run)
│   ├── _config_errors.py       # Config error formatting
│   ├── _flags.py               # Shared validation for flags more than one command takes
│   ├── _pipeline_runner.py     # Run SmartPipeline over the fetched assets + generate
│   ├── _editorial_context.py   # CLI flags + presets -> one EditorialRunContext
│   ├── _run_timeline.py        # The run's timeline: selection budget, then the settled plan
│   ├── _asset_fetch.py         # What a memory asks Immich for: videos, Live Photos, stills
│   ├── _album_generation.py    # Album mode: an Immich album is the candidate pool
│   ├── _llm_title.py           # Opt-in LLM title on the CLI path (the wizard's default differs)
│   ├── _trip_generation.py     # Trip detection, selection, per-trip generation
│   ├── _trip_display.py        # Trip table formatting & selection logic
│   ├── _date_resolution.py     # Date range resolution for memory types
│   ├── _generate_display.py    # Params table + result printing for `generate`
│   └── _live_display.py        # Rich Live interactive progress display
│
├── ui/                         # NiceGUI web interface
│   ├── app.py                  # App setup & routing
│   ├── auth.py                 # Auth middleware, credential verification, session helpers
│   ├── auth_oidc.py            # OIDC client (authlib starlette integration, singleton)
│   ├── health_api.py           # GET /health, /health/live, /health/ready — probe payloads + snapshot cache
│   ├── trigger_api.py          # POST /api/trigger — runs what `auto run` decides, 202 + status URL
│   ├── reverse_proxy.py        # Secure cookie + trusted X-Forwarded-* kwargs for ui.run
│   ├── state.py                # Shared UI state
│   ├── session_storage.py      # Expire the storage-user-*.json files NiceGUI writes but never cleans
│   ├── theme.py                # UI theme
│   ├── components.py           # Shared UI components
│   ├── nicegui_compat.py       # Compatibility helpers for NiceGUI background work
│   └── pages/
│       ├── login.py                # Login page (basic form + OIDC SSO button)
│       ├── memory.py               # The Memory page router: brief, cut in progress, result
│       ├── memory_brief.py         # The brief: type select, its params, Advanced, Cut
│       ├── memory_duration.py      # The duration line: the type's answer or an override
│       ├── memory_run.py           # The cut that outlives its page: install check, arm, poll, cancel, recover;
│       │                           # a refused or failed cut lands in AppState.cut_failure, the brief's red card
│       ├── memory_storyboard.py    # The storyboard tab: the cut in the order it plays
│       ├── picture_decisions.py    # Clear hold / Never use / Undo under a pool picture or a storyboard shot
│       ├── cut_progress_view.py    # A cut in progress: its pictures, the bar, the stage lines
│       ├── memory_story.py         # The story view: thesis, stories, carriers with reasons
│       ├── memory_story_data.py    # The only UI reader of plan.private.json -> frozen StoryView
│       ├── step1_config.py         # Immich connection panel + custom date range
│       ├── step1_cache.py          # Cache management UI
│       ├── step1_presets.py        # The parameters each memory type asks for
│       ├── step1_tabs.py           # Step 1 tab layout
│       ├── step2_review.py         # Clip review orchestration
│       ├── step2_loading.py        # Loading state UI
│       ├── step2_helpers.py        # Shared step2 utilities
│       ├── clip_grid.py            # Clip card grid display
│       ├── clip_review.py          # Clip refinement controls
│       ├── clip_pipeline.py        # The blocking cut worker and its editorial context
│       ├── pipeline_title.py       # Pipeline title display
│       ├── step3_options.py        # Assembly options
│       ├── film_length.py         # The length card: estimated before the render, measured after
│       ├── _step3_music_preview.py # Music preview controls
│       ├── step4_export.py         # Export & download
│       ├── _step4_generate.py      # Generation logic
│       ├── step4_recovery.py       # Reload recovers a run that outlived the page
│       ├── _step4_upload.py        # Upload-back to Immich
│       ├── settings_config.py      # Settings page
│       └── settings_people.py      # The companion editor: confirm who's who, flag twins
│
├── tracking/                   # Run history & telemetry
│   ├── run_database.py         # SQLite run storage
│   ├── run_database_rows.py    # SQLite row <-> model conversion
│   ├── run_lifecycle_errors.py # Refused lifecycle transitions and their diagnosis
│   ├── run_tracker.py          # Pipeline run tracking
│   ├── run_id.py               # Run ID generation
│   ├── models.py               # Run/phase data models
│   └── system_info.py          # System info collection
│
├── cache/                      # Analysis caching system
│   ├── __init__.py             # Re-exports public API
│   ├── database.py             # VideoAnalysisCache: owns cache.db's schema; the legacy segment tables it still reads
│   ├── schema_migrator.py      # SchemaMigrator: schema ladder v1..vN, DDL
│   ├── versions.py             # SCHEMA_VERSION / ANALYSIS_VERSION (independent)
│   ├── migration_sql.py        # Transactional migration helpers
│   ├── migration_v11.py … v23.py # One module per schema migration (no v18, no v20)
│   ├── asset_score_cache.py    # The legacy photo scorer's table, still read by `cache stats/export/import`
│   ├── judgment_cache.py       # Reasoning-mode LLM verdicts, keyed by the exact prompt asked
│   ├── thumbnail_cache.py      # File-based thumbnail storage
│   ├── disk_budget.py          # LRU-by-mtime eviction that holds a cache directory to a size cap
│   └── video_cache.py          # Downloaded video file cache
│
├── scheduling/                 # Scheduled memory generation
│   ├── engine.py               # Scheduler: cron parsing, next job calculation
│   ├── executor.py             # resolve_schedule_params(): schedule entry -> generation params
│   ├── daemon.py               # Daemon loop (foreground, SIGINT/SIGTERM)
│   └── models.py               # Scheduling data models
│
├── store/                      # The annotation store: every banked fact and reading
│   ├── caption_provenance.py   # What served each caption (served /models row + control digest), grouped
│   ├── motion_lines.py         # The motion line per video, keyed by picture, producer and source digest,
│   │                           # with what produced it (question, keyframes, admitting residual)
│   ├── library_overviews.py    # Read-only: the library's own account of a period, written by cataloguing
│   ├── library_catalogue.py    # The only writer of that table: content-addressed period accounts
│   ├── owner_decisions.py      # The only writer of the owner's per-picture decisions (clear hold,
│   │                           # never use): `source='owner'` rows in `flags`, one per picture
│   ├── cut_measurements.py     # What a cut measures and banks: a Live Photo's motion residual, a
│                               # clip's speech regions and a Live burst's companion clock offsets,
│                               # keyed the same way (a missing row is "not measured", never
│                               # "measured as nothing")
│                               # (annotations.sqlite; see docs/research for the design)
│
├── triage/                     # The pinned DINOv2 ONNX encoder and its eight context heads
│
├── people/                     # The library's people graph (counts and dates, no pixels)
│   ├── signatures.py           # Tiers, onset, twins, duplicates, dyads, owner curve pairing
│   ├── graph.py                # build_graph(): Immich roster + co-occurrence -> PeopleGraph
│   ├── companion.py            # ~/.immich-memories/people.yaml; confirmed beats inferred
│   ├── expression_window.py    # The earliest day a people condition can hold, from birth dates
│   └── editor.py               # The companion editor's model: the file as rows, and back
│
├── automation/                 # Smart automation (auto suggest/run)
│   ├── __init__.py             # Public API re-exports
│   ├── candidates.py           # Memory candidate detection
│   ├── candidate_scorer.py     # Candidate scoring & ranking
│   ├── candidate_discovery.py  # CandidateDiscovery: one library snapshot -> ranked candidates
│   ├── event_detectors.py      # Event-based detectors (activity bursts)
│   ├── calendar_detectors.py   # Calendar-based detectors (monthly, yearly)
│   ├── special_day_scan.py     # Scheduled scan for days worth resurfacing (skips holidays and trips)
│   ├── special_day_facts.py    # No-model day scan: one loud fact per day, ranked, a few a year
│   ├── variety.py              # Cadence and rotation rules for candidates
│   ├── failure_backoff.py      # Keep a candidate that keeps failing out of the nightly slot
│   ├── models.py               # Typed values returned/persisted by automation
│   ├── generation_request.py   # Typed boundary from candidates to the `generate` CLI
│   ├── state_store.py          # SQLite persistence for automation attempts
│   ├── status.py               # Cooldown gate + read-only AutomationStatus contract
│   ├── delivery_retry.py       # Durable state for one pending delivery retry
│   ├── notification_state.py   # Durable, sanitized notification delivery health
│   ├── trip_input_cache.py     # Durable, identity-checked inputs for auto trip discovery
│   ├── notifications.py        # Apprise notification integration
│   ├── runner.py               # Auto-run orchestrator (lease, subprocess, attempt record)
│   ├── in_process_scheduler.py # Daily timer inside the UI/Docker process
│   ├── runtime_provenance.py   # Which code a scheduled job actually ran: version, commit, checkout age
│   └── system_scheduler.py     # OS scheduler integration (launchd/systemd/cron)
│
├── operations/                 # Public lifecycle contract + read-only ops reports
│   ├── auto_output.py           # Private complete child transcripts, addressed by automation attempt
│   ├── call_families.py        # family_of()/calls_by_family(): model calls grouped by stage family
│   ├── cut_progress.py         # Where a run is, as one record the page and the terminal both read
│   ├── run_index.py            # A run id resolved to its attempt directory, for both surfaces
│   ├── candidate_fates.py       # Saved pool outcomes + decision-log reader shared with runs why
│   ├── picture_holds.py         # What holds a picture + the owner's decision, for the pool, storyboard and CLI
│   ├── caption_origins.py      # One picture's caption origin, and the run's distinct-origin line
│   ├── phases.py               # OperationalPhase / PhaseEvent: stable outer lifecycle
│   └── storage_report.py       # build_storage_report(): output + cache storage inventory (`runs storage`)
│
├── planning/                   # Media-aware duration planning
│   └── auto_duration.py        # decide_memory_duration(): Auto length fitted to discovered media, CLI and UI
│
├── config.py                   # YAML configuration management (re-exports)
├── config_loader.py            # Config loading logic
├── config_presets.py           # Named presets (`preset: fast`) that fill several knobs at once
├── config_models.py            # Resources a run uses: Immich server, cache, hardware (+ expand_env_vars)
├── config_models_analysis.py   # Source admission and the expected seconds per clip
├── config_models_auth.py       # Authentication config model (basic, OIDC, header)
├── config_models_automation.py # Running unattended: trips, automation, notifications, upload
├── config_models_llm.py        # LLM provider settings (shared by analysis and titles)
├── config_models_network.py    # The three third-party hosts a run may reach; all off by default
├── config_models_render.py     # What the video looks like: defaults, output, title screens, photos
├── config_models_server.py     # UI server bind settings + secure-by-default host rule
├── config_models_soundtrack.py # Music under a memory: local library, MusicGen, ACE-Step
├── generate.py                 # End-to-end generation orchestrator
├── generate_clips.py           # Clip extraction, probing, cleanup
├── generate_delivery.py        # Immich upload + delivered/pending/failed run state
├── delivery_timestamp.py       # The day a memory is filed under: its last picture, in the zone most share
├── generate_downloads.py       # Parallel asset downloads
├── generate_music.py           # Music resolution, AI generation, audio mixing
├── generate_photos.py          # Photo rendering, budget allocation, clip merging
├── generate_privacy.py         # GPS anonymization, fake names/cities, trip titles
├── generate_progress.py        # Adapters from pipeline progress to caller-supplied callbacks
├── generate_settings.py        # Assembly/title settings, assembler creation, music, upload call
├── generate_timeline.py        # Final-duration validation + content budget guards
├── pinned_models.py            # One digest-pinned artifact table: `models fetch` and the inference service both read it
├── filename_builder.py         # Output filename generation
├── timeperiod.py               # Date range utilities
├── security.py                 # Input sanitization, secret files, credential fingerprints
├── locked_file.py              # file_lock(): one writer at a time on a bank file several runs rewrite
├── i18n.py                     # Internationalization
├── i18n_places.py              # Country names in the film's language (CLDR, offline)
├── place_names.py              # Offline island boxes and short island/region names
├── place_name_translations.py  # Island and region names for the languages whose titles take no preposition
├── place_phrases/              # Per-language trip-title place phrases, one module per language; none = no preposition
├── locales/                    # gettext catalogues for the fourteen film languages
├── preflight.py                # Dependency checks
├── preflight_network.py        # One row per outside host the config allows; silent when none
├── preflight_render.py         # Authenticated worker version and render capability check
├── preflight_run.py            # Pinned models + writable output dir; `generate`/`prepare` and the web
│                               # Cut (`memory_run.install_refusal`, before the pool loads) refuse to start
├── preflight_homebase.py       # Trip setup checks that read no library and expose no coordinates
├── logging_config.py           # Logging setup
└── _version.py                 # Auto-generated by hatch-vcs (do not edit)
```

## Key Classes & Their Relationships

### Independent reader work

`editorial_reader_concurrency.py` bounds model-reader jobs and copies the run's
cancellation and metrics context into each worker. Each job owns a text judge
and audit directory; completed call records are merged in source order.
`editorial_block_votes.py` commits vote-bank updates on the caller thread.
`editorial_story_reading.py` overlaps independent monthly readings. The story planner
uses prepared captions inside each funded story's shortlist without another model
inventory. `editorial_prompt_pages.py` bounds the remaining text request pages.
Prompts and judgment identities do not include the concurrency setting.

How many jobs overlap comes from `llm_providers.reader_concurrency`, which reads
the endpoint when `llm.reader_concurrency` is unset: one job for a model on this
machine or this private network, four for a hosted one. `llm_single_flight.py`
keeps the bank's deduplication under concurrency, so two jobs carrying the same
judgment key cannot both pay for it. `provider_failure.THROTTLE` is the shared
pause a 429 puts on every reader at once, and `retry_wait` spreads each caller's
own wait so they do not retry in lockstep.

### Pipeline Flow (story-first)

Videos and photos are one pool, and the editor cuts from it:

```
generate / Memory page Cut
  └── build_smart_pipeline(editorial_context)           (editorial_runtime.py)
        └── SmartPipeline.run_editorial_source()        (smart_pipeline.py)
              └── RuntimeEditorialPlanner.plan_source()
                    ├── EditorialAttempt: lease + status.private.json   (operations/editorial_attempt.py)
                    ├── source model: fetch_full_window_source -> prepare_editorial_source
                    ├── "Reading dates, places and people": prepare_editorial_annotations
                    ├── TextEditorialPlanner.plan_prepared             (editorial_orchestration.py)
                    │     ├── "Reading event evidence": episode reader + cull
                    │     ├── "Building editorial cards": build_moment_cards -> moment wall
                    │     └── "Editing the memory": plan_structure -> select_story_first
                    ├── "Validating selected source timing": bind_editorial_timeline
                    └── project_source_rendering -> render-projection.private.json
              └── PipelineResult with editorial_selections  (editorial_projection.py)
```

### Assembly Flow

```
VideoAssembler.assemble()
  ├── AssemblyEngine resolves target resolution and transitions
  └── AssemblyEngine → streaming assembly → FFmpeg execution

VideoAssembler.assemble_with_titles()
  ├── TitleScreenGenerator → title/month/ending screens
  ├── assemble() → main content
  └── AudioMixerService → background music
```

## Configuration

- `Config` (config_loader.py): loaded from `~/.immich-memories/config.yaml`, tiered YAML (see above)
- `AssemblySettings` (assembly_config.py): video assembly parameters
- `PipelineConfig` (smart_pipeline.py): the per-run switches the editorial route reads

## Data Flow

```
Immich API → Asset models → ClipExtractor → VideoClipInfo
  → SmartPipeline.run_editorial_source → EditorialSelection (asset, interval, render mode)
  → ClipWithSegment → VideoAssembler → final .mp4
```

## Configuration Tiers

Config is organized in 3 tiers (see `config_loader.py`):

- **Tier 1** (top-level YAML): `immich`, `defaults`, `output`, `audio`, `title_screens`, `cache`, `upload`, `trips`, `network`, `photos`
- **Tier 2** (under `advanced:` in YAML, `_TIER2_SECTIONS`): `analysis`, `speech`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`, `automation`, `notifications`, `triage`, `editorial`, `inference`
- **Tier 3** (internal): `scheduler`, `title_llm`

At runtime, all sections are flat fields on `Config` (e.g. `config.analysis`).
Both flat and nested YAML formats are accepted.

The tiers are a YAML layout, not a code layout. The section models are grouped by
domain across the `config_models*.py` modules (resources, analysis, render,
soundtrack, automation, llm, auth, server), and `Config` in `config_loader.py`
assembles them into one flat settings object. A file naming a key of the removed
clip scorer (`_REMOVED_CONFIG_KEYS`) or a removed section (`_REMOVED_TOP_LEVEL_SECTIONS`) loads:
the key is dropped with one warning naming it, never a crash.

## Render worker (S1)

`services/render-worker/immich_memories_render_worker/` owns the authenticated
versioned job API. `admission.py` names a job after its cut (`memory_key` plus
the binding digest) and refuses an envelope that drifted from the binding it
carries, before any byte is fetched. `jobs.py` serializes GPU work, validates
artifacts (one decode per film, reused when the renderer already decoded it),
records the film's SHA-256 and sends it as `Repr-Digest`, enforces the job deadline and sweeps scratch at boot; `store.py`
keeps atomic job transitions behind a repository contract ready for a future
PostgreSQL implementation, with a per-job JSON record so a restart can say a
render died with its process. `native.py` and `native_plan.py` adapt selected
cuts to the existing generator: a missing NVENC is a recorded degradation, a
changed selection is a refusal. The app hands a cut to it when `render.worker_base_url` is set
(`generate_render.py`, `processing/remote_render.py`; `preflight_render.py` checks the worker's
version and capabilities first); deployment files are `services/render-worker/compose.yaml` and
`kubernetes.yaml`.

## Conventions

- **Max file length**: 800 lines soft / 1000 hard (enforced in CI via `make file-length`)
- **Max complexity**: Xenon grade C (<=20 cyclomatic complexity, `make complexity`)
- **Cognitive complexity**: complexipy ≤15 per function (`make cognitive-complexity`)
- **Makefile**: Single source of truth for all commands (CI, pre-commit, CLAUDE.md)
- **Composition**: Top-level orchestrators compose service objects via constructor injection
- **Re-export shims**: Only in `__init__.py` — never in regular modules
- **No `_`-prefixed overflow files**: All files have descriptive names
- **Private helpers**: Prefixed with `_`, same package
- **Tests**: `tests/` directory, run with `make test`
- **Integration tests**: run manually with `make test-integration*` (per-suite folders under `tests/integration/`, see CLAUDE.md); also run on the self-hosted GPU runner. Not a pre-commit hook.
- **Real-Immich gate**: `make test-immich-gate` (`tests/integration/immich_gate/`: compose file, `seed.py`, `media.py`) runs on every PR against Immich v2 and v3 in Docker (`.github/workflows/immich-gate.yml`, required check `Immich Gate`); the pinned images ride in the Actions cache per version (`scripts/immich_gate_images.sh`, `make immich-gate-fetch`/`immich-gate-save`).
- **Pre-commit**: Run `make ci` before committing

The web sidebar links Memory, Suggestions, Runs, Media pool and Settings.
`ui/pages/suggestions.py` uses `AutoRunner`; `ui/pages/runs.py` reads `RunDatabase`
and the shared run index/storyboard. Neither page owns a separate job store.
