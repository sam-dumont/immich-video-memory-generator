---
date: 2026-09-12
status: February spike and benchmark review complete; release acceptance remains open
---

# February: the fast path, Kubernetes and the cuts

**Historical February checkpoint.** The [full product capability matrix](2026-09-12-capability-matrix.md)
supersedes the monthly-only implementation limit and unrun matrix statements below. The
measurements and deployment conditions in this report remain the original observations.

The February no-inference slice now works. Fresh-cache selections took **55.4 seconds on the
Mac, 65.2 seconds on Kubernetes and 279.0 seconds on the NAS**. Repeating them took **1.4,
2.1 and 11.1 seconds**, with identical ordered selections on each machine. The external-reader
Kubernetes run completed in **16 min 26 s**. The measured cuts all contain **52.5 seconds** of
content against the same 60-second request, with title/transition allowance.

This completes the recovered phase 5 review and the subsequently authorized February spike.
It does **not** establish release readiness: occasion coverage still needs owner review, the
ten-route acceptance bar is unrun, and the successful cluster environment used temporary
dependency and source overlays. No merge, publication or production rollout was performed.

The [aggregate data](2026-09-12-phase5-readiness.data.json) contains the individual runs,
preparation stages, comparison counts and source fingerprints. The private contact-sheet gallery
is `output/phase5-review-20260912.private/index.html` in this worktree, with a Markdown index
beside it. It contains every completed selection, plus explicit entries for failed and
superseded attempts. Warm repeats have their own sheet links generated from their saved plans.

## Measured time

All arms select February 2024 monthly highlights with `--duration 60 --no-render --no-music`.
Preparation covers 1,440 pictures. “Cold” below means fresh picture facts and preview cache;
model files were already staged. It excludes installation, model downloads and pod scheduling.
Mac/Kubernetes times cover the CLI invocation; NAS times cover container lifetime, including
startup and shutdown. These are single observations, not an isolated hardware ranking.

| Application host / reader | Preparation condition | People contexts | Selection time | Clips |
| --- | --- | ---: | ---: | ---: |
| Mac / rules, no inference | `metadata_only`, cold | 89 | **55.4 s** | 15 |
| Mac / rules, no inference | same cache, repeat | 89 | **1.4 s** | 15 |
| NAS / rules, no inference | `metadata_only`, cold | 89 | **4m39s** | 14 |
| NAS / rules, no inference | same cache, repeat | 89 | **11.1 s** | 14 |
| Kubernetes / rules, no inference | `metadata_only`, cold | 89 | **65.2 s** | 14 |
| Kubernetes / rules, no inference | same cache, repeat | 89 | **2.1 s** | 14 |
| Mac / rules + image classifiers | other facts warm; Docling v2 rebuilt | 89 | **49.8 s** | 15 |
| NAS / rules + image classifiers | all facts warm | 89 | **15.1 s** | 14 |
| Kubernetes / rules + image classifiers | all facts cold | 89 | **5m01s** | 14 |
| Kubernetes / Qwen on Mac | warm facts; earlier reader attempts present | 89 | **16m26s** | 14 |
| NAS / Qwen on Mac, earlier fixed run | other facts warm; Docling v2 rebuilt | **0** | **25m13s** | 14 |
| Mac / Qwen, original cold run | `no_captions`, cold facts | 89 | **29m09s** | 14 |
| Mac / Qwen, original warm run | `no_captions`, warm facts | 89 | **24m12s** | 15 |
| Mac / hosted GPT-4.1 mini | `no_captions`, warm facts | 89 | **8m57s** | 15 |

