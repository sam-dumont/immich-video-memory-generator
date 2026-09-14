# Architecture Guide

Immich Memories turns sources from an Immich library into a video. This guide maps the
production route and its main boundaries. For contributor commands, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Selection and generation

Selection runs before rendering. The CLI `generate` command and the Memory page's **Cut**
build the same editorial pipeline:

```text
generate / Memory page Cut
  └── build_smart_pipeline(editorial_context)          analysis/editorial_runtime.py
        └── SmartPipeline.run_editorial_source()     analysis/smart_pipeline.py
              └── RuntimeEditorialPlanner.plan_source()
                    ├── prepare source metadata and missing annotations
                    ├── read event evidence and the period account
                    ├── build moment cards
                    ├── choose stories, moments and carriers
                    ├── validate selected source timing
                    └── project the plan into PipelineResult
```

The model reader and the rules reader share the source model, timing and rendering path.
The rules reader supplies factual episode cards and rule-based choices. Preparation tier
is a separate choice: the default `full` tier still needs image captions and local or remote
inference producers even with the rules reader.

`generate_memory()` in `generate.py` consumes the selected clips. It does not discover a
new pool when called without clips. It downloads selected originals, extracts or renders
the chosen intervals, assembles the video, adds music and optionally uploads the result.
`OperationalPhase` in `operations/phases.py` names the outer lifecycle: discovery, download,
analysis, selection, render, music, delivery and complete. Selection progress has more
detailed stages inside that lifecycle.

### Editorial boundaries

| Boundary | Responsibility |
|---|---|
| `editorial_runtime.py` | Build the production planner and coordinate source preparation |
| `editorial_runtime_ports.py` | `EditorialRuntimePorts`: provider and people-loader dependencies |
| `editorial_orchestration.py` | `TextEditorialPlanner`: episode reading, period account, cards and editing |
| `editorial_runtime_backend.py` | `ProductionPostCardBackend`: connect text orchestration to structure planning |
| `editorial_structure_contract.py` | `StructurePlannerPorts`: judgments; `StructurePlanningInput`: source, banks and audience |
| `editorial_rule_episodes.py`, `editorial_rule_reader.py` | Rules reader |
| `editorial_shareability_tiers.py` | Audience evidence available at each preparation tier |
| `editorial_source_route.py` | Bind the chosen rendering mode to the canonical source and Live Photo manifest |
| `editorial_projection.py` | Convert a plan to `PipelineResult` and report stages |
| `operations/editorial_attempt.py` | Durable attempt records and the process lease |

Attempts live under `<cache>/editorial-runs/<key>/attempts/<id>/`. The record includes the
stage, request state, plan, render projection and selection trace. The lease lets recovery
distinguish a running process from one that exited. Reusable facts and readings live in
`annotations.sqlite`; structure judgments and motion facts also use `structure-banks/`.
These records can contain private library information.

## Rendering

The main orchestrators compose services:

| Orchestrator | Composed services |
|---|---|
| `VideoAssembler` | `FFmpegProber`, `ClipEncoder`, `AssemblyEngine`, `AudioMixerService`, `TitleInserter` |
| `ImmichClient` | `SearchService`, `AllAssetsService`, `AssetService`, `PersonService`, `AlbumService` |
| `TitleScreenGenerator` | `RenderingService`, `EndingService`, `TripService` |

`SmartPipeline` delegates selection to its injected editorial planner. It does not score
or rank clips itself. Its per-run `PipelineConfig` carries `hdr_only`.

`assemble_streaming()` uses `make_decoder()` to decode a clip into normalized frames,
`FrameBlender` to write frames and blend transitions, and `StreamingEncoder` to pipe the
result into FFmpeg. This bounds decoded-frame buffering; output resolution still affects
memory use.

`encoding_plan.py` resolves one immutable output contract: codec, container, transfer,
encoder and quality settings. `output_contract.py` probes the finished file and publishes
it atomically. H.265 is the supported HDR output codec; H.264 and ProRes output use SDR.

Photo animation runs through `photos/photo_pipeline.py`. It streams Ken Burns frames to
FFmpeg; `renderer.py` uses the largest Immich face box to guide the pan when one exists.
Selected Live Photo motion follows the certified source manifest through
`processing/editorial_live_render.py`, without another alignment pass at render time.

