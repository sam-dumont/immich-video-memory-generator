---
sidebar_position: 0
title: Pipeline Overview
---

# Pipeline Overview

One `immich-memories generate` run (and one **Cut** on the Memory page, which runs the same
code) goes through the lifecycle `OperationalPhase` in `operations/phases.py` defines:
**discovery → download → analysis → selection → render → music → delivery → complete**. This
page traces it against the code and answers the two questions that matter when you are sizing a
machine or hunting a slow step:

- Where does the time go?
- Which stages have to run on this box, and which could run somewhere else?

## Current selection route

UI, CLI and scheduled memories use one story-first route with the FAMILY audience. It prepares
facts for the whole source period, reads stories and distinct moments, then allocates duration
and assembles the chosen material. Missing required facts stop selection; complete cached
producer results are reused. Provider setup is documented in
[Editorial annotation setup](../../deploy/configuration/editorial-preparation.md); how it decides
is in [The Curator](./the-curator.md).

```mermaid
flowchart LR
    source["Source metadata and previews"] --> facts["Exact-producer annotation preparation"]
    facts --> stories["Period stories and distinct moments"]
    stories --> choose["Duration allocation and picture choices"]
    choose --> render["Render, music and delivery"]
```

## Cost classes

Every stage below carries one of four labels.

| Class | Meaning |
| --- | --- |
| `local-only` | Needs this machine. Video decode, frame extraction, the picture encoder and detectors, FFmpeg assembly, encoding, Taichi title rendering. |
| `remotable` | An HTTP request to a model server: the caption model, the text model, music generation. Runs wherever you point the config: another box on the LAN, a GPU host, a hosted provider. |
| `network` | Immich API I/O. Bounded by your NAS and your LAN, not by CPU. |
| `cheap` | Pure Python over data already in memory or in SQLite. Milliseconds. |

## The six stages of a cut

These are the strings the run reports (the Memory page shows the current one beside its active
phase row, the CLI prints them), and what each costs.

| Stage | What runs | Class |
| --- | --- | --- |
| **Preparing source metadata** | The source model: every eligible source with its provenance, duration, Live companion and exclusion reason, fetched whole per window (`selection_source.py`). Then preparation, which reports `Preparing captions: n/N`, `public_heads`, `detectors` as it goes: one caption request per picture that has none, one encoder pass for the six context heads, the two detectors in their own interpreter, pixel facts. Complete facts are never re-produced. | `network` for previews; captions `remotable`; heads, detectors and pixel facts `local-only` (CPU) |
| **Reading event evidence** | Paged episode reading over the annotation lines (`text_episode_reader.py`), with the cull asked inside each episode's scope. Banked per group, producer and evidence key. | `remotable` (text model), `cheap` when banked |
| **Reading the period account** | The period read as an account with a thesis (`text_period_insight.py`), one bounded repair if the answer is malformed. Banked. | `remotable`, `cheap` when banked |
| **Building editorial cards** | One card per moment over the banked facts, rendered into the moment wall the planner reads (`moment_cards.py`, `editorial_moment_wall.py`). | `cheap` |
| **Editing the memory** | The structure planner and the story planner: the memory-worthy gate, the story weighing, the moment picks, the standing gate, the audience checks; each a text-model question banked by its exact request, and the gates asked in two orders. Motion is measured for the Live carriers that were chosen. | `remotable`; motion facts `local-only` |
| **Validating selected source timing** | The chosen intervals bound to their sources and the duration realised: requested seconds, content budget, selected, shortfall (`processing/editorial_timing.py`). | `cheap` |

Then the render, music and delivery phases below, which have not changed.

### What a cut leaves behind

Every attempt is durable under `<cache>/editorial-runs/<key>/attempts/<id>/`
(`operations/editorial_attempt.py`), and `<key>/latest-attempt.private.json` points at the newest.
Inside: `status.private.json` (the stage, the request, the outcome, the duration realisation; a
`.lease` beside it tells an interrupted run from a slow live one), `plan.private.json` (the
thesis, the stories with their weights, every carrier with its reason, which is what the Memory page's
story view reads), `selection-sheet.private.md` (the same, for a human), `render-projection.private.json`
(what shipped and each interval), `calls/` and `pre-planner-calls/` (every text-model request
and answer), `derived-decisions/` (the memory-worthy gate, the period story, the story selection,
one moment inventory per episode, the subject pool, the timing trim, the audience bank), `evidence-hashes.json` (per episode:
the evidence key its reading was banked under, and one SHA-256 per asset annotation line, ids
and digests only) with `evidence-lines.private.json` beside it holding those lines themselves.
The banks the next cut reuses are not in the attempt: they are in `annotations.sqlite` and
`structure-banks/` beside it.

