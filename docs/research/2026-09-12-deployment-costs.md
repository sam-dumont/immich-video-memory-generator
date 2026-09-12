---
date: 2026-09-12
status: measured results and explicit gaps; not a merge recommendation
---

# February 2024: deployment cost and privacy

**Historical checkpoint.** The owner subsequently authorized the February rule-reader spike,
Kubernetes benchmarks and shared people context. The [phase 5 readiness follow-up](2026-09-12-phase5-readiness.md)
supersedes this report's “not implemented” and “Kubernetes unmeasured” statements. The original
measurements below are retained with their original cache and context conditions.

**The Mac completed a cold, no-captions selection in 29 min 9 s. After fixing Docling's NAS classifications, the NAS plus Mac reader completed in 25 min 13 s, selecting 14 clips. That run rebuilt Docling facts and reused the other prepared facts. Hosted reading completed in 8 min 57 s on the Mac. A complete full-tier Mac run and a Kubernetes run remain unmeasured.**

This finishes the measurement review left in Claude session `7add5002-a463-48cb-b49f-876aaa001062`, benchmark worker `a9ad419da87687a6f`. The follow-up below records the owner's subsequent request to fix the NAS failure, the focused detector fix and its completed rerun. It does not implement the rule reader or make a release decision.

The common request is **February 2024, 60 seconds, monthly highlights, no rendering and no music**. Discovery found 285 videos and 1,730 photos; preparation requested 1,440 pictures. Original source under measurement: `feat/reduced-tiers`, commit `398b669c`, which includes the preparation tiers. The successful NAS follow-up adds the uncommitted Docling fix, whose three source hashes are in the data file. This is not a measurement of the combined open PR stack or its ONNX detector replacement.

## The four requested configurations

| Configuration | Measured state | Time and outcome | Personal data sent to inference |
|---|---|---|---|
| 1. Everything on the M5 Max | Cold picture facts, `no_captions`; local Qwen reader | Preparation **153.7 s**; whole selection **1,749.0 s**, **14 clips**. The requested **full** configuration was not completed: the caption server rejected its pinned model, and a separate full-tier run using existing facts failed during reading. | Reader requests stay on the Mac. Immich remains a separate configured service. |
| 2. NAS alone, no external inference | DS423+, J4125; `no_captions`; reader deliberately unreachable | All 1,440 pictures prepared across two attempts. Successful stage time sums to **2,297.1 s (38 min 17 s)**, not a measured uninterrupted run. Final command exits **1**, **zero clips**. | No reader payload was sent. This is not proof of a generally offline product. |
| 3. NAS with an external reader | Same NAS; Mac oMLX reader; `no_captions`; Docling fix applied | **1,512.505 s (25 min 13 s)** container lifetime, exit **0**, **14 clips**, **52.5 s content**. Includes **227.217 s** rebuilding Docling; other facts were warm. The earlier unfixed attempt failed in 6.4 s with zero clips. | Personal annotation text and selected pictures were sent to the Mac on the LAN. The fixed run logged **198 successful reader HTTP responses**. |
| 4. Kubernetes with external inference | Preflight and existing deployment inspection only | **Not measured.** Target node had **15.60 GiB free**, below the benchmark's **30 GB minimum**. No new benchmark pod was run. | Same reader payload; offloading picture preparation would also send its image tiles to the ML service. |

The NAS's source file hash was checked against the Mac worktree. Its benchmark uses a source mount inside an image, not a newly published release. The first image lacked the detector dependencies; the successful detector pass used the existing `immich-memories-editorial:test` image and a readable model cache. Changing the image and retrying is why its stage sum is not an end-to-end cold time.

### Approved NAS-to-Mac follow-up

The exact NAS-to-Mac test was approved and run on September 12. The existing image, source mount and isolated benchmark cache were reused, with memory capped at 4 GB. The container ran from **10:25:00.613 to 10:25:07.003 UTC**, exited 1 and was not OOM-killed. It checked all **1,440 previews**, produced no new facts, and failed with `Pipeline selected no clips`.

Its durable attempt still says **complete**, with the last stage `Preparing previews: 1440/1440`; no plan file was written. There were **zero reader HTTP requests in the log** and no logged reading stage. Configuring the external reader therefore did not get this run past the earlier failure. It does not establish reader connectivity, throughput or a successful NAS cut.

### Follow-up fix: Docling on the NAS CPU

