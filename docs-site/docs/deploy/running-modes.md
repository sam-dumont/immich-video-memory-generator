---
sidebar_position: 2
title: Running modes
sidebar_label: "Running modes and tradeoffs"
---

# Running modes and tradeoffs

Two settings decide what a deployment needs and what the cut loses. They are independent.

| Axis | Key | Values |
|---|---|---|
| Who reads the period | `advanced.editorial.reader` | `rules`, `model`, or `auto` (rules when `llm.model` is blank) |
| How much image analysis runs first | `advanced.editorial.preparation.tier` | `metadata_only`, `no_captions`, `full` |

Where the analysis runs is a third, smaller choice: in the app process (the default), or in the
optional [inference service](./installation/inference-service.md) on a CPU or CUDA box, switched
on with one key (`advanced.inference.facts_base_url`). The facts are the same rows either way, so
you can move the service, change its provider or turn it off without re-deriving anything.
Hardware encoders (Quick Sync, VAAPI, NVENC) only change the render; none of them runs inference.

On a cluster, set `advanced.inference.facts_concurrency` with it. It is 8 by default and the app
keeps that many `/facts` requests in flight. At 1, which is what the client used to do, a Job
against a T1000 on `no_captions` spent 42.7 minutes on 3,709 pictures: 0.69 s each, the same rate
a 133-picture scope got, so the wait was the round trip and not the card. A 13,552-picture month
would have cost 2.6 hours of facts before anything was selected. Match it to the service's
`REQUEST_THREADS` and give the pod the CPU for them. `prepare` prints what each side spent: the
`remote_facts` row carries the app's wall clock and a `service s/pic` column beside it.

## The reader

| `reader` | Needs | What you get | What you lose |
|---|---|---|---|
| `rules` | Nothing beyond the app | The ten standard memory types, cut from dates, places, favourites, known people and whatever image facts the tier produced. Repeatable: 72 runs across 12 cases, stable across hash seeds, zero model requests | No story thesis. Custom free-text subjects are refused. No model reranking, no Live Photo motion choice. It can drop an occasion, over-select repeated portraits on a trip, or let a mundane object take a slot in a month or year recap |
| `model`, local | A vision model with a 32k context on a machine you own. Graded on a 30B model at 4-bit, about 17 GB resident, oMLX on an Apple Silicon Mac with 32 GB | The full editor: the period read as a story, pictures weighed in words, a reason under every picture | Time and a second machine. One monthly selection with warm facts took 1,451.9 s on the local reader |
| `model`, hosted | An OpenAI-compatible endpoint and a key | The same editor, faster: the same monthly selection took 537.4 s on GPT-4.1 mini, about $0.256 in tokens | Your annotation text and 800 px picture tiles leave your network. One monthly is the only hosted price measured; do not extrapolate it to years or trips |

Rules and both model readers cost $0 in API fees except the hosted row. Electricity and hardware
were not metered.

## The preparation tier

| `tier` | What runs on every picture in scope | First pass, about ten thousand pictures on a Celeron J4125 NAS | What the gate can do |
|---|---|---|---|
| `metadata_only` | Previews and pixel measurements. No ONNX, no captions | Minutes | Nothing to judge with: every unit stays at family viewing, `sendable` export refused |
| `no_captions` | Pixels, the DINOv2 encoder with six context heads, the sensitive-content and document detectors | 3 h 41 min (1.23 s per picture) | Refuses what it would refuse on `full`. Cannot clear a unit: eight findings (bathing, toileting, medical procedures, identifying records and the rest) are only named by a description |
| `full` | All of the above plus one caption per picture from a 500M vision model | About four days (30.9 s per caption) | Everything. A sentence under each picture instead of the facts that funded it |

Facts are banked per picture and per producer. Changing tiers erases nothing, and a `no_captions`
library can add captions later, a month at a time. The second cut over a prepared period pays
only a preview check.

## Combinations that were measured

Selection only, 60-second monthly memory, 1,440 pictures prepared, no render or music.
"Cold" means fresh previews and pixel facts; installation, model download and image pull are excluded.
Single observations under different cache conditions, not a hardware ranking.

| Host and mode | Cold | Repeat |
|---|---:|---:|
| Workstation, rules + `metadata_only` | 55.4 s | 1.4 s |
| Celeron J4125 NAS, rules + `metadata_only` | 279.0 s | 11.1 s |
| Kubernetes pod, rules + `metadata_only` | 65.2 s | 2.1 s |
| Kubernetes pod, rules + `no_captions` | 301.0 s | not a matched pair |
| Kubernetes pod, model reader on the LAN | 985.7 s | not measured |
| NAS, model reader on the LAN | 1,512.5 s (1,278 s of it waiting on 198 reader responses) | not measured |