Titles use Quadrants kernels when the isolated backend probe succeeds, including its CPU
backend. `KernelTitleRenderer` composes `ParticleField`, `TitleTextRenderer` and
`AnimatedBlur`. PIL supplies simpler animated titles when the kernel backend is unavailable.

The generation helpers separate I/O from orchestration:

| Module | Responsibility |
|---|---|
| `generate_downloads.py` | Download originals and render certified Live Photo material |
| `generate_clips.py` | Extract, probe and clean up video clips |
| `generate_photos.py` | Render selected photographs and merge them into the timeline |
| `generate_settings.py` | Resolve assembly and title settings |
| `generate_timeline.py` | Content budgets and final-duration validation |
| `generate_music.py` | Resolve music, generate a soundtrack, mix and duck it |
| `generate_privacy.py` | Relocate GPS and replace generated person/place fields in privacy mode |
| `generate_delivery.py` | Upload and record delivery state |

## Application packages

| Package | Start here for |
|---|---|
| `api/` | Immich requests, pagination, people and album resolution |
| `analysis/` | Source admission, annotations, editorial planning, trips and special days |
| `store/`, `triage/` | Annotation storage and the public inference heads |
| `people/` | People graph and the owner's confirmations in `people.yaml` |
| `photos/`, `processing/`, `titles/` | Media rendering |
| `audio/` | Soundtrack backends, bundled tracks and mixing |
| `memory_types/` | Offered types, preset factories and date windows |
| `automation/` | Candidate discovery, ranking, cooldowns, delivery retry and unattended runs |
| `operations/` | Attempts, cancellation, lifecycle and storage reports |
| `tracking/` | Run history and delivery records |
| `cache/` | Cache database, media downloads, thumbnails and disk budgets |
| `cli/` | Click command registration and CLI adapters |
| `ui/` | NiceGUI routes, authentication, settings and the Memory page |

Automation chooses which memory to request. The editorial planner chooses what appears in
that memory. These are separate decisions. `automation/in_process_scheduler.py` runs the
UI's daily timer; `system_scheduler.py` installs OS jobs for `auto run`.

The Memory page is routed by `ui/pages/memory.py`: brief, running cut, result or recovery.
`memory_story_data.py` reads the saved plan for the story view; `memory_storyboard.py`
reads the render projection. Review and export reuse helpers under `step2_*` and `step4_*`.
File names left from those screens do not define a second selection route.

## Inference service

`services/inference/immich_memories_inference/` is a separate top-level Python package
served in its own image. Its `/ping`, `/health` and `/facts` endpoints provide the image
encoder, six public heads and two detectors. It imports the app's triage and detector
implementations. The app calls the service over HTTP and must not import its package.

`docker/Dockerfile.inference` supplies the runtime; the image adds the service package to
`PYTHONPATH`. Tests add it through `pyproject.toml`. The service's `seeding.py` uses the
app's `pinned_models.py` artifact table to populate a cold cache volume. Device selection
for the encoder does not move every producer to that device: the two detector constructors
currently create CPU ONNX sessions.

## Configuration and dependency rules

`Config` in `config_loader.py` loads `~/.immich-memories/config.yaml` by default and applies
environment overrides. Public model fields live in `config_models*.py`; generation converts
them into `AssemblySettings` and the editorial context. See the schema-checked
[configuration reference](docs-site/docs/reference/config-reference.md).

`make arch-check` enforces the forbidden-import contracts in `pyproject.toml`:

- Core analysis, processing, titles, people, store, triage and operations must not import UI.
- Those packages and audio must not import CLI.
- The app must not import the inference service.
- The inference service must not import UI or CLI.

These contracts enforce specific directions, not every architectural convention. Constructor
injection and Protocol contracts make dependencies explicit. Files over 800 lines warn;
files over 1000 lines fail the file-length gate. Split by responsibility rather than moving
arbitrary lines into helpers.

The Makefile owns local checks. `make check` is a subset; `make ci` adds the broader local
gates. GitHub Actions also runs a platform matrix, security services, builds and browser
checks. Passing locally does not replace those results. Details are in the
[testing guide](docs-site/docs/contribute/testing.md).
