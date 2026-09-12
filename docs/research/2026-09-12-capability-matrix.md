---
date: 2026-09-12
status: selection, films, captions, and published CUDA execution verified
---

# Reader capability matrix

**All ten standard products and both controls now produce a rules selection. Seventy-two successful public-CLI runs made zero model requests and wrote zero semantic-bank rows. Every profile/product pair selected the same ordered assets under two hash seeds. This establishes repeatability, not equal editorial quality.**

The private review contains **48 source contact sheets**: each accepted model reference beside the rules-only and current rules-plus-classifiers selections, plus twelve preserved sheets from before the Marqo export upgrade. The sheets use saved final plans and source previews, without reselection. They are not exact video trim frames. Private evidence stays under `output/reader-matrix-20260912.private/`; public aggregate numbers are in [the data file](2026-09-12-capability-matrix.data.json).

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

The first two columns are new runs on the Mac. “First” means first invocation of that route in a serial matrix: the first metadata route paid a cold preview/pixel pass, and later overlapping routes could reuse it. The final classifier arm began with existing context/Docling facts and paid a Marqo ONNX det-v2 backfill. The earlier Docling-v2 backfill run is retained in the data file. Neither column is a universal cold-start estimate. Repeat includes discovery and the real CLI; person discovery still takes about 30 seconds.

The last column is the historical accepted model invocation. Its preparation and semantic caches were mixed, so it is context rather than a controlled speedup ratio. No new hosted inference was purchased for this matrix.

| Product / control | Metadata first / repeat | Classifiers first / repeat | Historical accepted model |
| --- | ---: | ---: | ---: |
| monthly | 49.2 / 1.8 | 54.5 / 3.4 | 8.3 |
| person | 432.9 / 30.7 | 473.0 / 34.4 | 40.6 |
| multi_person | 32.1 / 31.4 | 35.8 / 34.8 | 282.7 |
| special_day | 5.5 / 0.7 | 6.1 / 1.8 | 191.9 |
| trip | 15.9 / 1.8 | 12.0 / 2.7 | 645.7 |
| year | 39.3 / 1.9 | 35.0 / 3.0 | 1680.6 |
| season | 18.1 / 1.1 | 16.9 / 2.1 | 1507.0 |
| holiday | 17.8 / 1.3 | 17.5 / 2.3 | 1132.7 |
| on_this_day | 12.8 / 1.9 | 12.1 / 2.9 | 540.7 |
| album | 0.8 / 0.8 | 3.1 / 1.6 | 101.4 |
| june_control | 4.9 / 1.8 | 6.0 / 2.8 | 4.3 |
| february_long_control | 2.0 / 2.0 | 3.2 / 3.2 | 33.1 |