Offloading the reader to another machine does not remove the wait for its answers. On the NAS, the
cold time splits into 19.9 s of preview access and 190.4 s of pixel facts; on the workstation,
34.7 s and 13.5 s.

Whole films on the workstation, warm cache, 1080p H.265 with bundled music, rules reader: between
56.9 s (on this day) and 287.7 s (trip) for the full CLI run including download, titles, encode
and audio. The [capability report](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/docs/research/2026-09-12-capability-matrix.md)
has the per-product table.

## What to expect on a first run

Ten setups, one 60-second monthly memory each, the same month of the same library, app image
`0.87.16`. Every cell banked in a cache of its own and started cold, so the cold columns are a
first derivation and not a re-read. Overlap is the share of pictures a cut has in common with the
Mac local-model cut of the same month. The hosts:

- **Mac**: Apple M5 Max. Reader Qwen3-VL-30B-A3B at 4-bit on oMLX, captions from mlxcel.
- **NAS**: Synology, Celeron J4125, 4 cores, no AVX, no CFS controller. Docker, 4 GB per container.
- **Cluster**: a Kubernetes Job with 2 CPU and 4 GiB, the inference service on an NVIDIA T1000 8 GB. CPU encode, GPU title kernels.

Both hosted rows read with Melious `qwen3-30b-a3b-instruct`.

### The demo month: 133 pictures, June 2024

<!-- Fields in output/setup-matrix/demo/run1/summary.data.json, per cells[] entry:
     setup id, tier tier, prepare cold timing.prepare_cold_s, prepare warm timing.prepare_warm_s,
     selection timing.selection_s, render timing.render_s, film video.duration_s,
     kept len(selected_asset_ids), overlap overlap_vs_cell_1 against reference_cell mac-local. -->

| Setup | Tier | Prepare cold | Prepare warm | Selection | Render | Film | Kept | Overlap |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Mac, local 30B reader | `full` | 22 s | 0.4 s | 218 s | 49 s | 55.5 s | 15 | 100 % |
| Mac, rules | `full` | 22 s | 0.4 s | 1 s | 50 s | 55.0 s | 15 | 15 % |
| NAS, rules, facts in process | `no_captions` | 180 s | 0 s | 5 s | 1,483 s | 54.0 s | 15 | 15 % |
| NAS, rules, facts on the service | `no_captions` | 120 s | 0 s | 5 s | 1,453 s | 55.0 s | 15 | 15 % |
| NAS, hosted 30B reader | `no_captions` | 0 s | 0 s | - | 1,582 s | 54.5 s | - | - |
| Cluster, rules, facts in process | `no_captions` | 43 s | 0 s | 1 s | 331 s | 58.0 s | 14 | 16 % |
| Cluster, rules, facts on the service | `no_captions` | 90 s | 0 s | 2 s | 313 s | 58.0 s | 14 | 16 % |
| Cluster, hosted 30B reader | `no_captions` | 120 s | 0 s | 563 s | 304 s | 58.5 s | 14 | 32 % |

The NAS hosted row found every picture fact already on the inference service, which is why it
derived nothing in preparation, and the run exited before its cut was collected, so the selection,
kept and overlap cells are blank. Its render second is the one to read.

### A real month: 13,552 pictures, February 2024

Only the two Mac cells are valid here. The NAS cells were stopped before they finished, and the
cluster cells were still running when this page was written; those land in a follow-up.

<!-- Fields in output/setup-matrix/february/run2/summary.data.json, per cells[] entry:
     setup id, tier tier, prepare cold timing.prepare_cold_s, prepare warm timing.prepare_warm_s,
     selection timing.selection_s, render timing.render_s, film video.duration_s,
     kept len(selected_asset_ids), overlap overlap_vs_cell_1 against reference_cell mac-local.
     Picture count is cells[].prepared.pictures. -->

| Setup | Tier | Prepare cold | Prepare warm | Selection | Render | Film | Kept | Overlap |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Mac, local 30B reader | `full` | 3,180 s | 1 s | 1,143 s | 81 s | 55.0 s | 15 | 100 % |
| Mac, rules | `full` | 4,860 s | 2 s | 8 s | 86 s | 54.5 s | 15 | 25 % |

### What dominates, per host

**Mac: the captions.** 53 min of cold preparation over 13,552 pictures, 61 % of it captions. The
two detectors took 540 s of the rest, the encoder and its six heads 300 s, previews 240 s, pixels
120 s. Reading the month cost 1,143 s over 1,417 candidates and the render 81 s, so once the month
is prepared the whole run is 1,225 s and the 53 min is a one-off. The rules cell
paid 4,860 s for the same preparation in its own cache, so 53 min is the good day and not the
figure to plan on.

