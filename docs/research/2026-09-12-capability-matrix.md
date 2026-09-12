---
date: 2026-09-12
status: selection and films verified; release integration in progress
---

# Reader capability matrix

**All ten standard products and both controls now produce a rules selection. Forty-eight successful public-CLI runs made zero model requests and wrote zero semantic-bank rows. Every profile/product pair selected the same ordered assets under two hash seeds. This establishes repeatability, not equal editorial quality.**

The private review contains **36 contact sheets**: each accepted model reference beside the rules-only and rules-plus-classifiers selections. The sheets use saved final plans and source previews, without reselection. They are not exact video trim frames. Private evidence stays under `output/reader-matrix-20260912.private/`; public aggregate numbers are in [the data file](2026-09-12-capability-matrix.data.json).

## What each configuration can do

| Capability | Rules + metadata | Rules + image classifiers | Model reader, local or external |
| --- | --- | --- | --- |
| Ten standard memory products | Yes, measured | Yes, measured | Existing accepted reference for each |
| Arbitrary semantic custom subject | Explicitly refused | Explicitly refused | Supported by model reading; not rerun here |
| Person, date, album and trip source constraints | Shared source contract | Shared source contract | Shared source contract |
| Favourites and known people context | Yes | Yes | Yes |
| Image-content evidence | Pixel measurements only | Frozen context heads plus Docling/Marqo | Depends on configured preparation tier; selected visual observations also possible |
| Natural-language story interpretation and thesis | No | No | Yes |
| Semantic moment grouping / model reranking | No | No | Yes |
| Live Photo motion selection | Stills only | Stills only | Available; present in several references |
| Album owner curation | Respected after the fix below | Respected after the fix below | Existing model route |
| Model-based audience judgements | No | No; classifiers provide limited evidence | Yes, with fallible model decisions |
| Per-call provider fee | **$0** | **$0** when classifiers run locally | Local: $0 provider fee; hosted: usage dependent |
| Machine/storage/electricity cost | Not measured | Not measured | Not measured |

Rules reuse the existing allocation, admission, source and audience gates. They cannot reproduce model judgements when the needed evidence does not exist. Choosing `metadata_only` in a populated store does not erase old facts. For the no-inference comparison, the store began empty; the classifier comparison removed captions, flags and semantic banks. Both blocked all configured model HTTP endpoints.

## Selection time, seconds

The first two columns are new runs on the Mac. “First” means first invocation of that route in a serial matrix: the first metadata route paid a cold preview/pixel pass, and later overlapping routes could reuse it. The classifier arm began with existing classifier facts and paid a Docling-v2 backfill. Neither column is a universal cold-start estimate. Repeat includes discovery and the real CLI; person discovery still takes about 30 seconds.

The last column is the historical accepted model invocation. Its preparation and semantic caches were mixed, so it is context rather than a controlled speedup ratio. No new hosted inference was purchased for this matrix.

| Product / control | Metadata first / repeat | Classifiers first / repeat | Historical accepted model |
| --- | ---: | ---: | ---: |
| monthly | 49.2 / 1.8 | 26.2 / 3.1 | 8.3 |
| person | 432.9 / 30.7 | 186.1 / 31.2 | 40.6 |
| multi_person | 32.1 / 31.4 | 33.6 / 33.2 | 282.7 |
| special_day | 5.5 / 0.7 | 4.2 / 1.7 | 191.9 |
| trip | 15.9 / 1.8 | 7.5 / 2.9 | 645.7 |
| year | 39.3 / 1.9 | 20.0 / 3.0 | 1680.6 |
| season | 18.1 / 1.1 | 10.1 / 2.1 | 1507.0 |
| holiday | 17.8 / 1.3 | 9.6 / 2.3 | 1132.7 |
| on_this_day | 12.8 / 1.9 | 7.8 / 2.9 | 540.7 |
| album | 0.8 / 0.8 | 2.5 / 1.7 | 101.4 |
| june_control | 4.9 / 1.8 | 6.0 / 2.8 | 4.3 |
| february_long_control | 2.0 / 2.0 | 3.1 / 3.2 | 33.1 |

