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