The owner then asked to fix the failure. The source gate had excluded **all 1,440 eligible pictures** as `screen-docling:table`. Their Docling confidence was **0.05294–0.05296**. The Mac had labeled **1,417 photographs**, with only four tables, over the same pictures.

Both machines used ONNX Runtime **1.28.0** and the same pinned model bytes (SHA-256 `acba68df0a2f149212f5b5082d98a81700c93280e39a73dca095040ef19a583f`). Three synthetic inputs isolated the failure without private image fixtures:

| Input | NAS, default optimizer | NAS and Mac, extended optimizer |
|---|---|---|
| White image | table, 0.052955 | table, 0.862082 |
| Black image | table, 0.052960 | table, 0.943777 |
| Seeded RGB noise | table, 0.052950 | other, 0.798308 |

Basic, extended and disabled optimization agreed on the NAS. Only the default `ORT_ENABLE_ALL` result collapsed. The Mac agreed across all four settings. The fix sets Docling to `ORT_ENABLE_EXTENDED`, retaining basic and extended optimizations while omitting the CPU layout stage. This level distinction is documented by [ONNX Runtime](https://onnxruntime.ai/docs/performance/model-optimizations/graph-optimizations.html); the failure itself was reproduced on the J4125, not inferred from the documentation.

Docling facts now use **`det-v2`**. Old saved configurations naming its `det-v1` recipe migrate on load. Other head versions stay unchanged. Preparation recomputes the retired Docling facts, keeps their old rows for diagnosis, then reuses the corrected rows on subsequent runs. No source or audience filter was loosened. Reader-bank keys also change with the annotation producer version.

The real-model regression failed on the NAS before the fix and passed after it; it also passed on the Mac. Focused preparation, cache reuse and saved-config tests passed (**38 tests**). The fixed end-to-end rerun uses the same February request, NAS image, 4 GB memory cap, isolated cache and approved Mac reader, with the three source-file changes above.

**The fixed run completed.** Its container ran from **10:47:53.948 to 11:13:06.454 UTC**: **1,512.505 s**. It exited **0**, was not OOM-killed, and wrote a plan plus a completed attempt with outcome `selected`. Final timing validation accepted **14 clips / 52.5 s content**, with **zero content-budget shortfall** and **zero render-projection adjustments**. The target remains 60 seconds with title/transition allowance; no MP4 was rendered.

The log contains **198 reader HTTP responses, all 200**, and no errors. The picture-facts pass reports **49 images sent**. Its counts and the planner's token metrics are separate partial collectors, not a full-run bill. The measured path from `Reading event evidence` to `Editorial selection complete` took **1,278.230 s (21 min 18 s)**. This is a successful NAS-to-Mac selection, with mixed preparation-cache state and a reader server that had already served the earlier comparisons; it is not a cold deployment comparison or a quality judgement.

Rebuilding the 1,440 Docling facts cost **227.217 s**, plus **0.418 s** for warm preview verification. No other facts were produced. All **1,440 asset-to-label assignments match the original Mac run exactly**; their sorted assignment digest is `dcd201a06510a03359d0878faadf4c12dd5f3551e25b820525c5e5a29d556432`. The source gate now rejects **22** screen/document pictures, leaving **1,418** candidates. The run reached `Reading event evidence` at **10:51:47 UTC**, and the Mac reader returned HTTP 200.

All non-test `make ci` gates passed. The full suite recorded **6,700 passed, 7 skipped and 2 failed**. The Docker-context failure came from the required graph refresh creating 70 MB of local analysis output; after retaining that output outside the checkout, both Docker-context tests passed. The remaining failure is the existing `test_triage_wiring.py` expectation of a removed `SmartPipeline(triage=...)` argument. It remains outside this detector fix.

## What preparation cost

All rows below cover the same 1,440 pictures. Model weights were already present; “cold” means the demanded per-picture facts were absent. It does **not** include image downloads, installation or model downloads outside the preparation stage.

| Stage | M5 Max, cold facts, `no_captions` | J4125 NAS, cold facts recovered across two attempts |
|---|---:|---:|
| Preview fetch/verification | 24.9 s | 22.3 s |
| Pixel facts | 15.9 s | 188.9 s |
| DINOv2 and six context heads | 38.0 s | 1,120.0 s |
| Two detectors | 74.9 s | 966.0 s |
| Sum of successful stages | **153.7 s** | **2,297.1 s** |

The detector counter is **2,880 units**, two per picture. Its NAS log's `0.335 s/pic` divides by those units. The cost per original picture is **0.671 s**, not 0.335 s. Both detectors wrote 1,440 facts, but the original NAS Docling facts were wrong. These are recorded execution costs, not the cost of a correctly prepared cold NAS run.

The Mac had other work running. No repeated, isolated machine comparison was completed for this month, and no hardware speedup ratio is claimed.

A separate Mac `metadata_only` attempt paid **27.9 s previews + 35.6 s pixels = 63.6 s**. It failed with no readable episode evidence because its reader endpoint was deliberately unreachable. This establishes the cost of those producers, not an acceptable metadata-only cut.

A full-tier attempt with an already populated fact store paid **33.3 s** to re-verify previews and produced no new facts. It then failed during reading. The earlier draft called this the “second memory” cost; that was incorrect. It is one warm preparation measurement, not a completed memory.

### Captions: the missing full-tier measurement

The Mac's oMLX returned HTTP 409 for the pinned SmolVLM2 revision (“Unrecognized image processor”). The September 11 producer benchmark recorded a working separate MLX caption server at **0.206 s per cold picture**, and the NAS llama.cpp Q8_0 recheck recorded **30.9 s per picture**. Those used public photographic fixtures, different runtimes and different quantizations. They are context for deployment, not measured February full-tier totals and not a valid single-variable ratio.

The separate full-tier run used previously banked descriptions and failed before making a selection. It cannot supply the missing caption cost or prove that descriptions do not matter.

## Local versus hosted reading

Both arms run the application on the **Mac**, using prepared `no_captions` facts copied from the first run. They change the reader endpoint, which is part of the producer identity and causes fresh reading. Picture preparation is warm; model-server prompt caching is visible in the usage counts. This is not a cold installation comparison.

The hosted arm used **GPT-4.1 mini**. After rejected setup attempts, the successful invocation ran from 11:01:07 to 11:10:04 local time: **537.4 s** for the whole invocation, **535.2 s** spanning recorded requests and **514.8 s** summed successful request duration. It selected **15 clips**.

| Hosted reader, successful invocation | Measured |
|---|---:|
| Successful requests / errors | 170 / 0 |
| Input tokens | 490,416 |
| Cached input tokens, included above | 20,224 |
| Output tokens | 41,203 |
| Image parts sent | 48 |
| Request bodies, including images | 7.574 MB |
| Base64 image payload within those bodies | 5.998 MB |
| Estimated model charge | **$0.2560** |

The charge uses $0.40 per million uncached input tokens, $0.10 cached input and $1.60 output, checked against the [official GPT-4.1 mini model page](https://developers.openai.com/api/docs/models/gpt-4.1-mini). It is token-based arithmetic, not an invoice. It excludes setup failures, electricity, hardware and other services.

The local Qwen3-VL-30B-A3B-Instruct-4bit arm also completed: **1,451.9 s (24 min 12 s)** for the invocation and **15 clips**. Its recorded request span was **1,448.3 s (24 min 8 s)**, with **1,435.2 s** summed request duration. It made **182 successful requests**, sent **48 image parts**, and used **581,696 input tokens** (245,248 cached) plus **59,226 output tokens**. Request bodies totalled **7.636 MB**, of which **5.944 MB** was base64 image payload. No electricity or hardware cost was measured; a zero provider invoice is not a zero operating cost.

The hosted setup rejected `chat_template_kwargs`, then `repetition_penalty` and `repetition_context_size`. The existing `llm.drop_params` option allowed the successful run. No application code was patched to accommodate it. Failed setup logs were kept separately; they are not mixed into the 170-request table.

**This does not isolate the cost of hosting.** The model, tokenizer, sampling compatibility and generated decisions differ. The request templates and scope are shared, but later prompts follow earlier answers, so the two complete request streams are not identical. Both final arms selected **15 clips and 52.5 seconds**, but only **4 assets are shared**; each chose eleven the other did not. Equal counts are not evidence of equal occasion coverage. No owner quality comparison was completed, and none is implied by a faster response.

## What the tiers change

`full` asks for captions, heads, detectors and pixel facts. `no_captions` skips caption production and retains the normal audience evidence. `metadata_only` omits the ML producers and uses the conservative family gate.

A tier controls **what gets produced**, not a clean erasure of facts already banked. Existing descriptions can still appear in annotation lines at `no_captions`. Comparing tiers over a populated store therefore does not establish the quality loss from having no captions. In these runs, no complete matched three-tier quality comparison was obtained.

The measured conclusion is narrow: omitting expensive producers reduces preparation work. Whether it loses an occasion remains unanswered. The no-LLM rule reader is still a design; neither lower preparation tier supplies one.

## What leaves the machine

The reader receives mostly text: episode evidence, the period account, story grouping and weights, and selection decisions. Annotation text can include real names, relationships, ages, timestamps, place names, and GPS rounded to three decimals when no named place is available. Some prompt surfaces also carry birth dates or an album title. Removing Immich IDs does not anonymize that text.

Visual reading passes send selected pictures at up to **800 px**, sampled pairs as two smaller tiles, or motion filmstrips. The captioner receives **400 px** JPEGs and a fixed prompt with no annotation metadata. A local reader keeps these inference payloads on the chosen machine; a hosted reader receives both the personal text and selected pixels.

Code references on the measured branch: `analysis/annotation_lines.py`, `text_period_wire.py`, `editorial_moment_wall.py`, `editorial_picture_facts.py`, `editorial_description_wire.py`, and `editorial_preparation_captions.py`.

A broader “nothing leaves the NAS” claim would be wrong. Other product paths request trip geocoding, map tiles and fonts. They were not the February no-render benchmark. The reranker also has its own endpoint configuration; reader-proxy byte counts do not measure every possible service in the application.

## Decisions the evidence supports

- **NAS, no GPU, no external reader:** preparation works; a finished memory does not. Do not advertise the no-LLM profile as implemented.
- **NAS plus a spare GPU box:** the corrected NAS preparation plus Mac reader now completes this February selection. Offloading picture preparation through the separate ML-service PR remains unmeasured here; this run prepared the facts on the NAS itself.
- **Willing to use hosted reading:** the successful GPT-4.1 mini arm gives a concrete cost, about 26 cents for this run. It sends personal text and pixels off-box. Quality relative to the local result still needs review.
- **Existing M5 Mac:** the measured no-captions path completed. A working full-tier caption endpoint is still needed to measure the requested full configuration.

## Questions and oddities to settle later

1. The original empty NAS selections came from the Docling optimizer failure documented above. A separate status oddity remains: a legitimate empty selection can be recorded as a complete attempt before the CLI exits with an error. This fix does not change that status contract.
2. The NAS external-reader path now completes with the focused fix. A fully cold corrected NAS run, the full-tier Mac and Kubernetes still have the explicit gaps above. No successful-run time was invented for them.
3. The four-way comparison also omits rendering and music by design. It cannot price a finished MP4 or choose a GPU encoder.
4. The existing cluster image's ONNX Runtime advertised CPU and Azure providers, while torch could see CUDA. CUDA-capable hardware does not establish that the ONNX seats are using it.
5. `feat/marqo-onnx` currently moves **Marqo** to `det-v2` and leaves Docling at `det-v1`. This fix moves **Docling** to `det-v2` and leaves Marqo at `det-v1`. Versions are per head, so the names do not collide, but integrating the branches must preserve both recipes and Docling's optimizer setting. These measurements are not performance of that eventual merged stack.
6. The benchmark caption proxy had to alias a model name for discovery; that did not make the pinned model load successfully. No successful caption compatibility claim follows.
7. Earlier NAS tier tables and other `doc_docling@det-v1` stores need a separate accuracy review. This fix retires those facts for new runs; it does not retroactively certify earlier selections or rewrite their measured costs.

## Evidence and reproduction

Aggregate measurements and source hashes: [2026-09-12-deployment-costs.data.json](2026-09-12-deployment-costs.data.json). The raw benchmark logs and attempt files remain in the original private scratch directory, under the session and worker named above. They contain personal data and are not copied into the repository.

The product invocation was:

```bash
immich-memories generate --memory-type monthly_highlights --year 2024 --month 2 \
  --duration 60 --no-render --no-music
```

Each cold arm overrides `editorial.annotation_database` and `cache.directory` to isolated benchmark paths. Costs come from `preparation.private.json`, timestamped CLI logs, and a recording HTTP proxy that retained counts, sizes, status and token usage. The proxy did not retain request/response bodies. A changed endpoint re-keys readings; a copied store is not necessarily a cold prompt cache.