The hosted run cost about **$0.256** in recorded token usage, not an invoice. The original
[cost report](2026-09-12-deployment-costs.md#local-versus-hosted-reading) retains its full
request counts and pricing arithmetic. Hosted inference received personal annotation text and
selected images. Qwen external inference here means the owner's Mac on the LAN.

## Why the NAS took so long

The successful NAS external-reader run spent **1,278.2 seconds (21m18s)** between event reading
and completed selection: about **85%** of its 1,512.5-second total. It logged **198 successful
reader HTTP responses**. Rebuilding corrected Docling facts cost another **227.2 seconds**.
Offloading the reader removed the NAS's model compute, but the application still waited for
all those responses. `no_captions` never meant “no inference.”

The final Kubernetes external run made **188 successful reader HTTP responses**. Its plan's
narrower collector reports 160 calls, 395,830 prompt tokens and 40,408 completion tokens;
these are partial planner metrics, not a full-run bill. Warm picture facts cost only 0.098
seconds to verify. The remaining time is principally reading and selection, not GPU preparation.
Earlier attempts had populated some reader banks, so 16m26s is not a cold-reader measurement.

The actual zero-inference path is `reader: rules` plus `preparation.tier: metadata_only`, with
a fresh annotation store when measuring degradation. Existing model facts in a reused store
would otherwise change what evidence the rules can see. The cold runs produced only the
1,440 pixel records, and every rules plan reports zero LLM calls. Rules bypass semantic banks
and do not construct the model observer, reranker or motion-reading ports.

| Fresh preparation stage | Mac, metadata only | NAS, metadata only | Kubernetes, metadata only | Kubernetes, classifiers |
| --- | ---: | ---: | ---: | ---: |
| Previews | 34.67 s | 19.88 s | 8.53 s | 11.20 s |
| Pixel facts | 13.52 s | 190.40 s | 42.11 s | 41.88 s |
| DINOv2 + six public heads | — | — | — | 61.13 s |
| Docling + Marqo | — | — | — | 172.06 s |
| Whole CLI/container | **55.42 s** | **279.01 s** | **65.20 s** | **301.04 s** |

Stage sums are not whole-run time. On the NAS, previews and pixels account for 210.3 seconds;
the remaining 68.7 seconds cover the other work and lifecycle overhead. This pass did not add
profiling to subdivide that remainder. The classifier detector counter is 2,880 units: two per
picture. The five-minute Kubernetes arm ran while the other arm waited on the Mac reader;
the pod shared a four-CPU limit. Do not treat these one-shot measurements as an SLA.

## People context and what changed in the cuts

The Mac's existing `people.yaml` was copied to the NAS benchmark home and the temporary
Kubernetes home. All three loaded **89 entries**, and their file checksums matched. Before
the copy, both remote homes loaded zero entries. The earlier NAS external cut remains labeled
with that missing context; copying a file later does not repair an old result. The NAS copy
remains in its benchmark home. The Kubernetes copy was ephemeral; no production ConfigMap
or deployment was changed.

The remote rules runs selected exactly the same ordered assets before and after the people
copy. With matching people context, NAS and Kubernetes also selected exactly the same ordered
14 assets for each corresponding rules tier. The Mac no-inference cut contains those 14 plus
one more asset; its classifier cut shares 13 with the remote classifier cut. These are useful
repeatability observations, not proof that every input/configuration was identical.

The sheets are rendered from **saved final plans and render projections**, without running
selection again. They show source previews in final order; they do not show the exact trimmed
video frames. Stars mark favourites. Yellow labels mark the two days with the most allocated
seconds. The sheet header rounds 52.5 seconds to 52; this report and the index preserve 52.5.

| Comparison | Shared assets | What the saved selections show |
| --- | ---: | --- |
| Kubernetes Qwen vs rules + classifiers, both with people context | **5 of 14** | Qwen: 11 distinct days, Feb 1–29, 8 favourites. Rules: 9 days, Feb 1–18, 10 favourites. |
| NAS no inference vs rules + classifiers, both with people context | **12 of 14** | Classifiers change two slots; both selections still stop at Feb 18. |
| Mac no inference vs rules + classifiers | **11 of 15** | Equal duration and favourite counts conceal different pictures. |
| Mac warm Qwen vs hosted GPT-4.1 mini | **4 of 15** | Same clip count and duration, substantially different selections. |

The rules sheets spend slots on hair clippings and, with classifiers, a USB hub and shelves.
The Kubernetes model sheet uses later-month family pictures instead. A dark video preview
appears in several cuts; its thumbnail is insufficient to judge the actual trim. The rules
stories also aggregate long consecutive runs, so the fixed weight floors can give a short,
favourite-heavy early story as many slots as a much longer later run. These are review findings,
not new fixes in this pass. No claim of equal occasion coverage is supported by the timing table.

## Kubernetes result and limits

The isolated pod ran on `rancher-worker-couronne-01` with the `nvidia` runtime, one advertised
GPU slot, a four-CPU limit and 8 GiB memory limit. The device is an **NVIDIA T1000 8 GB**.
The node advertises eight shared GPU slots; that does not mean eight physical cards.

The cached application image lacked several dependencies needed by this branch. The benchmark
used a source overlay plus temporary `timm`, `torchvision`, Hugging Face support and GPU ONNX
Runtime packages. Its PyTorch is `2.10.0+cu128`. ONNX Runtime GPU `1.26.0` worked with CUDA 12;
the initially tried `1.28` wheel required CUDA 13. The version boundary is documented by
[ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).
A copied DINO model path was initially a broken symlink; copying the resolved model fixed it.

An actual DINO inference returned shape `(1, 257, 384)` with the CUDA provider active. Docling
and Marqo ran on CPU in this branch. **NVENC passed a two-second synthetic 720p H.264 encode**
(60 frames; ffmpeg reported 5.6×). That is an availability smoke test, not memory-render
throughput. None of the benchmark selections rendered an MP4 or exercised music generation.

The first external attempt failed after **12m40s** with an incomplete period response. Captured
responses showed valid JSON followed by repetitive output. The pod configuration lacked the
working Mac reader's repetition controls. Applying the same `repetition_penalty: 1.1`,
`repetition_context_size: 2048` and disabled thinking completed the final run. A corrected
intermediate attempt was deliberately stopped after **5m55s** when the owner asked to add the
people file; it has no final selection. Failed and interrupted runs get status entries in the
gallery, not invented contact sheets.

All saved attempts were retrieved. The temporary pod and credential Secret were deleted and
verified absent. Temporary local credential and staged people files were removed. Production
deployment settings and workloads were left in place.

## Implementation and verification

The earlier Docling repair remains: use extended ONNX optimization on that detector and retire
its `det-v1` facts as `det-v2`. No gate was loosened. The corrected NAS labels match all 1,440
Mac assignments; the original [failure analysis](2026-09-12-deployment-costs.md#follow-up-fix-docling-on-the-nas-cpu)
contains the synthetic reproducer and hashes.

The authorized reader spike implements the monthly route only. `auto` resolves a blank reader
model to rules; explicit `model` still validates its model setting. The rule episode/period
readings preserve evidence identity, omit a thesis, bypass semantic banks, and reuse the
existing allocation, standing/admission and timing mechanisms. The runtime rejects rules for
other memory products before opening stores. Live Photos remain stills without motion
observation. UI and matrix changes remain outside the spike.

The final full suite recorded **6,707 passed, 7 skipped, 534 deselected and 1 failed**. The
remaining pre-existing `test_the_cli_banks_head_facts_beside_the_other_caches` still expects
the removed `SmartPipeline(triage=...)` argument. It is parked, not hidden. All non-test CI
gates passed, and 48 focused rule/runtime tests passed. The real Docling portability test
passed on the Mac and NAS after reproducing the NAS failure before the fix.

The required graph refresh completed; its generated output was preserved outside the checkout
to avoid bloating the Docker build context. Kubernetes benchmark source differs from the final
tested source by an equivalent `itemgetter` lint change and the guard that creates missing
plan lineage before recording the reader. Successful benchmark plans already had lineage.
The NAS also predates two recording fixes: its source lineage did not state that Live motion
was disabled, and its semantic-reuse label did not describe the rules bypass. Both behaviors
were already active; the final code records them explicitly. Measured and final source hashes
are retained in the aggregate data.

## Parked questions and wrap-up decision

1. **Occasion coverage:** accept the February rules cut, or revise its long-run grouping and
   weight floors? The declared ten-route bar—100% occasion recall and zero audience
   regressions—has not been evaluated. Do not call the whole rule-reader design released.
2. **Image choice:** review the object-photo slots, the dark video previews and the ID-style
   collage in the older NAS model cut. Contact sheets cannot establish motion quality.
3. **Deployment packaging:** build a reproducible image with matching CUDA dependencies,
   real model files, persistent people context and the working reader settings before claiming
   the existing production image is ready.
4. **Full tier:** the pinned caption-model compatibility failure remains unresolved. Neither
   the old full-tier attempt nor this pass supplies a successful full-tier comparison.
5. **Runtime/error bookkeeping:** an empty selection can still be recorded as a completed
   attempt before the CLI exits with an error. The stale triage-wiring test also remains.
6. **Remaining performance work:** cold NAS classifiers were not re-benchmarked as a single
   uninterrupted corrected run, and complete memory rendering was not measured. The old
   38-minute preparation figure is a sum across attempts, not a successful cold run.

The requested recovery, focused fixes, February slice, selection benchmarks and review artifacts
are complete. The evidence supports a working fast February path and working GPU capabilities
in Kubernetes. It supports an owner quality review next, not a blanket release sign-off.
