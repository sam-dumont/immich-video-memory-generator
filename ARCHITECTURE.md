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
it reads live in the store (`db/`, repositories in `store/`). The design is summarised in
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
and folds every trip's day episodes into one story per leg (`trip_legs.py`: a trip that changes
where it stays, such as a hike then a city, is two legs); each leg reserves
`round(slots / 2 * sqrt(leg days / film days))` pictures at its turn in the presence pass. After
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
keyframe reader), keeping the strongest frame: that is `det-v3`. A still's banked `det-v2` row is its
`det-v3` answer and `carry_still_exposure` (`editorial_preparation_model_facts.py`) banks it as one
before preparation counts what is owed, so an existing bank re-reads that head for videos only, and
videos send their sampled frames as well as their preview when inference is configured
(`editorial_preparation_remote_frames.py`). Completed aggregates retain local producer
identities; partial or mixed-artifact answers are never banked. A Live Photo's
clip is read the same way: it is no candidate, so `acquire_clip_companions`
(`editorial_preparation_model_facts.py`) reads it for the exposure head alone and banks it under the
clip's own id, and `load_detector_heads` puts those rows in the gate's `companion_detectors`, which
had been empty for every Live Photo. A flagged clip holds its still. The same sampled frames of a video
go through the `frame_kind` head (`prepare_clip_frames` in `editorial_preparation_heads.py`), and
`editorial_clip_frames.py` banks the clip's `clip_frames` fact: a clip that shows its moment in
fewer than three frames of four reads `frames=subject_often_missing` on its line, which the rules
reader scores 0 (a favourite still wins), and `StandingGate` refuses on every tier.
Before a retained Live carrier is certified, `live_source_integrity.py` checks the complete
presentation of its original companion bytes through `ProbeCache`. Sampled playback frames
are still semantic evidence; they do not prove that the original decodes completely. A
byte-proven decoder or presentation failure uses the existing `live-still` disposition,
keeping the selected photograph and member IDs. The carrier records the original SHA,
selected stream, decoder identity, presentation policy and refusal reason. Those verdicts
are cached by original-byte identity; unavailable downloads or tools remain failures.
This happens before sealing, and certified Live rendering continues to fail closed.
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
- **Person presence**: in a film about people, a person is present in every picture of an episode
  where Immich recognised their face at least once; `AND` asks for every named person somewhere in
  the episode, not in one frame. Decided once, by the fetch, by face ID over the window Immich
  returns (`api/person_scope.py`, one read per kind per window: `fetch_media` in the CLI,
  `_fetch_media` in the wizard). The pool the owner reviews holds those pictures (marked
  "Same episode", `AppState.found_by_episode`) and the cut keeps that answer: an evidence
  exclusion removes a picture for its own reason, never its neighbours' presence. Never past
  the episode (`person_presence.py`).
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

`store/caption_selection.py` chooses complete description/setting pairs for an explicitly
selected LLM caption identity, preferring valid banked SmolVLM pairs. Fact reads, missing-fact
checks and provenance use the same choice. Default SmolVLM reads retain their exact producer.
`editorial_preparation_motion.py` owns `MotionScope`, motion acquisition and bank reads. Its
producer-specific reads reuse described SmolVLM motion lines before requesting a new LLM line;
unchanged sources retain their existing bank entries. `prepare` acquires descriptions; film-time
evidence preparation reads banked motion or falls back to plain clip facts. Missing motion
remains in the preparation report but does not block required-fact completeness.

- **Producer**: anything that writes a fact about a picture: the caption server, the heads, the
  detectors, the motion and pixel readers (`editorial_preparation*.py`). Film preparation runs
  active picture facts before the rules draft, then captions and clip inspection for that draft
  and actual replacement candidates (`editorial_film_preparation.py`). Bulk `prepare` keeps its
  explicit whole-source scope. Deferred video exposure is never banked as a completed frame check.
  The first draft and its source gate use a caption-free annotation view, even when descriptions
  are already banked. That view has a distinct evidence contract. Refinement and demanded episode
  context retain the original caption producer; the bank is never erased to build the draft.
- **Heads**: eight small linear classifiers over one pinned DINOv2 ONNX embedding: location,
  people, children, activity, venue, frame_kind, screen, uncovered_person
  (`triage/bundled_heads/public-8heads-v4.npz`, `editorial_preparation_heads.py`). Beside them sit
  two detectors, `nsfw_marqo` (exposure) and `doc_docling` (documents)
  (`editorial_preparation_detectors.py`). `EditorialConfig.active_head_versions` filters both
  out when tier-owned `detectors_enabled` is false on NAS. Raw `head_versions`, banked rows and
  producer identities stay unchanged; GPU/Full reuse matching detector facts or fill missing ones.
  Saved review and automatic permanent holds remain, since old exposure holds lack producer attribution.
- **Tiers**: `tier: auto` resolves `nas` (CPU heads), `gpu` (adds Marqo, Docling, captions and Laya),
  or `full` (adds an explicitly configured prose LLM). NAS and GPU always use the rules reader.
  Text-only titles and music mood may use a configured LLM on every tier; they neither enable
  model selection nor image captioning. Explicit `caption_provider: llm` lets preparation use
  that LLM for missing still and motion captions, without promoting NAS to Full. Config and
  preflight warn about cost; the rules draft defers these requests to selected/candidate refinement.
  CLI and film preparation pass the configured provider; motion provenance records its origin.
  LLM preflight checks configured text services too.
  Preparation follows that same product tier; legacy overrides no longer win. `config_compute.py`
  checks inference-service health and local CUDA/MLX capability without loading weights.
  Explicit tiers remain available for comparisons. Sharing never asks the prose
  LLM (`config_tiers.py`, `config_models_editorial*.py`, `editorial_shareability_tiers.py`).
  `laya_checkpoints.py` selects platform-matched archive, path and threshold defaults;
  `pinned_models.py` owns the SHA-256 pins used by `models fetch` (the encoder, the detector
  exports, Laya, and the WordNet corpus free-text requests are read with).
- **Reach**: the pictures a film can actually select (for a person film, its person presence),
  plus their Live Photo siblings and capture runs. This bounds cheap preparation; captions
  and playback have the narrower selected/candidate scope. The rest of the window is read as
  Immich metadata (`editorial_film_reach.py`). Each acquisition is recorded under the attempt's
  `refinement/<sequence>/preparation.private.json`, with requested IDs and producer timings.
- **Fill on demand**: a film reads only the episodes its shots sit in, and banks them; reading a
  whole scope ahead is optional (`prepare --overviews`). A model-tier film left short by S seconds
  reads at most 2 * ceil(S / 3.5) more unread episodes (`episode_demand.py`,
  `editorial_thin_short.py`).
- **Bank / banked**: an answer stored under its exact inputs and producer identity, so the next
  run asks nothing and a changed asset invalidates only its own rows. The main ones:
  the store's annotation tables (`store/`), episode readings, accounts, cut measurements
  (`store/cut_measurements.py`), judgments (`cache/judgment_cache.py`), the thesis-fit and
  memory-worthy vote banks (`store/vote_banks.py`) and the audience bank (`store/audience_bank.py`).
  No row means nobody asked, never "measured nothing". Two runs write them at once (the pipeline
  lock covers assembly only): every bank writes in short store transactions, and audience holds
  reach the store in batches of up to 500 pictures, each merged with the store's copy in one
  transaction under locks taken in sorted order, so the stricter hold always stands.
  Private database creation is exclusive. Existing files are chmodded without opening and
  closing an extra descriptor, which would release live SQLite connections' POSIX locks.

**Building the cut**

- **Rules draft**: the film the no-model reader cuts (`editorial_rule_reader.py`,
  `editorial_structure_planner.py`). It may read what a model already banked about the library,
  which can only tighten it (`editorial_rule_banked_facts.py`).
- **Carrier**: the picture admitted to carry one chosen moment of a funded story, if it is free,
  in context and spaced from the shots already committed (`editorial_story_carriers.py`,
  `editorial_carrier_eligibility.py`). A carrier is a shot before it is rendered.
  Exact-version head confidence travels with structured annotation facts. Corroborated document
  evidence joins the shared source exclusions before family seats or refills; favourites and
  required pictures bypass this extra check. Rendered annotation text and its cache identity stay
  unchanged.