**NAS: the render.** 1,483 s to make a 54-second film on four Celeron cores, against 49 s for the
same job on the Mac. Preparation at `no_captions` cost 1.4404 s per picture, 0.5963 s of it the
encoder and its six heads and 0.6930 s the two detectors. February at that rate is 19,520 s, about
5 h 25 min, which is why those cells were stopped. Warm, the same month prepares in 0 s and selects
in 5 s.

**Cluster: the render again, at a fifth of the NAS.** 304 s to 331 s per film, CPU encode and GPU
title kernels. Cold preparation over the LAN ran at 0.68 s per picture, 0.61 s of it the facts
request including the service's first model load. The same pod deriving its own facts managed
0.3229 s per picture and 43 s cold against 90 s, so the inference service does not pay for itself
on a box that quick: it is there for hosts like the NAS, at 1.4404 s per picture in process. The
hosted 30B reader took 563 s cold for 130 candidates, against 218 s for the local 30B on the Mac
over the same month.

### What overlap means

Overlap counts identical pictures: the cut's asset ids against the Mac local-model cut of the same
month. The two 30B readers read the month the same way and picked different frames of it, 32 % of
the pictures in common. The rules reader keeps the days and loses the story: 15 % on the demo
month, 25 % on February, at the same picture count and the same film length. A low overlap is a
different selection of the same period, not a broken one, and only the model cut carries a reason
under each picture.

### How these numbers were taken

One memory per cell, single observations, from the setup matrix runs of 13 and 14 September 2026 on
image `0.87.16`. [Setup matrix](../contribute/setup-matrix.md) has the ten cells, the
cache-per-cell rule that makes the cold columns cold, and the command to run the whole thing again.
The two z.ai cells were in the matrix and were not measured this time.

## What the rules cut keeps, per memory type

Against the model editor's reference cut over the same periods, the rules reader retained these
shares of the known occasions (a lower bound: an alternative picture can carry the same occasion):

| Memory type | Rules + metadata | Rules + classifiers |
|---|---:|---:|
| Special day, on this day, album | 100 % | 100 % |
| Person | 94 % | 94 % |
| Multiple people | 86 % | 86 % |
| Trip | 67 % | 78 % |
| Monthly | 62 % | 62 % |
| Holiday | 46 % | 55 % |
| Season | 45 % | 30 % |
| Year in review | 43 % | 49 % |

Prefiltered requests (a person, an album, one event, a trip) survive rules well. Broad recaps (month,
season, year) are where the model's interpretation earns its cost. Classifiers are not an automatic
upgrade: the season cut with classifiers filled its target but chose more household objects and was
judged less focused than the shorter metadata cut.

## What leaves your network, per mode

| Mode | To the caption server | To the reader | Elsewhere |
|---|---|---|---|
| rules + `metadata_only` | nothing | nothing | Immich reads; Nominatim for trip GPS; map tiles for title screens |
| rules + `no_captions` | nothing | nothing | same |
| any reader + `full` | a 400 px JPEG of every picture in the period, once | (see next rows) | same |
| `model`, local | as above on `full` | 800 px tiles of a few dozen candidates, plus their annotation lines with people and place names, to a box you own | same |
| `model`, hosted | as above on `full` | the same tiles and lines to the provider | same |

The caption and reader endpoints default to `localhost`. Pointing either at another host is the
consent step; nothing asks twice. The complete list, with the switch for each destination, is on
[Network & Privacy](./configuration/network-and-privacy.md).

## Not measured

- The full product-by-host matrix: every memory type was measured on the workstation only.
- A cold `full` tier end to end on any host.
- Hosted cost for anything but one monthly selection.
- NAS and Kubernetes render throughput on the story-first route.
- Any controlled quality ranking between readers or providers.

The rules reader is a degraded mode, not an equal-quality alternative. Review the cut before you
share it.

## Pick one

- One Apple Silicon Mac with 32 GB or more: `reader: model`, `tier: full`, everything local. The only end-to-end configuration that has been graded.
- A NAS and nothing else: `reader: rules`, `tier: metadata_only` today; `no_captions` once `models fetch` has run. See [NAS + a model box](./common-setups/nas-only.md).
- A NAS plus a machine that holds the model: the app on the NAS on `no_captions`, `llm.base_url` pointing at the other box.
- A hosted reader: the same as above with a provider URL and key, and the privacy table above read once.

The whole stand-up, in order, is the [self-hosting guide](./self-hosting.md).

## Title rendering

Every mode above renders title screens the same way: on the GPU kernels where they exist, and with PIL where they do not. GPU title rendering runs on Quadrants, which has wheels for Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 on Python 3.11-3.13. On macOS x86_64 and on Python 3.14 there is none, and title screens fall back to the PIL renderer (static gradient and text, no animated kernels, no SDF text); `immich-memories preflight` says which you will get. See [Title kernels](./hardware/cpu-only.md#title-kernels).