All rules rows cost **$0 in model API charges**. Electricity was not metered. To price it honestly, use measured average watts × wall seconds ÷ 3,600,000 × local currency/kWh; add hardware and storage separately. The [earlier February comparison](2026-09-12-deployment-costs.md#local-versus-hosted-reading) measured a hosted GPT-4.1-mini selection at **537.4 s and approximately $0.256** from recorded tokens. That one monthly price must not be multiplied blindly across trips or year reviews. The other hosted product prices remain unmeasured.

The final ONNX recheck selected exactly the same ordered assets for all twelve routes as the earlier classifier arm. Both hash seeds agreed, with zero LLM calls. The extra first-run cost is fact rebuilding, not a different edit; the quality findings below are unchanged.

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

## Visual review and product positioning

All 36 current source sheets and all ten finished-film sheets were inspected and shown in
the private review conversation. These are qualitative judgements about this library, not
blind ratings or a claim that the historical model reference is always better.

| Product | What works without inference | What the sheets actually lose |
| --- | --- | --- |
| Person | Visits, feeding, walks and the child growing through the year remain recognisable. | Some tighter portraits and interactions become weaker alternatives from the same occasion. |
| Multiple people | The relationship itself supplies a strong theme; most major gatherings survive. | Some extra scenes weaken subject focus; semantic interpretation and Live Photo motion remain absent. |
| Album | Owner curation supplies intent. The brewing album retains a readable process from grain to fermentation. | Two near-duplicate process frames remain; the model reference is much shorter, not clearly more informative. |
| Special day | The festival still has performers, the crowd and personal selfies across the day. | Classifiers reduce the cut from 54 to 40 seconds and lose both-person selfies and the final performance. |
| Trip | Metadata keeps the travellers and an intelligible journey; classifiers restore caves, architecture and landscapes. | Metadata overweights repeated portraits; classifiers shift too far toward scenery in places. The model mixes people and setting more evenly. |
| Monthly | The birthday, hospital stay and first days at home remain visible. | The short cut opens with incidental objects and ends on the 18th; the model reference reaches the 26th. |
| June control | Individual family and baby photos are attractive. | Both rules cuts spend all 15 slots by June 8, omitting the later cycling and month-end gathering visible in the reference. This is a temporal allocation problem, not an inherent need for an LLM. |
| Longer February | More room restores coverage through month-end and a recognisable birth-and-homecoming sequence. | Incidental objects remain. The classifier sheet includes a medical-chart photo and a dirty nappy; detection alone does not supply audience judgement. |
| Year | Metadata spans the year and retains several outings and family events. | It also selects a photographed identity card. Classifiers exclude that frame but add household and administrative clutter. |
| Season | The shorter metadata cut retains people, outings, a concert and a birthday. | The full-length classifier cut is dominated by household objects, garden progress and car pictures. Longer is worse here. |
| Holiday | Family, play and familiar holiday activities remain. | Several reference outings and interactions disappear; an empty-room video preview survives. The five-year title range obscures the recurring-holiday premise. |
| On this day | Both rules modes retain evidence of all six known reference occasions, sometimes through alternative photos. | Classifiers add stairs, a meter, a tool and food. These fill time without strengthening the memory. |

The useful product distinction is **prefiltered memories versus broad recaps**. Person,
album and event requests already constrain meaning; trips constrain both place and time.
Rules are a credible fast option there. Broad month/year/season recaps need the intelligence
layer to triage a mixed camera roll and make sense of it. This matrix does not support
marketing classifiers as a consistent editorial upgrade. Nor does it establish unattended
sharing for either reduced mode. Temporal coverage, favourite retention, sensitive records
and the balance of people versus scenery are recorded decisions for a later editorial pass.

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

Kubernetes proved actual CUDA DINO inference and a short NVENC encode. Its benchmark used a temporary source/dependency overlay, not a reproducible release image. The temporary Pod and credential Secret were removed after collecting evidence. The ONNX/service combination now passes local CI. Actual CPU and CUDA images build and run as UID/GID 1000 without PyTorch; the CPU image is 816,461,586 bytes and the CUDA image is 5,671,291,490 bytes. The CUDA image uses ONNX Runtime GPU 1.26 with CUDA 12/cuDNN 9. The published CUDA image was subsequently verified by immutable digest on `rancher-worker-couronne-03`: CUDAExecutionProvider, ORT GPU 1.26.0, both det-v2 detectors, six heads, no PyTorch, and successful cold/warm HTTP requests using seeded synthetic pixels. This checks execution, not semantic accuracy or real-library throughput.

| Published CUDA producer | First request, seconds | Repeat, seconds |
| --- | ---: | ---: |
| DINO + six heads | 0.961 | 0.018 |
| Marqo | 0.299 | 0.127 |
| Docling | 1.038 | 0.035 |

Models were staged before these requests; download and image-pull time are excluded.
The tested image was `ghcr.io/sam-dumont/immich-video-memory-generator/inference@sha256:8a954937003cd79d9f5f120e451296881a7416753f477b4b03259ffb4f54f7ee`.
Two attempts on couronne-01 were evicted for node ephemeral-storage pressure before execution. The successful retry used a 256 MiB memory-backed model cache; all temporary pods were deleted. No credentials or private photographs were needed for this check. Existing application cache/output claims use `proxmox-data-xfs`; container images still occupy node-local storage.

Image publication also exposed two release-workflow errors: digest artifact filenames contained a forbidden colon, and the intentionally skipped version-release ancestor skipped the inference manifest. Digest export/import now round-trips portable filenames, and the manifest explicitly requires successful image builds while allowing the intentionally skipped ancestor.

## Implementation and release review

The February reader now accepts all ten standard products. Preflight follows the same reader and preparation tier, so metadata-only rules neither probe model endpoints nor require unused model files; an explicitly requested model reader still needs a model name. A runtime test exercises their real selector and render projection without constructing model ports. A custom semantic request is refused before opening a store. The album matrix exposed a root cause: the owner’s album choice was not treated as editorial intent, so an unstarred album could select nothing. Album curation now supplies worthiness and ordinary standing after technical/source/audience gates. Its saved matrix cut contains ten clips.

The obsolete triage-constructor test was removed; existing preparation/runtime tests already cover banking in the library cache and warm producer reuse through the current API. The new comparison regression proves that a different asset in a complete reference inventory still retains its occasion. It also distinguishes missing inventories from proven losses.

Local `make ci` passed: **6,819 tests** for the combined capability branch and **6,773 tests** for the ONNX/service integration, with seven skips each. The documentation builds and staged privacy checks passed. Final release integration is tracked in [PR #813](https://github.com/sam-dumont/immich-video-memory-generator/pull/813). Historical merge-message lint now accepts the existing lowercase merge prefix, and the inference health test covers both available and absent optional ONNX Runtime. The new rules path is a measured degraded option; the findings above do not satisfy the design’s equal-quality release bar. The caption follow-up is included; the broader UX rework remains the next phase.

## Caption follow-up

The requested caption change uses 48 px text at 1080p (previously 67 px), 85% opacity,
and a lighter outline. Dates and places are deduplicated independently. Known hidden
locations reset place changes; missing metadata does not. Original EXIF names and GPS
remain available to maps.

Familiarity uses a 250 m circle around each asset and attendance spread across years,
not city names or photo volume. Monthly recurrence over two years or sparser recurrence
across five years qualifies; an annual holiday burst does not. The full local metadata
scan covered 111,029 assets, 84,959 with GPS, in 112.5 seconds. That scan is separate
from the earlier matrix timings. A private, credential-scoped seven-day cache avoids
repeating it for each film. The configured home circle supplies the home country from
GPS history; unknown country evidence retains the original label. Tests and the caption
preview use the Royal Palace in Brussels as their home fixture, never a personal address.
The full CLI album follow-up passed complete decoding with the identical selection and
zero LLM calls: 184.1 seconds including its first GPS history scan. A subsequent history
load took 0.03 seconds for 38,881 deduplicated observations. Its smaller first-draft text
was rejected in visual review; the revised 48 px / 85% proof was rendered separately.
The ten benchmark films above preserve the pre-change presentation and timing evidence.

## Questions parked for review

- Is the shorter people-heavy season cut preferable to the longer classifier cut? Do not tune it by runtime alone.
- Should rules bias harder toward favourites and later-period coverage? June and long February expose the current tradeoff.
- Review assets the model refused before claiming audience parity. Keep the raw decisions private.
- Decide whether the declared 100% occasion-recall bar is a requirement for the optional rules mode, or a goal for a later editor revision. Missing inventories need human review.
- The standalone inference service serves facts, but application wiring to consume those facts remains its planned next slice. This release does not claim remote classifier preparation through that service.
- Measure hosted cost on the remaining products and render throughput on NAS/Kubernetes before publishing those cells as supported performance claims.
- The multi-person opening displays its raw Boolean request and overlaps the year. Holiday/on-this-day date ranges and mixed-language trip titles also need the planned UX/localization review.
- Recheck gain-map transfer curves against Apple's Rec.709 reconstruction guidance in a later colour review. This pass fixes metadata units and missing-data handling; it does not replace the previously validated reconstruction algorithm.