- **Picture admission**: `PictureAdmission` (`editorial_picture_admission.py`) owns the shared
  standing, audience, spacing and candidate repetition checks. Draft selection, thin swaps,
  audience replacements, duplicate refills and family seats use it. Later candidates acquire
  their bounded facts before standing is refreshed; the private admission record names each
  result. `editorial_carrier.py` binds every candidate to its own story context. Story allocation
  still owns depth and recovery; these are explicit exceptions, never inherited by a refill.
  Batch readmission seats representatives before accepted depth, then restores chronological
  order. An earlier depth picture cannot consume the spacing slot of the representative it
  supplements; standing, audience and duplicate checks still apply to both.
- **Standing**: does a picture stand by itself, and may it serve as context inside its story.
  Answered on every tier from the facts, never asked of a model (`editorial_standing_facts.py`: two
  points tables, heads alone or heads plus the ingest caption; a caption naming an animal, or a
  person Immich found a face for, is never refused; `face_evidence` reads the faces), and applied
  by `StandingGate` (`editorial_story_standing.py`). An album handed over with a written subject
  (`generate --from-album --subject`, `EditorialIntent.pool_is_subject`) is a curated pool: its
  pictures stand on the subject whatever their score, every year it holds gets a shot, the filler
  pass skips it, and `stood_on_subject` in the story-selection record lists what got in that way.
- **Look-alike / scene print**: a story's next picture is kept only if it adds to the ones already
  kept (`editorial_story_lookalike.py`). The final review drops repeats by perceptual hash and by
  scene print, the pooled DINOv2 vector of a preview, which catches the same scene in another
  framing (`editorial_final_hash_review.py`, `editorial_scene_prints.py`).
  The rules draft carries its explicit starred-twin collapses into refinement. Final invariant
  checks follow that history only to a keeper still in the film; missing keepers and cycles
  remain violations.
- **Block vote**: the shape of every model yes/no. At most 12 rows, asked twice, in source order
  and in a hashed order; picked both times is firm, once is a maybe (`editorial_block_votes.py`).
- **Thin layer / thin polish**: model mode's editing when `thin_model_layer` is on (the default).
  Separate date windows also draft from rules and demand episode context afterwards. The model
  reads the finished rules draft once and proposes replacements through the same gates. A vote-named
  shot stays until a replacement passes its final fit check; gate-refused shots stay excluded. Budget: 4
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
  (`editorial.strict_sharing`, on by default; applied per film, never banked). The activity question is answered only by **Laya**, a local 0.4B
  text classifier over the compact ingest caption (`editorial_laya_reader.py`,
  `editorial.laya_audience`, off by default, Apple Silicon). The rules route runs it too when
  captions are prepared. Sharing never asks a prose LLM: detector/exposure flags hold without
  further review, and an unanswered caption stays with the family. Answer banks distinguish
  Laya from the rules check. `editorial_shareability_tiers.py` selects this policy independently
  of whether the film uses prose or polish.
- **Picture evidence is banked**: the eight heads prepare facts before the rules draft. GPU and
  Full also prepare Marqo and Docling; their first draft retains that tier's detector policy.
  Caption and clip producers acquire missing evidence for selected shots and actual candidates;
  matching banked evidence is reused. A wider preparation scope requires an explicit `prepare`
  job. The prose reader is text only, and so is music: its mood comes from the cut's thesis, story titles and
  captions (`audio/text_mood.py`), else the clips' own mood, else `calm`. `refuse_pictures` in
  `tests/no_pictures.py` wraps the one dispatch every model request passes through and fails on
  any request carrying a picture; `tests/test_editorial_demanded_previews.py` holds the production
  route to that, cold and warm, and `tests/test_audio_no_pictures.py` holds the music path to it
  and fails any `images=` argument in `audio/`, `processing/`, `titles/` or the music command.
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
two detectors and Demucs over HTTP (`/ping`, `/health`, `/queue`, `/facts`, `/audio/stems`) in its own image with its own device
variant (`docker/Dockerfile.inference`, `docker/hwaccel.inference.yml`). It imports the app's
triage engine and detector module rather than reimplementing them, which is what keeps a fact
computed there identical to one computed in process; two import-linter contracts hold the
direction of that dependency and keep the UI and CLI out of it. The service is not a distribution:
the image puts it on `PYTHONPATH`, and `pythonpath` in `[tool.pytest.ini_options]` does the same
for the suite. Its `queue.py` bounds classifier waits and admits one call per producer without
occupying native workers while waiting; `/queue` reports aggregate counts and timings. Its `seeding.py` fills a cold cache volume from the app's `pinned_models.py` table,
so a fresh PVC needs no `kubectl cp`. Design:
`docs/implementation-plans/2026-09-11-phase5-inference-service.md`.

The optional `immich_memories_inference.gpu_worker` entrypoint runs both existing service
lifespans, mounts authenticated rendering at `/render`, and proxies `/v1` to the CUDA image's
bundled caption process. `gpu_phases.py` admits model phases around the existing queues and
retains active native work through client cancellation. The existing render thread waits with
a deadline, unloads classifier weights and stops captions before rendering; Demucs releases
after each separation. `caption_runtime.py` bounds subprocess start/stop, and `caption_proxy.py`
streams responses without forwarding render credentials. `services/inference/compose.gpu-worker.yaml`
reserves one GPU and publishes one port. Standalone service entrypoints and external ACE-Step
remain unchanged.

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
  - `cpu_video.py`: synthesizes two Pillow plates once; FFmpeg fades and encodes them on CPU-only/NAS hosts
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
- `generate_saved_cut.py`: render a saved cut or revision (CLI `runs render`, web export) from the
  cut's own `render-inputs.private.json` (`processing/render_inputs.py`, written by every cut)
  through `operations/revision_render.py` (owner edits incl. recorded-sibling swaps) → `generate_memory`
