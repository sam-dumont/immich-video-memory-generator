---
date: 2026-09-17
status: measured; two cells absent by decision, two cells failed
---

# Setup matrix, 17 September 2026

Eleven cells over the fixture month and four over a real month, one memory each, on three hosts.
This page is the per-cell record: what every cell measured, what every cell did not, what failed
and why. The reader-facing version is
[Running modes and tradeoffs](../../docs-site/docs/being-rewritten/running-modes.md).

Nothing from the owner's library is here beyond timings, counts and costs. No titles, no places, no
people, no film names, no asset ids, no pictures.

## What ran, and on which build

| Lane | Build |
|---|---|
| Cluster and NAS | app image `0.102.0`, pinned in every Job and compose file the run wrote |
| Inference service | image `0.102.0-cuda` |
| Mac, `mac-local` and `mac-rules` | a working tree at v0.102.0, before [#1071](https://github.com/sam-dumont/immich-video-memory-generator/pull/1071) merged |
| Mac, `mac-hosted-openai-luna` | the same tree with #1071 in it |

The run's own `summary.data.json` records `image: 0.100.4`. That field is wrong: it is the label
passed to the summary writer, and every Job manifest the run produced pins `0.102.0`. Read the
manifests, not the field.

Readers: `Qwen3-VL-30B-A3B-Instruct-4bit` on local oMLX, `gpt-5.6-luna` on OpenAI,
`glm-5.3-flash` on z.ai, and the rules reader with no model at all. Three alternate local models
were run or attempted on the fixture month and are not published: the reader shortlist closed on
15 September and they are not on it.

## Hosts

- **Mac**: Apple M5 Max, 64 GB. Reader on oMLX, captions from mlxcel, facts in process.
- **NAS**: Synology, Celeron J4125, four cores, no AVX. Docker, `--cpuset-cpus 0-3 --memory 4g`.
- **Cluster**: a Kubernetes Job, 2 CPU and 4 GiB requested unless the row says otherwise, with the
  inference service on a CUDA node. The card behind the service was not recorded for this run.

## The fixture month: June 2024, 133 pictures, 130 eligible

Every cell banked into a cache of its own and started cold. Overlap is the share of identical
pictures against `mac-local`. Contract is rejections over repairs: how many answers a reading
contract refused, and how many of those were asked again.

| Cell | Tier | Reader | Facts | Models fetch | Prepare cold | Per picture | Prepare warm | Selection | Render | Titles / encoder | Peak RSS | Kept | Overlap | Contract | Film |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|---:|
| `mac-local` | `full` | local 30B | local | not measured | 35 s | 0.2608 s | 0.7 s | 319 s | 125 s | Metal / `hevc_videotoolbox` | 3,029 MB | 13 | 100 % | 0/0 | 56.0 s |
| `mac-rules` | `full` | rules | local | not measured | 45 s | 0.3355 s | 0.7 s | 1 s | 114 s | Metal / `hevc_videotoolbox` | 3,143 MB | 14 | 17 % | - | 60.5 s |
| `mac-hosted-openai-luna` | `full` | `gpt-5.6-luna` | local | not measured | seeded | seeded | seeded | 126 s | 89 s | Metal / `hevc_videotoolbox` | 1,314 MB | 14 | 23 % | 0/0 | 60.5 s |
| `nas-rules-local` | `no_captions` | rules | local | 5 s | 180 s | 1.4813 s | 0 s | 9 s | 1,586 s | not printed / `libx265` | 2,633 MB | 14 | 13 % | - | 60.0 s |
| `nas-rules-service` | `no_captions` | rules | service | not measured | 59 s | 0.4460 s | 0 s | 8 s | 1,573 s | not printed / `libx265` | 2,000 MB | 14 | 13 % | - | 60.0 s |
| `nas-hosted-zai` | `no_captions` | `glm-5.3-flash` | service | not measured | 53 s | 0.4012 s | 0 s | 594 s | 1,470 s | not printed / `libx265` | 1,973 MB | 14 | 23 % | 0/0 | 60.5 s |
| `k8s-rules-service` | `no_captions` | rules | service | not measured | 33 s | 0.2518 s | 0 s | 5 s | 316 s | CUDA / `libx264` | 2,415 MB | 14 | 13 % | - | 60.0 s |
| `k8s-hosted-zai` | `no_captions` | `glm-5.3-flash` | service | not measured | 37 s | 0.2757 s | 0 s | 371 s | 342 s | CUDA / `libx264` | 2,414 MB | 14 | 17 % | 0/0 | 60.0 s |
| `k8s-rules-local` | `no_captions` | rules | local | 2 s | 43 s | 0.3235 s | 0 s | 5 s | 294 s | CUDA / `libx264` | 2,531 MB | 14 | 13 % | - | 60.0 s |
| `k8s-gpu-t1000` | `no_captions` | rules | service | not measured | 41 s | 0.3081 s | 0 s | 5 s | 231 s | CUDA / `h264_nvenc` | not reported | 14 | 13 % | - | not recovered |
| `k8s-full-rules` | `full` | rules | service | not measured | 180 s | 1.2268 s | 0 s | 5 s | 295 s | CUDA / `libx264` | 2,357 MB | 14 | 17 % | - | 61.0 s |

`mac-hosted-openai-luna` prepared nothing: its bank was seeded from `mac-local`, which measures
preparation for that host, tier and facts source. Only the reader differs.

`k8s-gpu-t1000` is the encoder A/B against `k8s-rules-service`: same tier, same facts source, same
cluster, `h264_nvenc` against `libx264`, 231 s against 316 s. It is not a clean pair. The NVENC cell
asked for 1 CPU and the CPU-encode cell for 2, so the card finished 85 s sooner on half the CPU
request. Nobody has re-run it matched.

`k8s-rules-local` exists to price the inference service against a pod deriving its own facts, not as
a setup to publish. On a host with a card, CPU classifiers are a defect.

### Costs

Only one cell has a cost. It is list price times measured tokens, in the currency the shop
publishes in, with the token counts rounded at or above 1,000 by the run summary. Nothing was
charged to an account and read back.

| Cell | Model | Calls | Tokens in | Tokens out | Tiles | Cost |
|---|---|---:|---:|---:|---:|---|
| `mac-hosted-openai-luna` | `gpt-5.6-luna` | 93 | 103,865 | 13,003 | 14 | USD 0.0364 |
| `nas-hosted-zai` | `glm-5.3-flash` | 85 | 102,570 | 18,740 | 14 | no published price for this account |
| `k8s-hosted-zai` | `glm-5.3-flash` | 71 | 99,188 | 17,811 | 14 | no published price for this account |

z.ai returns no price with a completion and the manifest holds no list price for a coding-plan
account, so there is nothing to multiply those tokens by.

## A real month: February 2024

Four cells. Candidates after the scope pass: 1,417 on the Mac, 1,418 on the cluster.

**The preparation columns are the whole of 2024, not February.** `prepare --year Y --month M` dropped
the month and prepared the calendar year until
[#1056](https://github.com/sam-dumont/immich-video-memory-generator/pull/1056) merged on
17 September, after these cells ran. Every preparation figure below is 13,544 pictures of 2024. The
per-picture rates are unaffected, and `prepare --month` now prepares only that month, so a month
costs its own picture count at the same rate. Selection and render are February's.

| Cell | Tier | Reader | Facts | Prepare cold (the year) | Per picture | Prepare warm | Selection | Render | Titles / encoder | Peak RSS | Kept | Overlap | Contract | Film |
|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|---:|
| `mac-local` | `full` | local 30B | local | 6,420 s (1 h 47 min) | 0.4778 s | 2 s | 1,719 s (28 min 39 s) | 228 s | Metal / `hevc_videotoolbox` | 4,279 MB | 14 | 100 % | 3/1 | 61.1 s |
| `mac-rules` | `full` | rules | local | 1,080 s (18 min), cache already primed | 0.0806 s | 2 s | 15 s | 190 s | Metal / `hevc_videotoolbox` | 2,500 MB | 14 | 17 % | - | 60.4 s |
| `k8s-rules-service` | `no_captions` | rules | service | 3,900 s (1 h 05 min) | 0.2896 s | 1 s | 27 s | 428 s | CUDA / `libx264` | not reported | 14 | 17 % | - | not recovered |
| `k8s-hosted-zai` | `no_captions` | `glm-5.3-flash` | service | 3,840 s (1 h 04 min) | 0.2822 s | 1 s | 724 s (12 min 04 s) | 406 s | CUDA / `libx264` | not reported | 13 | 13 % | 2/0 | 57.6 s |

`mac-rules` did not pay a cold year. Its own cache was already there and only 5,576 captions were
outstanding, so its 1,080 s is a partial re-read and its 0.0806 s a picture is not comparable with
the row above it. Do not quote it as the rules cost of a year.

`k8s-hosted-zai` recorded 110 calls, about 273,500 tokens in and 37,000 out. Those counts come from
the run log rather than the per-call record, and the summary rounds at or above 1,000. No price.

### Where the time went

- `mac-local`: captions are 4,080 s of the 6,420 s cold preparation, 64 %. The two detectors 1,020 s,
  the encoder and its six heads 720 s, previews 480 s, pixels 240 s.
- `k8s-rules-service`: facts requests are 3,420 s of 3,900 s, 88 %, at 0.2509 s a picture. Previews
  360 s, pixels 360 s.

## Two cells are absent by decision

- **The cluster's CPU-only `rules-local` cell on February.** Not published. On a GPU host every stage
  that can use the card must, so a CPU classifier row on a cluster is a defect and never a setup
  anyone should copy. The attempt in the record also failed, at `apply-claims exited 1`.
- **A hosted reader on February.** Not published. Hosted spend stays on the fixture month. The
  attempt in the record also failed, at `generate exited 1`, which is the config regression #1071
  fixed later the same evening.

Neither is a blank row anywhere. Where a table would have carried them, the reason is written out.

## The two cells that failed

| Cell | Failure | What it means |
|---|---|---|
| February `mac-hosted-openai-luna` | `generate exited 1`, no cut, peak RSS 571 MB | Every hosted reader was broken in v0.102.0 by a config regression: an `OPENAI_API_KEY` alias beat the reader key the config file states. [#1071](https://github.com/sam-dumont/immich-video-memory-generator/pull/1071) fixed it at 21:15 UTC on 17 September. The fixture-month hosted Mac cell was re-run after that and is the one published |
| February `k8s-rules-local` | `apply-claims exited 1`, nothing measured | The claims never applied and the pod never started. Not chased, because the cell was not going to be published anyway |

## Three cells lost their copy-out

`k8s-gpu-t1000` on the fixture month, and `k8s-rules-service` and `k8s-hosted-zai` on February, hit
`Truncated tar archive: Unknown error: -1` while copying the attempt directory off the volume.
Anything behind the broken member stayed on the volume. What that costs each of them:

- The film's duration, where the film itself was behind the break.
- The editor's own record of the cut, so the kept pictures are the ones the run downloaded, read off
  the generate log, and the order they play in is not recoverable from a download line.

Peak RSS and CPU seconds are also missing from all three: the cluster collector did not report them.

## Most Mac cells were measured with other work on the machine

A contention log sampled the Mac once a minute from 17:29 to 22:15 on 17 September, 286 minutes,
keyed by the worktree each concurrent run came from. Per cell window:

| Mac cell and phase | Window | Minutes | Another run alive | Most at once |
|---|---|---:|---:|---:|
| February, local reader, cold preparation | 17:35 to 19:22 | 107 | 80 (75 %) | 3 |
| February, local reader, selection and render | 19:24 to 19:53 | 30 | 29 (97 %) | 1 |
| February, rules, cold preparation | 19:56 to 20:15 | 20 | 20 (100 %) | 1 |
| Fixture, rules, whole cell | 20:21 to 20:25 | 5 | 5 (100 %) | 1 |
| Fixture, local reader, whole cell | 21:07 to 21:16 | 10 | 0 | 0 |
| Fixture, hosted reader, whole cell | 23:44 to 23:50 | not sampled | unknown | unknown |

The concurrent work was three other worktrees of this same project, each running its own selection:
`immich-diverse-cut-20260917` (80 of the 107 preparation minutes), `immich-videos-first-20260917`
(58) and `immich-vp9-clip-20260917` (6).

What that costs the headline figure: the February local-reader cell prepared at **0.4778 s a
picture** here, against **0.2336 s** for the same tier on the same machine on 13 to 14 September
with nothing else on it. Both are real. The contended one includes whatever the other runs took off
the box, and the quiet one is the floor. Neither replaces the other, and both are published with
their condition.

The fixture local-reader cell is the one Mac cell the log shows running alone, so its 35 s
preparation, 319 s selection and 125 s render are the cleanest Mac numbers in the run. The fixture
hosted cell ran after the log stopped and nothing is known about the machine then.

The NAS and cluster cells are unaffected. Each ran on its own host.

## The rules reader still made one model call

On both Mac lanes the rules cell recorded exactly one model call: 854 prompt and 54 completion
tokens on the fixture month, 1,033 and 59 on February. It is not the reader. The log puts it in the
music stage, after the render, on a host with an `llm` endpoint configured. A rules cell on the NAS
or the cluster, with no endpoint, recorded none. "Rules makes no model requests" is true of the
reader and not of the whole run, and the deploy pages now say so.

## What this run does not answer

- `metadata_only` on any host. The matrix has no such cell and never has.
- Anything but `full` on the Mac.
- `full` on the NAS. A caption on four Celeron cores measured 30.9 s on an earlier run, so the tier
  was not attempted.
- A NAS over a real month. The NAS lane ran the fixture month only.
- Which card answered the facts requests. The run did not record the inference node's product.
- Any quality ranking between readers. Overlap counts identical pictures and is not a grade.
- Repeat observations. Every cell is one run.