All rules rows cost **$0 in model API charges**. Electricity was not metered. To price it honestly, use measured average watts × wall seconds ÷ 3,600,000 × local currency/kWh; add hardware and storage separately. The [earlier February comparison](2026-09-12-deployment-costs.md#local-versus-hosted-reading) measured a hosted GPT-4.1-mini selection at **537.4 s and approximately $0.256** from recorded tokens. That one monthly price must not be multiplied blindly across trips or year reviews. The other hosted product prices remain unmeasured.

## What is lost

| Product / control | Metadata clips / content seconds | Classifier clips / content seconds | Exact reference assets retained, metadata / classifiers | Known reference occasions retained, metadata / classifiers |
| --- | ---: | ---: | ---: | ---: |
| monthly | 15 / 52.5 | 15 / 52.5 | 5/15 / 5/15 | 5/8 / 5/8† |
| person | 22 / 82.5 | 22 / 82.5 | 11/22 / 11/22 | 16/17 / 16/17† |
| multi_person | 22 / 82.4 | 22 / 82.4 | 13/22 / 13/22 | 19/22 / 19/22† |
| special_day | 10 / 54.0 | 7 / 40.0 | 4/11 / 3/11 | 1/1 / 1/1 |
| trip | 28 / 115.9 | 32 / 122.4 | 3/32 / 3/32 | 6/9 / 7/9† |
| year | 32 / 128.7 | 37 / 142.1 | 10/37 / 7/37 | 16/37 / 18/37† |
| season | 18 / 74.9 | 33 / 127.0 | 5/33 / 0/33 | 9/20 / 6/20† |
| holiday | 13 / 52.2 | 13 / 52.2 | 4/13 / 4/13 | 5/11 / 6/11† |
| on_this_day | 8 / 34.0 | 10 / 37.5 | 3/6 / 3/6 | 6/6 / 6/6 |
| album | 10 / 37.5 | 10 / 37.5 | 1/4 / 1/4 | 1/1 / 1/1† |
| june_control | 15 / 52.5 | 15 / 52.5 | 1/15 / 1/15 | 4/13 / 4/13† |
| february_long_control | 45 / 172.5 | 45 / 172.5 | 12/45 / 17/45 | 13/20 / 14/20† |

† **Lower bound, incomplete reference inventories.** A selected member proves a known occasion survived. Missing inventory membership cannot prove an occasion was lost. Exact photo overlap is a different measure: another picture can preserve the same occasion. No 100%-occasion-recall claim is supported by these records.

The metadata special-day cut contains **54.0 s** against an **82.5 s content budget**; classifiers reduce it to **40.0 s**. Both are declared editorial shortfalls. The metadata season cut contains **74.9 s** against **127.5 s**; classifiers fill **127.1 s**, but visual review shows more household objects and repeated garden/car pictures. Length recovered is not quality recovered. The accepted special-day model reference was also shorter than the requested budget; the model column is a reference, not an infallible oracle.

The season classifier selection shares **zero of the 33 accepted model assets**. The metadata season selection shares five and contains more people and outings. The June control keeps only **one of 13 reference favourites** in either rules profile. February at 180 seconds keeps **11 of 15 reference favourites**; filling the longer cut does not restore all the preferred moments. Rules also lose the semantic thesis and motion choices listed above.

Some rules selections include assets the reference model marked `do_not_show`: **9 occurrences across the ten metadata products**, versus **6 with classifiers** (controls counted separately below). This is a concrete audience-review flag, not proof those pictures are unsafe: the old judgement can be wrong and the scopes need checking. The private comparison records the exact assets and reference occasions for review. The longer February control adds four such flags with metadata and three with classifiers. The June control adds none. The design’s zero-audience-regression bar has therefore not been established.

## Actual films
**All ten 1080p H.265 films with bundled music passed full video and audio decoding.**
Each film uses exactly the ordered selection in its metadata-only review sheet. Every saved
plan reports zero LLM calls and no model-endpoint attempt was logged. No films were uploaded.
The private gallery includes each MP4 and a six-frame sheet sampled from the finished film.

| Product | Whole CLI, seconds | Finished film, seconds | Model API charge |
| --- | ---: | ---: | ---: |
| monthly | **104.5** | 54.50 | $0 |
| person | **191.3** | 89.03 | $0 |
| multi_person | **230.9** | 89.05 | $0 |
| special_day | **126.5** | 57.50 | $0 |
| trip | **287.7** | 115.85 | $0 |
| year | **184.6** | 140.57 | $0 |
| season | **168.4** | 75.82 | $0 |
| holiday | **124.2** | 57.73 | $0 |
| on_this_day | **56.9** | 38.50 | $0 |
| album | **71.1** | 41.50 | $0 |

These are **warm preparation-cache workstation observations**, including discovery, source
downloads/conversion, titles, encoding, audio mixing and final processing. They are not cold
end-to-end timings. Local CI, graph refresh and documentation work overlapped some films;
no isolated throughput or cross-hardware speedup is claimed. Film duration differs from the
sum of selected content because the render adds titles and overlaps transitions.

The initial monthly/person launcher restarted the CLI inside macOS GPU-probe children and
forced CPU title rendering. Those attempts are preserved but excluded from this table. Both
were rerun after adding the `__main__` guard; final logs confirm Metal. The corrected monthly
run took 104.7 seconds versus 186.4 seconds in the invalid launcher attempt. This fixes the
benchmark launcher, not the application GPU selection.

Visual review then caught black photos despite successful decoding. The ExifTool fallback
treated Apple's raw HDRHeadroom tag as a linear brightness ratio: a raw zero became a
zero-nit photo. Apple's reconstruction requires both raw tags; a declared XMP ratio can
also be used. The parser now uses complete metadata or a neutral 1.0 ratio (203-nit SDR
white) when it is incomplete. See [Apple's reconstruction guide](https://developer.apple.com/documentation/appkit/applying-apple-hdr-effect-to-your-photos)
and [ExifTool's Apple tag definitions](https://exiftool.org/TagNames/Apple.html).
The five affected films were rendered again; the table above uses those corrected runs.
The earlier attempts remain in the private evidence. Regression coverage includes raw
zero, complete metadata pairs, declared ratios and invalid/nonfinite metadata.

## NAS and Kubernetes evidence

The [phase 5 report](2026-09-12-phase5-readiness.md) retains the measured February deployment comparison: metadata-only cold/warm selection was **55.4 / 1.4 s on the Mac**, **279.0 / 11.1 s on the NAS**, and **65.2 / 2.1 s on Kubernetes**. The Kubernetes cold classifier run took **301.0 s**. These are single observations with their recorded cache/config differences, not a hardware ranking. This new twelve-case matrix ran on the Mac; the full product-by-host cross product remains unmeasured.

The earlier NAS external-reader run spent **1,278.2 of 1,512.5 seconds** waiting through reading/selection, with 198 successful reader responses. Offloading model execution does not remove that wait. Kubernetes external reading completed in **985.7 seconds**, with 188 responses and matching people context. The earlier NAS reader run lacked people context. The copied people file does not retroactively fix that old run.

Kubernetes proved actual CUDA DINO inference and a short NVENC encode. Its benchmark used a temporary source/dependency overlay, not a reproducible release image. The temporary Pod and credential Secret were removed after collecting evidence. The inference-service and ONNX packaging PRs still need integration and CI before an image can be called ready.

## Implementation and release review

The February reader now accepts all ten standard products. A runtime test exercises their real selector and render projection without constructing model ports. A custom semantic request is refused before opening a store. The album matrix exposed a root cause: the owner’s album choice was not treated as editorial intent, so an unstarred album could select nothing. Album curation now supplies worthiness and ordinary standing after technical/source/audience gates. Its saved matrix cut contains ten clips.

The obsolete triage-constructor test was removed; existing preparation/runtime tests already cover banking in the library cache and warm producer reuse through the current API. The new comparison regression proves that a different asset in a complete reference inventory still retains its occasion. It also distinguishes missing inventories from proven losses.

Local `make ci` passed: **6,733 tests** for this capability branch and **6,730 tests** for the service repair, with seven skips each. The documentation build passed; the final staged privacy check precedes publication. Release integration and its final CI remain in progress. Historical merge-message lint now accepts the existing lowercase merge prefix, and the inference health test covers both available and absent optional ONNX Runtime. The new rules path is a measured degraded option; the findings above do not satisfy the design’s equal-quality release bar. UX implementation remains the next phase.

## Questions parked for review

- Is the shorter people-heavy season cut preferable to the longer classifier cut? Do not tune it by runtime alone.
- Should rules bias harder toward favourites and later-period coverage? June and long February expose the current tradeoff.
- Review assets the model refused before claiming audience parity. Keep the raw decisions private.
- Decide whether the declared 100% occasion-recall bar is a requirement for the optional rules mode, or a goal for a later editor revision. Missing inventories need human review.
- Measure hosted cost on the remaining products and render throughput on NAS/Kubernetes before publishing those cells as supported performance claims.
- The multi-person opening displays its raw Boolean request and overlaps the year. Holiday/on-this-day date ranges and mixed-language trip titles also need the planned UX/localization review.
- Recheck gain-map transfer curves against Apple's Rec.709 reconstruction guidance in a later colour review. This pass fixes metadata units and missing-data handling; it does not replace the previously validated reconstruction algorithm.