- `processing/source_preparation.py`: bounded completion queue with worker-owned clients;
  `generate_clips.py` gives each source its own scratch directory and restores editorial order.
  `DownloadCoordinator.sources_for` shares downloaded components across workers by source ID.
  `processing/memory_budget.py` sizes the pool when `source_prepare_workers` is `auto`: one worker
  per 3 GiB after a 1 GiB parent reserve, at least 1, at most 2 and never more than the CPUs.
  The cgroup limit wins over physical RAM. Worker-local encoder budgets divide the remaining
  memory by the actual worker count; they do not alter the subsequent assembly budget.
  The same budget caps each assembly decode's FFmpeg `-threads` (one per 2 GB, 1 to 4).
  It also sets libx265's `rc-lookahead` above 1080p (5 up to 3 GB, 10 at 4-5 GB, default from 6 GB)
  through `clip_encoder.encoder_args_for_plan` and the photo clip encoder. Bounded x265 encodes
  use one frame thread, as required by the measured lookahead memory curve.
  Below 3 GB with no hardware HEVC encoder, `output_canvas` renders an `auto` 4K film at 1080p.
  NAS tier caps the shared photo/assembly canvas at 1080p even for an explicit 4K request;
  lower requested resolutions remain unchanged.
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
│   ├── accounts.py             # open_accounts: one /users/me-verified client per selected account (#1500)
│   ├── access_clients.py       # AccessBoundClient: the run's client; reads each routed picture (details,
│   │                           # preview, original, motion, playback) through its owner's account, kept
│   │                           # open until the run ends; uploads stay primary. AccountReadFailed names it
│   ├── compatibility.py        # Immich API-version compatibility policy (v2/v3 resolution)
│   └── models.py               # API data models (Asset, Person, etc.)
│
├── photos/                     # Photo-to-video animation (converts stills to .mp4 clips)
│   ├── __init__.py             # Public API re-exports
│   ├── renderer.py             # Frame-by-frame renderer: Ken Burns, face_aware_pan, render_split (parked)
│   ├── animator.py             # Photo source prep: HEIC decode, downscale cap, HDR detection
│   ├── photo_pipeline.py       # Render one photograph as a Ken Burns clip, streamed to FFmpeg
│   ├── encoding.py             # Verified photo encoder; NAS H.264/SDR on hardware without HEVC
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
│   ├── editorial_film_preparation.py # NAS-first acquisition and live fact views for selected/candidate refinement
│   ├── annotation_line_fields.py # Which parts of a picture's line are its content and which we wrote; content rules read only the first
│   ├── editorial_film_reach.py # What a film prepares: its demanded pictures, their Live families and capture runs
│   ├── person_presence.py      # Who a person film may select: every picture of an episode its people are recognised in
│   ├── person_resolution.py    # A name or UUID -> faces: the store's aliases per read account, else the roster
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
│   ├── editorial_picture_admission.py # Shared candidate preparation and standing, audience, spacing and repetition checks
│   ├── editorial_carrier.py        # A playable picture bound to its own story context
│   ├── editorial_thin_vote.py      # One closed thesis-fit vote over the whole cut, in balanced blocks,
│   │                               # source order first, the hashed order only where it decides
│   │                               # rows carry close family relations; a relative's only shot is held
│   │                               # and so is a year's only shot (voice_per_partition); a year keeps one;
│   │                               # and a story's only texture shot (editorial_shot_kinds)
│   ├── editorial_shot_kinds.py     # Portrait or texture, off the frame/people/activity heads; the kind mix
│   ├── editorial_thin_pages.py     # What a seat is offered: motion first, records first, the refused moment first
│   ├── editorial_thin_short.py     # A short cut reads ≤2·⌈S/3.5⌉ unread episodes of shot-less stories;
│   │                               # only a story whose reading records a moment gets a seat
│   ├── editorial_thin_refill.py    # Which seats open; each picks from 12 rows first, then only the picks
│   │                               # meet the gates, and a refused pick is picked once more; a removal's
│   │                               # seat takes its freed seconds, and refill pages lead with the lacking kind;
│   │                               # a vote-named shot's refill comes from another moment
│   ├── editorial_laya_onnx.py      # Portable Laya tokenizer and batched ONNX scorer (CUDA or CPU).
│   ├── editorial_laya_reader.py    # Laya answers the activity question from compact captions;
│   │                               # cached answers include checkpoint contents, runtime and threshold.
│   │                               # Enabled for gpu/full tiers by editorial.laya_audience; the sharing
│   │                               # question never goes to an LLM: what Laya leaves, heads + rules decide
│   ├── library_catalogue.py    # The account of a month/year (or a multi-year window: one per year
│   │                           # plus one over them), written over banked episode readings
│   │                           # (plus the no-model facts of episodes a cut did not read), keyed by
│   │                           # them plus the model that wrote them
│   ├── catalogue_runtime.py    # Who writes one: `prepare --overviews` over a window, and a film run
│   │                           # over a month, year or window the library has no account of yet
│   ├── episode_demand.py      # The draft reads the period from facts; only the episodes its shots
│   │                          # sit in are read by the model, when the polish layer asks for the account
│   │                          # Latest demanded-reading availability reaches the final private plan;
│   │                          # unresolved readings also warn in the trace, recovered ones stay banked
│   ├── editorial_home_radius.py    # Where home is, and whether captures sit inside its radius
│   ├── editorial_shareability_tiers.py  # Audience evidence policy for reduced preparation tiers
│   ├── editorial_review_list.py    # The finished cut's shots in the detector's 0.2-0.5 grey zone that
│   │                               # nothing else holds; a list for the owner, never a gate
│   ├── editorial_exposure_chains.py # A five-minute capture run half flagged, with 3+ flagged in it,
│   │                                # is held whole: the hold the detector's per-picture answer misses
│   ├── editorial_preparation*.py   # Annotation preparation: captions, public heads, detectors, pixel facts,
│   │                               # motion lines (one caption-seat sentence per video, read by
│   │                               # the pick).
│   │                               # _previews.py fetches, verifies and caches the preview every
│   │                               # stage reads. Every broad per-picture handler here lets
│   │                               # AccountReadFailed through (test_account_read_escapes.py).
│   │                               # _model_facts.py plans who answers each model producer;
│   │                               # _detector_frames.py samples a video's eight frames for the
│   │                               # exposure head, through the motion line's keyframe reader
│   ├── selection_source*.py    # The canonical source model: admission, provenance, groups, invariants
│   ├── household_source.py     # A run naming its accounts (`EditorialRunContext.accounts`) reads the
│   │                           # window per account, keeps chosen owners only, tags `Asset.access_accounts`
│   │                           # and routes the run's AccessBoundClient; `HouseholdWindows` is the same
│   │                           # read for person presence in discovery; the kept copy of each exact-copy
│   │                           # group and its account are frozen in the attempt's source snapshot, and
│   │                           # `runs render` reads that copy through that account
│   ├── text_episode_reader.py  # Reading event evidence (paged, banked); the same reading names
│   │                           # each episode's notable moments, which the polish layer seats and protects
│   ├── text_episode_prompt.py  # What that reading is asked, and what it may take a name from
│   │                           # (a film's on-demand reading asks the lean form: no Cull, one representative)
│   ├── prose_shapes.py         # The JSON shape each prose seat asks for (response_format json_schema)
│   ├── text_episode_paging.py  # Its request limits: an episode cut into pages, pages packed into prompts
│   ├── editorial_album_index.py # Album names by asset, one listing + one read per album, once per run
│   ├── editorial_story_*.py    # Story reading, weighing, slots, shortlist, carriers: the story planner
│   ├── editorial_story_trips.py     # Detected trips become one story per leg, with a reserve for each leg's length
│   ├── editorial_story_lookalike.py # A story's further picture is refused when it repeats one it holds
│   ├── editorial_story_depth.py     # A short film's free slots as verified-different frames inside shown moments
│   ├── editorial_story_trim.py      # The allocation in reverse when the production budget is tighter
│   ├── editorial_story_threads.py   # A recurring activity at one place is one story per era, if the reader agrees
│   ├── editorial_same_kind.py       # No-model: dense same-label stories at one place in one partition share one story's depth
│   ├── editorial_event_story.py     # No-model: a dense, distinct one-off inside a home story is its own event story
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
│   ├── trip_legs.py            # Where a trip changes where it stays: areas of stay become legs (#1563)
│   ├── trip_place.py           # Names a trip at the scale its pictures cover (city → country)
│   ├── place_geocoder.py       # Opt-in Nominatim: district names per ~1 km cell, cached in the store
│   ├── trip_discovery.py       # Shared UI/CLI all-asset discovery, including year-boundary trips
│   ├── special_day.py          # Every run of activity, and a found day named from its own lines
│   ├── special_day_sequence.py # Days read a month at a time in order (close family by role on each line); 30 s film floor
│   ├── prepared_captions.py    # Exact-producer caption reads for music and special-day text calls
│   ├── special_day_title.py    # What a day may be called: the grounding guard, the re-ask, the fallback
│   ├── album_source.py         # Album mode: the album is the candidate pool, nothing is searched for
│   ├── source_filter.py        # Drop doorbell / dashcam / screen-recorder uploads by filename
│   ├── source_quality.py       # Drop messaging re-encodes: sub-1080p with no camera EXIF
│   ├── exact_copies.py         # Same SHA-1 + kind under distinct UUIDs: one item (a Live copy with its motion, favourite, primary owner, ids); a video equal to a Live Photo's motion folds into the Live Photo; any member's star stars the kept item
│   ├── picture_copies.py       # One picture stored as several files: fold, keep the most pixels
│   ├── llm_failures.py         # Separate "the model could not answer" from a bug in the calling code
│   ├── request_heartbeat.py    # RequestHeartbeat: periodic log line for long-outstanding HTTP calls
│   ├── duplicate_hashing.py    # Perceptual hashing for duplicates
│   ├── thumbnail_prefetch.py   # cached_preview_bytes(): the one preview reader the editorial modules share
│   ├── apple_vision.py         # macOS Vision framework face detection (smart crops)
│   ├── apple_vision_image.py   # Vision image conversion helpers
│   ├── llm_query.py            # The live transport: one prompt, one connection, one answer
│   ├── llm_adaptations.py      # Capability refusals: remembered request changes, schema/object fallback
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
│   ├── film_timeline.py        # measure_film_timeline(): content + regular title seconds + map extra on top
│   ├── map_move_timing.py      # MapMoveTiming: 6-8 s map moves by distance, eased flight + 2 s still hold
│   ├── location_card_route.py  # location_card_moves(): when a trip card appears and where it flies from
│   ├── title_inserter.py       # TitleInserter: title screen concatenation
│   ├── title_background_renderer.py # TitleBackgroundRenderer: pre-renders the clip a title reveals into
│   ├── title_divider_planner.py # TitleDividerPlanner: month/year/location divider cards
│   ├── audio_mixer_service.py  # AudioMixerService: background music mixing
│   ├── privacy_audio.py        # Privacy mode audio processing (lowpass filter)
│   ├── clip_caption.py         # The per-clip date/place caption: text and geometry, no decoding
│   ├── caption_image.py        # Captions drawtext cannot draw (non-Latin scripts), rendered with the title fonts
│   ├── frame_sampling.py       # One cached still-frame sampler for title colours and previews
│   ├── playback_keyframes.py   # A playback's index and a few keyframes by byte range, decoded from a sparse copy
│   ├── frame_preview.py        # Frame extraction for previews
│   ├── hdr_utilities.py        # HDR detection & conversion filters
│   ├── scaling_utilities.py    # Resolution, aspect ratio, smart crop
│   ├── ffmpeg_runner.py        # FFmpeg execution with progress
│   ├── hardware.py             # Hardware detection (GPU, encoders)
│   ├── hardware_detection.py   # Hardware detection backends
│   ├── hardware_encode.py      # VAAPI/QSV device init + hwupload for built commands
│   ├── rate_control.py         # CRF -> per-encoder constant-quality flags
│   ├── live_geometry.py        # Shared burst canvas, with a 1080p limit on NAS intermediates
│   └── live_photo_merger.py    # Live merging; measured transfer conversion and NAS hardware policy
│
├── audio/                      # Audio processing
│   ├── mixer.py                # Audio mixing & ducking
│   ├── mixer_class.py          # AudioMixer class
│   ├── mixer_helpers.py        # Mixing helper functions
│   ├── mood_analyzer.py        # Music mood vocabulary + VideoMood (no picture reads; mood is text_mood's)
│   ├── music_generator.py      # AI music generation orchestrator
│   ├── cut_music_preview.py    # The track a saved cut would get, before rendering it (`music preview`)
│   ├── music_generator_client.py # Music generation client
│   ├── music_generator_models.py # Music generation data models
│   ├── music_sources.py        # Music source providers (local library)
│   ├── text_mood.py            # Banked music judgment from saved cut text; private answering-route record
│   ├── generated_audio.py      # Decode and reject silent/non-finite generator output before fallback
│   ├── music_pipeline.py       # Multi-provider pipeline (ACE-Step -> MusicGen fallback)
│   ├── bundled_music.py        # The 28 bundled royalty-free tracks (`music` extra), used with no backend
│   ├── track_tempo.py          # Measure a bundled track's tempo (numpy onset autocorrelation, no librosa)
│   ├── beat_grid.py            # Ask the generator for a tempo whose beat divides the photo cut cadence
│   ├── mastering.py            # Loudness + high-shelf pass so a generated track sits under video
│   └── generators/             # Music generation backends
│       ├── base.py             # MusicGenerator ABC + StemSeparator Protocol
│       ├── factory.py          # Generator factory
│       ├── memory_budget.py    # Will this ACE-Step profile fit in currently available memory?
│       ├── cgroup_memory.py    # Linux process/ancestor limits and conservative reclaimable file cache
│       ├── cuda_usability.py   # Tiny kernel and synchronization before automatic audio CUDA routing
│       ├── musicgen_backend.py # MusicGen API (generation + remote Demucs stems)
│       ├── ace_step_backend.py # ACE-Step lib/API (mode choice, captions, REST protocol)
│       ├── ace_step_runtime.py # ACE-Step in-process handlers: device, MLX/torch memory, one render
│       ├── ace_step_isolated.py # Local subprocess using the installer's separate audio environment
│       ├── ace_step_captions.py # Dense caption templates
│       ├── inference_demucs.py # Owned inference HTTP stems, optional local fallback
│       └── demucs_local.py     # Local Demucs stem separation (in-process)
│
├── titles/                     # Title screen generation (film_title.py: explicit, model or template title, any surface)
│   ├── generator.py            # TitleScreenGenerator (composes 3 services)
│   ├── rendering_service.py    # RenderingService: GPU/CPU renderer selection
│   ├── ending_service.py       # EndingService: fade-to-white ending
│   ├── trip_service.py         # TripService: trip map + location cards
│   ├── _text_memory_types.py   # Memory type title helpers
│   ├── _trip_titles.py         # Trip title text generation
│   ├── convenience.py          # Convenience/factory functions
│   ├── cpu_video.py            # CPU/NAS still plates, FFmpeg text fades and plan-owned encoding
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
│   ├── map_animation.py        # Satellite map fly-over and location-card flights (van Wijk zoom)
│   ├── trip_stops.py           # group_trip_stops(): intro pins grouped within 25 km, every pin named
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
│   ├── run_people.py           # `--accounts` and `--person`/`--people-expression` through the people store
│   ├── config_cmd.py           # `config`, `years`, `preflight`
│   ├── people_cmd.py           # `people` scan/show
│   ├── models_cmd.py           # `models fetch`
│   ├── prepare_cmd.py          # `prepare`
│   ├── auto_cmd.py             # `auto suggest/run/history/status/install/test-notification`
│   ├── special_days_cmd.py     # `discover-days`, `days-due`, `days-export`/`days-import`: the days worth a memory
│   ├── store_cmd.py            # `store status/import/copy/backup/restore`
│   ├── titles.py               # `titles test`, `titles fonts`
│   ├── runs.py                 # `runs list/show/story/why/stats/storage/delete`
│   ├── pictures_cmd.py         # `pictures show/clear-hold/never-use/undo/list`: the owner's word on one picture
│   ├── music_cmd.py            # `music search/analyze/add`
│   ├── hardware_cmd.py         # `hardware` info display
│   ├── capabilities_cmd.py     # Setup report and optional real local music profile tests
│   ├── _helpers.py             # Shared console/print utilities
│   ├── _generation_preview.py  # Plain-text summary for read-only generation planning (--dry-run)
│   ├── _config_errors.py       # Config error formatting
│   ├── _flags.py               # Shared validation for flags more than one command takes
│   ├── _pipeline_runner.py     # Run SmartPipeline over the fetched assets + generate
│   ├── _editorial_context.py   # CLI flags + presets -> one EditorialRunContext
│   ├── _run_timeline.py        # The run's timeline: selection budget, then the settled plan
│   ├── _asset_fetch.py         # What a memory asks Immich for: videos, Live Photos, stills
│   ├── _album_generation.py    # Album mode: an Immich album is the candidate pool; a CuratedPool
│   │                           # (generate --ask) is read by asset id in its place
│   ├── _ask_generation.py      # generate --ask: model tier required, translate against the store,
│   │                           # print + save the trace, then a RunScope (pool as album, or special day)
│   ├── runs_render.py          # `runs render`: a saved cut or revision → generate_saved_cut
│   ├── _trip_generation.py     # Trip detection, selection, per-trip generation
│   ├── _trip_display.py        # Trip table formatting & selection logic
│   ├── _date_resolution.py     # Date range resolution for memory types
│   ├── _generate_display.py    # Params table + result printing for `generate`
│   └── _live_display.py        # Rich Live interactive progress display
│
├── web/                        # The web server: the Svelte client, its /api/v1, health, trigger, sign-in (#1395)
│   ├── server.py               # create_app(): FastAPI + session cookie + auth middleware; `immich-memories ui` runs it
│   ├── app.py                  # mount_web(): the /api/v1 routers + the built client under /app
│   ├── session.py              # GET /api/v1/session: who is signed in, and which sign-in the login page offers
│   ├── auth.py                 # Who gets in: credential check, rate limiter, bypass paths, session helpers
│   ├── auth_oidc.py            # OIDC client (authlib starlette integration, singleton)
│   ├── health.py               # GET /health, /health/live, /health/ready: probe payloads + snapshot cache
│   ├── trigger.py              # POST /api/trigger: runs what `auto run` decides, 202 + status URL
│   ├── reverse_proxy.py        # Secure cookie + trusted X-Forwarded-* settings for uvicorn
│   ├── runs.py                 # GET /api/v1/runs[/{id}[/child-output]]: RunDatabase, run index, transcripts
│   ├── cut.py                  # /runs/{id}/cut (storyboard + trace + polish + siblings), /story, /revisions
│   ├── pool.py                 # /runs/{id}/pool and /pictures/{id}/decision (Never use, Clear hold)
│   ├── media.py                # /assets/{id}/thumbnail (shared cache), /video (Range-streamed), /people/{id}/face
│   ├── i18n.py                 # GET /api/v1/i18n: the browser's ui.po as JSON, and the languages offered
│   ├── brief.py                # CutBrief: generate's flags 1:1 → argv, and the command shown to copy
│   ├── jobs.py                 # JobRunner: the CLI as a child process; records/logs under cache/web-jobs
│   ├── job_routes.py           # POST /cuts (generate --no-render), /runs/{id}/renders (runs render),
│   │                           #   /runs/{id}/music-preview, /music uploads, /roster/scan,
│   │                           #   /jobs/{id}[/events|/cancel|/output] (SSE progress), /runs/{id}/film
│   │                           #   /ask (tier: full?), /ask/preview[/{id}] (generate --ask --dry-run
│   │                           #   --ask-trace: the translation as JSON); the film is a /cuts brief with `ask`
│   ├── library.py              # GET /people, /albums, /trips, /special-days for the brief's pickers
│   ├── connection.py           # /connection: the Immich URL + key saved to the database; never follows a new URL
│   ├── suggestions.py          # /suggestions: what `auto suggest` offers, generate one as `auto run` would
│   ├── roster.py               # /roster: the store's people registry (roles, relationships) for People
│   ├── settings.py             # /settings: every setting + its source, saved to the database; /caches
│   ├── schemas.py              # Pydantic response models = the contract (openapi.json)
│   ├── dependencies.py         # Config, thumbnail cache, Immich fetches; overridable in tests
│   ├── openapi.json            # Generated (make web-api); web/src/lib/api-types.ts comes from it
│   ├── static/fonts/           # The title fonts the client previews with
│   └── client/                 # Generated SvelteKit build (make web-build), gitignored: the wheel and image build it
│
├── tracking/                   # Run history & telemetry
│   ├── run_database.py         # RunDatabase: run history in the store (pipeline_runs, phase_stats)
│   ├── run_database_rows.py    # Store row <-> RunMetadata/PhaseStats conversion
│   ├── phase_rows.py           # advance_phase(): forward-only phase log on a run or attempt row
│   ├── run_lifecycle_errors.py # Refused lifecycle transitions and their diagnosis
│   ├── run_tracker.py          # Pipeline run tracking
│   ├── run_id.py               # Run ID generation
│   ├── models.py               # Run/phase data models
│   └── system_info.py          # System info collection
│
├── db/                         # The store (#871): one versioned database on SQLite or PostgreSQL
│   ├── __init__.py             # Public API: open_store, Store, upsert, to_db/from_db, migrations
│   ├── bootstrap.py            # StoreLocation: env > config.yaml `database:` > sqlite:///~/.immich-memories/store.db;
│   │                           # redact_url (a URL is never logged with its password)
│   ├── engine.py               # create_store_engine: SQLite pragmas + explicit BEGIN, psycopg 3 pool,
│   │                           # schema_translate_map (symbolic `immich_memories` -> None / the PG schema)
│   ├── store.py                # Store (.begin/.connect/.schema), open_store: one engine per location, upgraded on open;
│   │                           # on_first_open hooks (run once per store per process, outside every lock);
│   │                           # unmigrated_store for status/backup
│   ├── migrate.py              # Alembic driven in code: upgrade/downgrade under pg_advisory_lock or
│   │                           # fcntl + BEGIN IMMEDIATE; pending_changes, migration_schema
│   ├── migrations/             # env.py, script.py.mako, versions/ (shipped in the wheel; alembic.ini is dev only)
│   ├── metadata.py             # The shared MetaData(schema="immich_memories") and naming convention
│   ├── tables/                 # One module per domain's Table objects: store_meta; people (people_registry,
│   │                           # people, people_aliases, people_relationships; 0002_people; the alias's
│   │                           # Immich account, null = primary, 0009_people_alias_accounts); settings
│   │                           # (0003_settings); annotations.py (asset facts, captions, heads, pixels, faces,
│   │                           # cut measurements, motion lines, owner decisions in asset_flags) and
│   │                           # model_answers.py (judgments, Cull verdicts, episode readings/refusals,
│   │                           # library overviews; 0004_annotations); operations.py (pipeline_runs,
│   │                           # phase_stats, automation_attempts, notification_health, asset_scores,
│   │                           # run_attempts, special_days; 0005_operations); banks.py (audience answers
│   │                           # and holds, block vote entries, owner review edits; 0006_banks); places.py
│   │                           # (geocoded_places: opt-in reverse-geocode answers per cell;
│   │                           # 0007_geocoded_places); timing.py (run_spans, run_diagnostics;
│   │                           # 0007_timing). Both 0007s grew from 0006_banks; the empty
│   │                           # 0008_merge_timing_geocoded joins them into one head
│   ├── legacy_import.py        # ImportOutcome: what one domain's import_legacy(store, home) did; the
│   │                           # `legacy_import` records in store_meta (read_/write_import_record)
│   ├── inventory.py            # row_counts, present_counts, recorded_revisions, digests: order-free,
│   │                           # backend-neutral per-table content digests (copy checks, restore drill)
│   ├── copy.py                 # copy_store: every table into another store (SQLite <-> PostgreSQL),
│   │                           # batched, one target transaction, serial sequences advanced, digests compared
│   ├── backup.py               # backup_store / restore_store + Manifest: VACUUM INTO / pg_dump -Fc -n on one
│   │                           # snapshot; restore swaps the file or pg_restores the schema, migrates, checks counts
│   ├── status.py               # store_status: revision, head, import record, counts, size; never migrates
│   ├── sqlite_files.py         # connect_sqlite: the one raw sqlite3 factory (WAL, busy_timeout 30 s,
│   │                           # synchronous NORMAL, foreign keys), private_database_path (0600)
│   ├── network_guard.py        # Refuses a SQLite file on NFS/SMB/CIFS unless IMMICH_MEMORIES_ALLOW_NETWORK_SQLITE=1
│   ├── leases.py               # Lease: fcntl lock file on SQLite, pg_try_advisory_lock on PostgreSQL
│   │                           # (automation, PipelineLock, editorial attempt; works across hosts)
│   ├── upsert.py               # upsert(): dialect insert().on_conflict_do_update / do_nothing
│   └── time.py                 # to_db / from_db: naive UTC in the store, aware UTC at the edge
│
├── cache/                      # Local caches and the store-backed banks
│   ├── __init__.py             # Re-exports public API
│   ├── judgment_cache.py       # Reasoning-mode LLM verdicts, keyed by the exact prompt asked (store table `judgments`)
│   ├── editorial_verdicts.py   # Cull's standing per-picture verdicts (store table `editorial_verdicts`)
│   ├── embedding_cache.py      # HeadFactStore: head answers (store table `head_facts`)
│   ├── thumbnail_cache.py      # File-based thumbnail storage
│   ├── thumbnail_sizes.py      # The sizes the grid and avatars ask for, and the downscale to them
│   ├── disk_budget.py          # LRU-by-mtime eviction that holds a cache directory to a size cap
│   └── video_cache.py          # Downloaded video file cache
│
├── store/                      # Repositories over the store's annotation tables: every banked fact and reading
│   ├── caption_provenance.py   # What served each caption (served /models row + control digest), grouped
│   ├── motion_lines.py         # The motion line per video, keyed by picture, producer and source digest,
│   │                           # with what produced it (question, keyframes, admitting residual)
│   ├── library_overviews.py    # Read-only: the library's own account of a period, written by cataloguing
│   ├── library_catalogue.py    # The only writer of that table: content-addressed period accounts
│   ├── owner_decisions.py      # The only writer of the owner's per-picture decisions (clear hold,
│   │                           # never use): `source='owner'` rows in `asset_flags`, one per picture
│   ├── cut_measurements.py     # What a cut measures and banks: a Live Photo's motion residual, a
│                               # clip's speech regions and a Live burst's companion clock offsets,
│                               # keyed the same way (a missing row is "not measured", never
│                               # "measured as nothing")
│   ├── legacy_annotations.py   # import_legacy(store, home): annotations.sqlite + judgments.db, read-only,
│   │                           # keys kept, idempotent; the only reader of those files; verify_legacy
│   ├── legacy_imports.py       # The import registry (people -> annotations -> operations -> banks; one per domain):
│   │                           # run_import (resumable, per-importer fingerprint records), verify_import,
│   │                           # import_on_first_open (the CLI/UI enable it after the config loads; a lease
│   │                           # makes concurrent starts import once)
│   ├── legacy_verify.py        # verify_rows: every legacy key in the store with equal values
│   ├── audience_bank.py        # The audience bank's rows: answers by answerer + evidence key, hold slots
│   │                           # (permanent / text) per picture, merged a batch per transaction
│   ├── vote_banks.py           # VoteBank: a block vote bank (memory-worthy, thesis-fit) per case key;
│   │                           # save() writes only the entries changed since the last save
│   ├── owner_edits.py          # The owner's review edits before a render, kept whole per edit id and
│   │                           # read back by attempt (`runs why`)
│   ├── legacy_banks.py         # import_legacy(store, home): the structure-banks/ JSON files and
│   │                           # <film>.owner-edits-<id>.private.json, read-only, idempotent;
│   │                           # verify_legacy (owner edits exact, holds at least as strict)
│   └── batches.py              # id_in/in_chunks (one array parameter on PostgreSQL, IN slices under
│                               # SQLite's bind limit); bank_rows/upsert_rows: one transaction per batch.
│                               # Producers bank in batches (PendingHeadFacts, PendingMeasurements,
│                               # judgment_cache's shared bank): a crash costs at most one batch
│
├── triage/                     # The pinned DINOv2 ONNX encoder and its eight context heads
│
├── people/                     # The library's people graph (counts and dates, no pixels)
│   ├── signatures.py           # Tiers, onset, twins, duplicates, dyads, owner curve pairing
│   ├── graph.py                # build_graph(): Immich roster + co-occurrence -> PeopleGraph
│   ├── companion.py            # The people registry's writers (scan, confirm, add, relate), each one
│   │                           # store transaction under the registry row lock; confirmed beats inferred
│   ├── registry_store.py       # The registry document <-> the people tables (the only code that knows the rows)
│   ├── account_ids.py          # A person's ids: one flat list (primary account) or one list per account
│   ├── transfer.py             # people export/import (validated, ids kept) and import_legacy(people.yaml)
│   ├── evidence_graph.py       # ~/.immich-memories/people-graph.json: scan measurements, a derived file
│   ├── expression_window.py    # The earliest day a people condition can hold, from birth dates
│   └── editor.py               # The companion editor's model: the registry as rows, and back
│
├── free_text/                  # A film asked for in a sentence (#1436, experimental; design in
│   │                           # docs/designs/free-text-memories.md, user page docs-site/docs/make/free-text.md). Only reading.py and the
│   │                           # model's picks in linking.py, subject.py and pool_questions.py reach an LLM
│   ├── __init__.py             # The package API (the pool, the reading and the CLI build on it)
│   ├── lexicon.py              # Lexicon Protocol; load_wordnet(): the pinned WordNet 3.0 zip that
│   │                           # `models fetch` writes (free_text.wordnet), digest-checked, read through
│   │                           # nltk; never downloaded at run time. Noun files, plurals, people words,
│   │                           # young people, time periods, roles through their kinds, verb bases,
│   │                           # adjectives, derived nouns, relatives() (own kinds/parts, inherited parts),
│   │                           # synonyms() (the first sense's other everyday names)
│   ├── reading.py              # read_request(): the model picks who/when/where/what from an enum of the
│   │                           # request's own n-grams, 3 field orders, 2-of-3 token votes, where+what
│   │                           # voted as content; choose() (one option, 3 orders), choose_several()
│   │                           # (a list, 2-of-3 per option); Asker Protocol and WireAsker
│   │                           # (llm_query.query_llm with the answer's json_schema, through the async
│   │                           # bridge so it also answers under a running loop)
│   ├── translate.py            # translate(): reading -> link_who -> link_when -> build_subject ->
│   │                           # link_where (Subject.heads) -> link_facts -> build_pool, as an Ask
│   │                           # (Translation + Pool); household_of(): people file, owner, homes
│   ├── handoff.py              # film_for(): one occasion of one day (the pool's found day, a one-day
│   │                           # date range, or the model's voted "one day or longer") -> special day,
│   │                           # the model picking between the catalogue's occasions that day; else the
│   │                           # pool as the film's whole reach with the request as written subject;
│   │                           # "not possible" -> no film
│   ├── rule_preview.py         # preview_rules(): a dry run asks the editor's rules about the pool
│   │                           # before render, through the run's own functions (source pass, screen
│   │                           # gate, never_auto, carrier sources, video frames); count + hashed ids
│   │                           # per rule; at-cut rules (audience, look-alikes, spacing) named only
│   ├── trace.py                # explain(): READING/WHO/WHEN/WHERE/WHAT/FACTS/POOL/RULES/VERDICT/FILM;
│   │                           # save_with_run(): the run's diagnostics["free_text"] (report builder)
│   │                           # and free-text-trace.private.txt in the attempt directory; the report's
│   │                           # vocabulary (name parts -> role, places, OCR words) and the marks basis
│   │                           # (pool, OCR anchors, admitting step, words read). save_picks(): the
│   │                           # clips generate_memory() was handed
│   ├── marks.py                # marked(): `report --wrong/--missing` on a free-text run: each photo's
│   │                           # admitting stage and pick; missing words read/offered/picked/in the pool
│   ├── printed.py              # ImmichPrintedText: the PrintedText port on Immich's /search/metadata
│   │                           # `ocr` filter (the store banks no OCR text)
│   ├── subject.py              # subject_words(): the head noun per coordinated part of the what-spans
│   │                           # (time phrase cut; people, picture words never), -ing activities add
│   │                           # WordNet's derived nouns, "X making" is X. build_subject(): candidates
│   │                           # = those words + own kinds/parts the captions use; people words only when
│   │                           # said or formed from the request; the model votes the main subject (2-of-3,
│   │                           # fallback: the request's words); a quality stays when the model says it
│   │                           # narrows AND captions say it; own parts/kinds are the subject, inherited
│   │                           # parts only for a place. Subject.heads feed link_where; subject_kind()
│   │                           # (the model's place/animal/thing/activity vote)
│   ├── pool.py                 # build_pool(Translation, LibraryView, ...): the funnel, each Step keeps a
│   │                           # count and a Reason: when; who (a face in the picture's 90-min episode);
│   │                           # printed text (PrintedText port = Immich OCR: anchors' episodes replace
│   │                           # where); where (scopes.py) or Immich's place names; kind of picture
│   │                           # (photographs and videos unless a kind is named); sharpness; computed
│   │                           # selections (farthest trip, first/last per frequent person, faces over N);
│   │                           # one undated occasion's day; subject by caption grammar (main + extent +
│   │                           # other names, minus left-out); company from captions. Verdict possible /
│   │                           # thin (<12) / not possible, naming the filter that emptied it. No model
│   │                           # looks at a picture
│   ├── pool_questions.py       # Text-only votes the pool asks, each gated by grammar: left_out (only what
│   │                           # follows a negation), other_names (WordNet synonyms + caption subject-slot
│   │                           # words the model picks; quality carried), one_particular_place (GPS
│   │                           # required), printed_words (for OCR), one_occasion (a plural is many)
│   ├── scopes.py               # in_place(): at a home (150 m), home of the picture's time, near it (home
│   │                           # radius), or trips detected per home; no GPS stays unless one place
│   ├── linking.py              # Code links the spans: link_who (I = the owner for dates, never a face;
│   │                           # we adds the partner; names/roles need faces; plural people = company),
│   │                           # link_when (an age read as numbers, calendar in code; dates question only
│   │                           # with time words, years or people), link_where (one voted place per
│   │                           # phrase beyond the subject's nouns, widest of several), time_cut; each
│   │                           # decision keeps a Reason for the trace
│   ├── library.py              # read_library(): per picture, Immich's date/media kind/places/GPS from
│   │                           # annotation_assets, and the configured producers' caption, doc_docling
│   │                           # label, sharpness and people-file faces via AssetAnnotationFactRepository
│   ├── grammar.py              # Caption grammar: is_about (the subject up to the first verb), is_thing
│   │                           # (WordNet's noun file), free_tier (captions about the subject),
│   │                           # subject_head (the caption subject's head noun)
│   ├── facts.py                # link_facts(): request words -> places, picture kinds, the sharpness line,
│   │                           # faces over N, first/last/farthest; first_pictures (the onset, never
│   │                           # before birth), last_pictures, home_trips (trips per home of the time),
│   │                           # farthest_trip, occasion_day; LibraryFacts.placed/of_kind/sharp_enough
│   └── homes.py                # homes_over_time(): each year's most-photographed ~200 m cell, a new
│                               # home past 300 m; the configured home base when none shows; Home.held_on
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
│   ├── state_store.py          # Automation attempts in the store; failure streaks for backoff
│   ├── status.py               # Cooldown gate + read-only AutomationStatus contract
│   ├── delivery_retry.py       # Durable state for one pending delivery retry
│   ├── notification_state.py   # Durable, sanitized notification delivery health (store row id 1)
│   ├── catalogue.py            # The special-days catalogue in the store: load/save, entries, scope
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
│   ├── cut_progress.py         # Where a run is, as one record the page and the terminal both read;
│   │                           #   read_latest_attempt/live_progress_of: any process reads a cut's progress
│   ├── run_index.py            # A run id resolved to its attempt directory (store table run_attempts);
│   │                           #   record_cut_run: `generate --no-render` keeps its cut as a run
│   ├── store_import.py         # import_legacy(): cache.db run/automation/score tables, the by-run
│   │                           # index and special-days.json into the store, read-only, idempotent
│   ├── candidate_fates.py       # Saved pool outcomes + decision-log reader shared with runs why
│   ├── cut_review.py           # The model polish record per shot (swaps, protections, refused offers)
│   ├── cut_revisions.py        # Owner edits to a saved cut (incl. pool additions) as numbered revisions
│   ├── revision_render.py      # A revision projected onto the cut's render inputs, as the render reads it
│   ├── storyboard.py           # A saved cut as shots in playback order, their intervals and moment siblings
│   ├── story_view.py           # The stories a cut tells, heaviest first, from plan.private.json
│   ├── picture_holds.py         # What holds a picture + the owner's decision, for the pool, storyboard and CLI
│   ├── caption_origins.py      # One picture's caption origin, and the run's distinct-origin line
│   ├── phases.py               # OperationalPhase / PhaseEvent: stable outer lifecycle
│   └── storage_report.py       # build_storage_report(): output + cache storage inventory (`runs storage`)
│
├── planning/                   # Media-aware duration planning
│   ├── auto_duration.py        # decide_memory_duration(): Auto length fitted to discovered media, CLI and UI
│   └── memory_length.py        # default_duration_for_type(): the length a memory type asks for, one resolver for every surface
│
├── config.py                   # YAML configuration management (re-exports)
├── config_loader.py            # Config loading: env > config.yaml > database > default (pydantic-settings sources)
├── config_sources.py           # describe_settings(): every leaf key's value (secrets masked), source and exact override
├── settings_store.py           # SettingsStore: the `settings` table, Fernet secrets under IMMICH_MEMORIES_SECRET_KEY;
│                               # load_stored_settings (bootstrap-safe, never via get_config)
├── settings_edit.py            # save_settings (the UI/CLI write path, database only), move_to_database (`config move-to-db`)
├── config_presets.py           # Named presets (`preset: fast`) that fill several knobs at once
├── config_tiers.py             # One resolved product tier: reader, preparation producers, Laya
├── config_compute.py           # Inference capability discovery, separate from video encoding
├── config_models.py            # Resources a run uses: Immich server, cache, hardware (+ expand_env_vars)
├── config_models_analysis.py   # Source admission and the expected seconds per clip
├── config_models_auth.py       # Authentication config model (basic, OIDC, header)
├── config_models_automation.py # Running unattended: trips, automation, notifications, upload
├── config_models_free_text.py  # free_text: where the pinned WordNet corpus lives and comes from
├── config_models_llm.py        # LLM provider settings (shared by analysis and titles)
├── local_inference.py          # App-owned llama.cpp lifecycle and shared local audio memory lease
├── local_reader_process.py     # Lifetime-pipe supervisor reaps the model after app crashes
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
├── model_bundle.py             # Bake all CUDA small-model weights into an immutable image directory
├── filename_builder.py         # Output filename generation
├── timeperiod.py               # Date range utilities
├── security.py                 # Input sanitization, secret files, credential fingerprints
├── locked_file.py              # file_lock(): one writer at a time on a file several processes rewrite
│                               # (the SQLite migration lock, the place-name cache)
├── i18n.py                     # Internationalization
├── i18n_places.py              # Country names in the film's language (CLDR, offline)
├── place_names.py              # Offline island boxes and short island/region names
├── place_name_translations.py  # Island and region names for the languages whose titles take no preposition
├── place_phrases/              # Per-language trip-title place phrases, one module per language; none = no preposition
├── locales/                    # Fourteen languages: messages.po for films, ui.po for the interface
├── preflight.py                # Dependency checks
├── setup_capabilities.py       # ACE-Step profile memory advice and synthetic audio validation
├── preflight_network.py        # One row per outside host the config allows; silent when none
├── preflight_render.py         # Authenticated worker version and render capability check
├── preflight_run.py            # Pinned models + writable output dir; `generate`/`prepare` and the web
│                               # Cut (`memory_run.install_refusal`, before the pool loads) refuse to start
├── preflight_homebase.py       # Trip setup checks that read no library and expose no coordinates
├── preflight_accounts.py       # One row per extra Immich account (`immich.accounts`), no key printed
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