Two cuts of the same period that land on different carriers are told apart from those hashes:
`scripts/replay_editorial_routes.py` diffs `evidence-hashes.json` between the newest attempt and
the accepted one and names the first episode whose evidence moved, with how many of its asset
lines changed.

### Where the time goes

A **cold** cut pays for every picture that has never been read (one caption request, one
encoder pass, the detectors), and for every text-model reading of a period nobody has cut before.
A **warm** cut over the same period finds all of that banked and is mostly the render. There is
no depth knob and no source-level shortlist: every eligible source is prepared, because a picture
the editor never saw is a picture it cannot weigh. (The shortlist that does exist is downstream
and cheap: once a story knows how many carriers it can fund, it looks at a bounded multiple of
that number rather than the whole pool. No model call is involved.)

The levers, in order of what they buy:

1. **Put the caption server where it is fast.** It is one HTTP request per picture
   (`editorial.preparation.caption_base_url`, up to `caption_concurrency` in flight), and it is the
   whole of the cold cost that is not the encoder.
2. **Put the text model where it is fast.** Every reading and every gate waits on it. The run
   summary prints its call count and time.
3. **Keep the cache.** Facts are per picture and per producer version; readings are per exact
   request. Clearing the cache directory turns the next cut cold again.

If *generation* feels slow (the part after the cut), none of the above helps. That is decode,
scale, blend and encode, and the only levers are a hardware encoder, a lower output resolution,
and fewer clips.

## Render

`generate_memory()` in `generate.py` takes over from the plan. It holds a file lock so two runs
cannot interleave, and it downloads the originals of the selected sources: nothing before this
point needed more than previews. Then:

- **Downloads the originals and trims each selected interval** with FFmpeg
  (`DownloadCoordinator` fetches in parallel; `analysis.download_workers` defaults to 3). Segment
  extraction *can* use hardware decode. A Live Photo chosen for its motion plays its video
  component; one chosen as a still is held.
- **Renders selected photos** frame by frame in Python. Ken Burns is one `cv2.warpAffine` per
  frame, two when the shot needs a blurred background behind it, at 30 fps for the seconds the
  editor granted: 120 frames per photo at 4 s. HEIC decode and Apple gain-map HDR reconstruction
  happen here too, and the source is capped at 1.5× the output size: at 4K, measured on a 24.5 MP
  HEIC, a 2.0× cap paid 0.63 s and 0.32 GB per photo for pixels its own resize then discarded.
- **Generates title screens**, using the Taichi renderer when Taichi initialises and PIL
  otherwise. Both encode with the same encoder the final video uses.
- **Assembles and encodes.** Two or more clips always go through the streaming assembler
  (`processing/streaming_assembler.py`): one FFmpeg decode process per clip at a time, crossfades
  blended with `cv2.addWeighted` into a single preallocated buffer, raw frames piped into one
  FFmpeg encode process. Memory stays flat regardless of clip count, which is what makes 4K output
  possible.

Encoder selection is mostly a real probe, not a capability listing: NVIDIA, then Apple, then QSV,
then VAAPI, and NVIDIA, QSV and VAAPI each have to successfully encode one 256×256 frame before
they are used. VideoToolbox is the exception: it is taken on FFmpeg's listing alone. If a
hardware encoder fails mid-run the whole encode is retried once in software with the same codec.

One thing to be clear about, because it changes what hardware helps: **assembly does
hardware-accelerated *encode* but not hardware-accelerated *decode*.** `FrameDecoder` builds its
FFmpeg command with no `-hwaccel` flag, and all scaling, padding, blurring and captioning in the
assembly path is software. A GPU speeds up the write side of assembly, not the read side.

## Music

`resolve_music()` in `generate_music.py` walks a fixed chain: an explicit `--music` file wins;
otherwise AI generation if a backend is enabled; otherwise a bundled track chosen by mood. A
generation failure falls through to bundled rather than aborting the run, and the run is told:
the substitution comes back as a warning on the finished artifact, so a dead backend shows up in
the UI and in the nightly notification instead of sounding like working music forever.

ACE-Step has two modes, and which one you pick decides the cost class:

| `advanced.ace_step.mode` | What runs |
| --- | --- |
| `api` (default) | HTTP POST to an ACE-Step server, poll every 3s. `remotable`. |
| `lib` | The model runs in-process on this machine: MLX on Apple Silicon, CUDA on NVIDIA, PyTorch CPU otherwise. `local-only`. |

`lib` falls back to `api` when the `acestep` package is not importable, and logs a line saying so.
MusicGen is HTTP-only. The code puts CPU-only generation at "8+ hours per song"; disabling the
ACE-Step language model (`use_lm`, off by default) is documented in-code as taking a 60 s track
from roughly 45 s to 17 s.

Mixing, ducking, mastering and muxing are FFmpeg, so `local-only`.