`llm_providers.structured_output_enabled` selects schema enforcement from the named request:
local `episode_reading` requests retain prompt-only JSON to avoid the measured decoder stall,
while free-text, title and account schemas remain enforced. Explicit endpoint settings win.
OpenAI-compatible and Ollama transports use the same decision; text judgment keys carry the
effective schema and model identities version the request policy so stale answers are not reused.

### App-owned local reader

`LLMConfig.enabled` gates every request. An enabled reader with a blank `base_url` uses
`local_inference.LocalModels` on Linux or macOS; a URL forwards to the configured dialect.
The managed process binds loopback with a private API key and starts lazily. A small
`local_reader_process` supervisor watches the app's lifetime pipe and stops the native process
group even when the app crashes or receives SIGKILL; ordinary shutdown reaps the supervisor too. Its bank identity
uses model/projector SHA-256 digests and context length, independent of its ephemeral port.
`models fetch` installs the pinned default Gemma GGUF and projector; custom paths stay explicit.

A process-wide lease serializes requests across event loops and hands memory to local ACE-Step
and Demucs only after the reader process group has stopped. Audio drops its pipeline and native
allocator caches before releasing that lease. Cancellation drains native work before unlocking;
external API servers are never unloaded by this lifecycle. The next read restarts the model.

### Pipeline Flow (story-first)

Videos and photos are one pool, and the editor cuts from it:

```
generate / Memory page Cut
  └── build_smart_pipeline(editorial_context)           (editorial_runtime.py)
        └── SmartPipeline.run_editorial_source()        (smart_pipeline.py)
              └── RuntimeEditorialPlanner.plan_source()
                    ├── EditorialAttempt: lease + status.private.json   (operations/editorial_attempt.py)
                    ├── source model: fetch_full_window_source (per account: fetch_household_source)
                    │     -> prepare_editorial_source
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

- `Config` (config_loader.py): env > `~/.immich-memories/config.yaml` (tiered YAML, see above) > the
  store's `settings` table > defaults. `config.yaml` is operator-owned: the app writes it only for
  `config move-to-db`. The UI and `immich-memories config` save through `settings_edit.save_settings`,
  which refuses keys env or the file override and bootstrap keys (`database.*`). The database source
  is opened from env + the file's `database:` block only, so it never recurses into `get_config()`.
  `config_sources.describe_settings` is the per-key source report the settings page and
  `config show` render.
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

- **Tier 1** (top-level YAML): `tier`, `preset`, `immich`, `defaults`, `output`, `audio`, `title_screens`, `cache`, `upload`, `trips`, `network`, `photos`
- **Tier 2** (under `advanced:` in YAML, `_TIER2_SECTIONS`): `analysis`, `speech`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`, `automation`, `notifications`, `triage`, `editorial`, `inference`
- **Tier 3** (internal): `title_llm`