## Delivery

Optional upload back to Immich. A failure here is non-fatal: the video stays on disk, the run is
marked delivery-pending, and error strings are scrubbed of any configured secret before they are
logged.

## Stage reference

| Stage | What it does | Cost | Class |
| --- | --- | --- | --- |
| Asset search | One Immich query per date window | A few round-trips | `network` |
| Live Photo discovery | Search + pair still with video component | Extra round-trips | `network` |
| Preview fetch | Fill the preview cache for pictures without facts | One small GET per asset | `network` |
| Source gates | Provenance, name-based exclusions, the screen/document gate | Milliseconds | `cheap` |
| Captions | One compact caption per picture without one, from a 400 px tile | One HTTP round-trip per picture | `remotable` |
| Context heads | DINOv2-small ONNX + six public heads | One encoder pass per picture | `local-only` |
| Detectors | Sensitive-content and document-figure classifiers | Batches in a worker process | `local-only` |
| Pixel facts | One fixed JPEG recipe, thresholds | Milliseconds per picture | `local-only` |
| Episode reading | Paged text reading, cull inside each episode | One text call per page; banked | `remotable` |
| Period account | The thesis | One text call, one bounded repair; banked | `remotable` |
| Editorial cards | Cards and the wall | Milliseconds | `cheap` |
| Memory-worthy gate | remarkable / maybe / background per happening, two orders | Blocks of 12; banked | `remotable` |
| Story weighing | dominant / major / minor / glimpse / none, one table | One text call, up to two repairs; banked | `remotable` |
| Moment picks | Which moments tell a funded story | One call per funded story; banked | `remotable` |
| Standing gate | Does each picture stand by itself, two orders | Blocks of 12; banked | `remotable` |
| Audience checks | Who may see each candidate | Per candidate; banked | `remotable` |
| Motion facts | Sampled motion for chosen Live carriers | FFmpeg sampling per carrier | `local-only` |
| Timing | Intervals bound, duration realised | Milliseconds | `cheap` |
| Source download | Originals for the selected sources | Bounded by NAS and LAN | `network` |
| Clip extraction | FFmpeg trim and re-encode per clip | Can use hardware decode | `local-only` |
| Photo render | numpy/OpenCV frame loop, 30 frames per second granted | Plus HEIC decode and gain-map HDR | `local-only` |
| Title screens | Taichi GPU kernels, or PIL | Per-frame render plus an encode per screen | `local-only` |
| Assembly + encode | Streaming decode, blend, encode | Hardware encode, software decode | `local-only` |
| Output validation | `ffprobe -count_frames` on the finished file | A full pass over every frame | `local-only` |
| Music generation | ACE-Step or MusicGen | Seconds to minutes on a GPU; hours on CPU | `remotable`, or `local-only` in ACE-Step `lib` mode |
| Mix and master | FFmpeg mixing and ducking | Seconds | `local-only` |
| Upload back | POST the finished file to Immich | One large upload | `network` |

## The caches

| Cache | Location | Holds |
| --- | --- | --- |
| Annotation store | `~/.immich-memories/cache/annotations.sqlite` | Every fact per picture and producer; the episode, period, cull and judgment banks |
| Structure banks | `~/.immich-memories/cache/structure-banks/` | The memory-worthy and standing votes, thumbnail hashes, demanded motion |
| Attempts | `~/.immich-memories/cache/editorial-runs/` | One directory per cut, see above |
| Downloaded videos | `~/.immich-memories/cache/video-cache` | 10 GB, 7 days |
| Immich previews | `~/.immich-memories/cache/thumbnails` | 10 GB |
| Clip previews | `~/.immich-memories/cache/preview-cache` | 2 GB |
| Run database | `~/.immich-memories/cache.db` | Run history; the old scorer's analysis rows until they are removed |

Facts are keyed by producer version (`editorial.description_model`, `editorial.head_versions`,
`editorial.pixel_producer_key`): changing a version names a different fact generation, and the
next cut produces it. Readings are keyed by the exact request, prompt included.

## Seeing it for yourself

When the result is wrong, the useful question is which stage ate the pictures you expected.

```bash
immich-memories generate --year 2024 --trace-selection selection.txt
```

That writes a funnel (one row per pass, showing what went in, what came out, and how many
favourites survived each step), plus a matching `selection.json`. A pass that swallowed every
favourite is flagged. The attempt directory holds the rest: the plan with every reason, and every
question the text model was asked.

## Related pages

- [The Curator](./the-curator.md): how the editor decides
- [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md): the producers
- [Photo Support](./photo-support.md): animation modes and HDR handling
- [Audio & Music](./audio-and-music.md): the music backends
- [Hardware Acceleration](../../deploy/hardware/overview.md): what each encoder needs