At runtime, all sections are flat fields on `Config` (e.g. `config.analysis`).
Both flat and nested YAML formats are accepted.

These YAML tiers are not the product `tier` (`config_tiers.py`): `nas` (inexpensive CPU classifiers), `gpu`
(every light model, no LLM) or `full` (plus an enabled local or server LLM). The product
tier owns `editorial.reader`, `editorial.preparation.tier` and `editorial.laya_audience`.
`auto` resolves from inference capability and the configured LLM; conflicting legacy preparation
settings are ignored with a notice. A save stores only the keys that changed, so an automatic
config never becomes a machine-specific pin. Internal metadata-only component fixtures remain
available without exposing a fourth product tier. The real-Immich gate uses NAS with pinned CPU models.

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
- **Free-text evaluation**: `tests/free_text/prompts/*.json`, one recorded prompt per file (the request, the model's banked answers keyed by a phrase of their question, the expected spans/links/subject/funnel/pool/verdict/film); `test_prompts.py` translates each against the invented household in `eval_library.py`, the model faked once in `banked.py` (`recorded()`)
- **Integration tests**: run manually with `make test-integration*` (per-suite folders under `tests/integration/`, see CLAUDE.md); also run on the self-hosted GPU runner. Not a pre-commit hook.
- **Real-Immich gate**: `make test-immich-gate` (`tests/integration/immich_gate/`: compose file, `seed.py`, `media.py`) runs on every PR against Immich v2 and v3 in Docker, each with the store on SQLite and on PostgreSQL (`IMMICH_GATE_DATABASE`; `.github/workflows/immich-gate.yml`, required check `Immich Gate`); the pinned images ride in the Actions cache per version (`scripts/immich_gate_images.sh`, `make immich-gate-fetch`/`immich-gate-save`).
- **Launch check per backend**: `make launch-check-ci` (SQLite) and `make launch-check-ci-postgres` (each launch workspace gets its own schema in `IMMICH_MEMORIES_E2E_DATABASE_URL`); CI job `Hermetic Launch Check (sqlite|postgresql)`. `scripts/with_throwaway_postgres.sh` starts the throwaway `postgres:16` for this, `make test-store` and the gate.
- **Container e2e**: `make test-container` (`tests/container/`, marker `container`) builds the image and runs it from `docker-compose.yml` on a legacy volume: first-start import and `store import --verify`, `store backup`/`restore` in the image, the trigger API called by the CronJob's curl. `CONTAINER_E2E_DATABASE=postgresql` switches on the compose file's PostgreSQL example; CI job `Container E2E (sqlite|postgresql)`.
- **CI scope**: `scripts/ci_scope.py` (`make ci-scope`) sorts a pull request's diff into docs, code, store and container areas; each job in `ci.yml` and `immich-gate.yml` reads that in its `if:`. Build files, workflows, unknown paths and the release run everything. Required checks are the rollups `CI Success` and `Immich Gate`.
- **Pre-commit**: Run `make ci` before committing

The web sidebar links Memory, Suggestions, Runs and Settings. Every action in the client is the
CLI: a cut is `generate --no-render`, a render is `runs render [--revision N]`, a people scan is
`people scan`, a music preview is `music preview`, a sentence's preview is `generate --ask --dry-run`, each run by `web/jobs.py` as a child process
whose progress the page follows over SSE. The review page (`web/src/routes/runs/[run_id]`) reads
the saved cut (`operations/storyboard.py`, `cut_review.py`, `story_view.py`), keeps the owner's
edits as numbered revisions in the attempt directory (`operations/cut_revisions.py`), and renders
one through the same projection the CLI uses (`operations/revision_render.py`,
`generate_saved_cut.py`). The pool's ticks are the owner's last pass, saved as a revision too:
added pictures are made playable by `processing/added_material.py` (a Live Photo's motion stitched
through `motion_renderings`) and the film grows to hold them; nothing is selected again.
Suggestions use `AutoRunner`; nothing owns a separate job store.

**Web client (`web/` at the repo root, served from `src/immich_memories/web/client`).** SvelteKit
static SPA with `@immich/ui` (MIT; its logos and store badges are Immich trademarks, stripped at
build time and gated by `scripts/check_web_brand.py`). It talks only to `/api/v1`; the server
pages it replaced redirect to `/app/...`. The build is not committed (#1580): the release job
and both Docker images build it, `hatch_build.py` refuses a wheel without it, `make dev` builds it
for a checkout, and until then `/app` answers 503 naming `make web-build`. `make web-check` fails
on a stale OpenAPI contract or TS types, a client that no longer builds, or a shipped Immich brand
asset. Import-linter keeps
`immich_memories.web` from importing the CLI, and the core packages from importing the web
server. Labels are `t('...')`/`N_('...')` in Svelte and land in the `ui.po` catalogues
(`make ui-catalogues`).

## Run diagnostics

`tracking/timing.py` buffers context-local spans and logs. Preparation, reader, discovery and render
boundaries share it; worker pools propagate context. `run_observations.py` owns the CLI lifecycle from
before discovery through failure or completion. `span_store.py` persists spans and diagnostic context
through Alembic revision `0007_timing`, on SQLite or PostgreSQL. No span writes to the database.
`peak_memory.py` gives each span of a measured run its peak RSS, own and with child processes: the
`ru_maxrss` lifetime high at both ends, plus one sampler thread (libproc on macOS, /proc on Linux).

`tracking/report.py` allowlists diagnostic fields. `report_privacy.py` redacts the chosen strings and
assigns per-report salted IDs. `report_service.py` assembles the same report for `report` and the HTTP
endpoint; neither calls Immich or sends anything. `span_progress.py` reads the saved spans for normalized
rates and whole-run estimates. A first run has no historical total estimate.

**Provider conformance** (`conformance/`): synthetic feature probes use the production prompt,
transport and parser. The AST inventory discovers model-asking functions; HTTP observation
counts attempted calls, and runtime function observation verifies each declared path ran.
Uncovered sites fail the command and the inventory guard. Domain-specific case modules use
fresh temporary stores and synthetic evidence; optional private HTTP artifacts and incremental
usage reports make failures reproducible without reading the personal library.

### Detector cache compatibility

`store/detector_cache.py` inspects the frozen `detector-facts-v1` head bank, migrates proven
Marqo still equivalents without overwriting current answers, and explicitly refreshes selected
head/asset pairs. `cli/store_facts.py` exposes status and preview/apply maintenance under
`store facts`. No model runs in these commands. `tests/fixtures/detector-cache-v1.json` freezes
the payload keys, model pins, bundle, labels and sampling; app versions do not re-key facts.
