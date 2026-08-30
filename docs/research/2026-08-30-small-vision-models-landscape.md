---
date: 2026-08-30
status: research — landscape sweep + two punch-list defects found in-tree
issue: none-yet
---

# Small/mid vision-language models, February–August 2026

Can a model at or below 10B take either of the two big remaining jobs from the
incumbent ~27B (`Qwen/Qwen3.8-27B`, served via omlx/MLX)?

- **Job 1 — cards.** Structured factual scene descriptions (who/what/where/action)
  read off a 400px composite tile sheet, emitted as parseable JSON at temperature 0.
- **Job 2 — audit.** Set-level judgments over contact sheets of at most 12 tiles.

Every number below is tagged `[measured]` (observed on Apple-silicon hardware in a
dated public sweep, or verified directly in this tree), `[cited-YYYY-MM]` (a figure
from a dated source), or `[EST]` (my arithmetic, not observed).

---

## Verdict

**Cards: yes, `Qwen3.5-9B` is a credible swap — but bill it honestly at 1.3x, not 3x.**
It matched or beat the incumbent in three independent temperature-0 sweeps, is
Apache-2.0, and halves peak memory from 21 GB to 10 GB. The end-to-end speed gain is
small; the win is memory and licence headroom.

**Audit: no. The 27B stays the floor.** The deficit at this size is concentrated in
hard instruction-following and judge reliability, which is exactly what a set-level
call is.

**But the two highest-value changes in this document are not a model swap.** They are
the schema fix in §2 and the two fail-silent defects in §1, both of which apply to the
27B today.

---

## 1. Punch list: two fail-silent paths in this repository

These are defects, not research findings. Both fail by producing plausible output
rather than raising, which is why neither shows up in logs as a failure.

### 1.1 Images are sent as base64 data-URIs — the pattern with an open upstream bug on this exact model family

**Receipt.** `src/immich_memories/analysis/llm_query.py:519-537`, function `_openai_content`:

```python
# llm_query.py:529-532
"type": "image_url",
"image_url": {
    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("utf-8"),
```

Consumed by `_query_openai` (`llm_query.py:540`), which POSTs to
`{base_url}/chat/completions`.

**What silently fails.** mlx-vlm issue
[#1925](https://github.com/Blaizzy/mlx-vlm/issues/1925) — **open**, filed 2026-08-16,
last updated 2026-08-25 `[cited-2026-08]` — reports that through `mlx_vlm.server`'s
OpenAI-compatible `/v1/chat/completions`, a base64 `data:` URI yields **zero image
tokens** on `mlx-community/Qwen3.8-27B-4bit` and `lmstudio-community/Qwen3.8-27B-MLX-4bit`.
The model emits a repetition loop, returns **empty `content`** with
`finish_reason: length`, and the *identical image passed as a file path* describes
correctly. Upstream cross-reference: `lmstudio-ai/mlx-engine#325`.

**Why this is more than theoretical here.** This tree already carries a retry
workaround for precisely that symptom `[measured]`:

```
llm_query.py:591   # Retry up to 3x — some models (Qwen/mlx-vlm) return null content
llm_query.py:630   logger.debug("LLM null content (attempt %d/3)", attempt + 1)
llm_query.py:631   msg = "LLM returned null content after 3 retries"
```

"Qwen/mlx-vlm returns null content" is the #1925 signature. The retry masks it: a call
that silently dropped its image either retries into a different sample, or fails after
three attempts, and never reports *why*.

**How I verified it.** Read the source at the cited lines; read #1925's body and
reproduction script via the GitHub API on 2026-08-30. **Not verified:** whether omlx
(which serves this project and pins mlx-vlm to commit `78b96eb5`, dated 2026-06-28)
decodes base64 itself before mlx-vlm sees it. omlx's README claims its own
"base64/URL/file image inputs" handling, so it may be immune. **This is the open
question, and it is a ten-minute check, not a rewrite.**

**Minimal fix / check.** Send one known image twice against the live server — once as
the current data-URI, once as a filesystem path — with an identical prompt at
temperature 0, and diff the descriptions. If they differ materially, or if the
data-URI run produces empty `content`, switch `_openai_content` to emit a path (or a
`file://` URL) for the local-MLX transport. Independently: promote
`llm_query.py:630` from `logger.debug` to `logger.warning` and include the model id,
so a silent image drop stops being invisible.

### 1.2 JSON is requested by prompt, never enforced — and the repair ladder hides it

**Receipt.** `response_format`, `json_schema`, `guided_json` and `grammar` appear
**zero times** anywhere under `src/` `[measured]`:

```console
$ grep -rn 'response_format' src/ --include '*.py' | wc -l
0
```

The outgoing payload is built at `llm_query.py:556-569` and carries only `model`,
`messages`, `max_tokens`, `temperature` (plus thinking params). No schema is attached.

Downstream, `src/immich_memories/analysis/llm_response_parser.py` compensates with a
hand-rolled repair ladder — six separate `json.loads` attempts across
`:352, :360, :367, :372, :401`, including fence-stripping (`:345`), first-element
extraction from arrays (`_parse_json_array_text`, `:349`), brace-balancing for
truncated output (`_parse_json_object_text`, `:364-373`, which logs
`"Fixed truncated JSON by adding closing brace"`), and a substring scan for JSON
embedded in prose (`:397-401`).

**What silently fails.** The ladder is load-bearing, which means malformed model output
is routine and is being *repaired* rather than *counted*. A brace appended to a
truncated object produces a syntactically valid card whose trailing fields are missing
or wrong, and nothing distinguishes it from a clean parse. There is no metric for how
often repair fires.

**The available enforcement, and the trap.** `llguidance>=1.7.0` is a **mandatory**
dependency of mlx-vlm 0.6.17; `mlx_vlm/structured.py` performs real token-level masking
and is exposed as OpenAI `response_format: {"type": "json_schema", "strict": true}`,
documented to work on multimodal requests `[cited-2026-08]`. **But do not simply switch
it on under omlx.** omlx does not use llguidance — it uses `xgrammar` as an *optional*
extra, and when unavailable `grammar_compiler` returns `None` and `response_format`
routes through a **prompt-injection fallback logged at `logger.info`** `[cited-2026-08]`.
The model is then merely *asked* for JSON. That is a fail-open path of exactly the kind
this project has been bitten by before. vLLM has its own version: `guided_json` was
removed in v0.12.0 and is now **silently ignored** (vLLM issue #53975, open).

**Minimal fix.** (a) Add a parse-failure counter around the repair ladder and log at
`warning` when any rung past the first fires — you cannot manage what you do not count.
(b) Attach `response_format` with the card schema. (c) **Assert the constraint is live**:
send one request whose schema forbids the model's natural output and confirm the server
*cannot* produce it. If it can, the grammar is not attached and you are on the
prompt-injection fallback. Do this before trusting any parse-rate improvement.

---

## 2. The schema finding: required fields manufacture facts, and constrained decoding does not stop it

This is the most consequential result in the literature sweep, and it lands directly on
the stated requirement of "no invented people/relations".

**PhantomFill**, arXiv 2607.20492v2, 2026-06-11 (upd. 2026-07-27) `[cited-2026-06]`.
Thirteen models, unanswerable questions, only the answer *format* varied:

- In free text, GPT-5.5 correctly reports there is no data **98%** of the time.
  Given a **required JSON field**, the same model invents an answer **40/40**.
- **Required fields drive fabrication to 100% in ten of thirteen models.**
- Under grammar-constrained decoding, with an escape token *guaranteed reachable by the
  sampler*, five open-weight models spent it **zero times out of 203 trials** on the
  fields that carry the fabrication — and twelve times on the one field where escaping
  conceded nothing. They can emit the token; they decline to spend it where it costs
  them an answer.
- A direct instruction not to infer is **overridden by the schema in four of six models**.
- **"Resistance does not come with scale"** — within one family the smallest model
  refuses, the mid-sized fabricates, the largest refuses again.

**Read for the card task.** A required `people` or `action` field will manufacture
people on ambiguous tiles at close to 100%, on open weights, at any size, and
llguidance will not prevent it. Grammar fixes *syntax*; fabrication is a *semantics*
failure and is untouched.

### The one-line schema fix

Every card field that can be guessed must have a costless way to decline. Concretely,
for each such field either make it optional in the schema, or admit a sentinel:

```json
"people": {
  "anyOf": [
    {"type": "array", "items": {"type": "string"}},
    {"const": "insufficient_evidence"}
  ]
}
```

The load-bearing property is **costless**: the escape must be a first-class schema value,
not prose permission, and the model must not lose anything by taking it — PhantomFill
shows the escape *is* spent on fields where declining concedes nothing. Apply the same
to `action`, and to any relation field. **Then re-measure the 27B's invented-people rate
before considering a swap.** If the schema alone fixes it, the model was never the
problem.

Two supporting results. Asking for JSON *at all* collapses answer diversity — modal
answer **41% -> 64%**, distinct answers **52 -> 36**, surprisal **1.80 -> 1.58 bits**
(arXiv 2607.18476, 44 models) `[cited-2026-07]` — and decisively: *"enforcing the schema
at the decoder compresses no further than the request (-0.03 bits): the collapse lives
in the model's response to the register, not the decoder."* JSON register itself pushes
cards toward generic, modal descriptions. And the JSON penalty is a **capacity** tax
that scales with schema complexity: models with headroom absorb it, models near their
limits collapse (arXiv 2606.09410, Haiku -36.2 pp, p<0.0001) `[cited-2026-06]` — so it
bites hardest exactly where a ≤10B swap would put you. Their mitigation is
"think first, format later" (delayed structure), which recovers most of the loss.

**Actionable and specific to a Qwen stack:** *"Qwen models tend to benefit more from
schema-level instructions, whereas LLaMA models rely more on prompt-level guidance"*
(arXiv 2604.14862v2) `[cited-2026-04]` — changing only schema **key wording**, with
prompt and decoding fixed, measurably moves accuracy. This is the published form of the
in-house finding that prose hurts and structure helps: put the constraints in the key
names.

---

## 3. Shortlist

Speed columns are `[measured]` on Apple M5 Max, 4-bit, temperature 0, from the dated
mlx-vlm sweeps in §8. Prefill rate is prompt tok/s including vision encode.

| Model | Params | Release | MLX repo (exact) | CUDA path | License | Task-relevant evidence | Speed | Anchoring risk |
|---|---|---|---|---|---|---|---|---|
| **Qwen3.5-9B** | 9B dense | 2026-03-02 | `mlx-community/Qwen3.5-9B-MLX-4bit` (also `-4bit`/`-6bit`/`-8bit`); `lmstudio-community/Qwen3.5-9B-MLX-4bit` = 5.97 GB | vLLM `Qwen3_5ForConditionalGeneration`; GGUF `unsloth/Qwen3.5-9B-GGUF` | **Apache-2.0** | Clean/caveat/clean vs incumbent's clean/caveat/caveat across 3 sweeps — **never worse**. HallusionBench 69.3 vs 27B's 70.0; OCRBench 89.2 vs 89.4; IFBench 64.5 vs 76.5 | 307.9 prefill tok/s · 90.5 decode · **10 GB** | **LOW** — rewrote and *corrected* the supplied hint |
| **MiniCPM-o-4_5** | 9.37B | 2026-02-03 | `mlx-community/MiniCPM-o-4_5-4bit` (6.16 GB, **137 dl**) | vLLM `MiniCPMV`/`MiniCPMO` | **Apache-2.0** | Best published multi-image evidence at this size: MuirBench **72.0**, Mantis-Eval **79.7**, MMT-Bench 69.7, MM-IFEval 66.3, HallusionBench 63.2, MMHal-Hallrate 24.3 | not in sweep | untested |
| Qwen3.5-4B | 4B dense | 2026-03-02 | `mlx-community/Qwen3.5-4B-4bit`, `-MLX-4bit` | same as 9B | Apache-2.0 | **IFBench 59.2** — below the ~7B structured-output floor (§5) | not in sweep | **MEDIUM** |
| Ministral-3-3B-Instruct-2512 | 3B | 2025-10-31 (pre-2026) | `mlx-community/Ministral-3-3B-Instruct-2512-4bit` | vLLM `PixtralForConditionalGeneration` | Apache-2.0 | clean card **3/3 sweeps** | 189 tok/s decode · 6.4 GB · 2.8 s total | MED-HIGH (3B tier) |
| granite-4.0-3b-vision | 3B | 2026-03-03 | `mlx-community/granite-4.0-3b-vision-4bit` | vLLM `Granite4VisionForConditionalGeneration` | Apache-2.0 | clean 1/2 sweeps | **3,383 prefill tok/s** · 171 decode · **4.6 GB** | MED-HIGH |
| *middle class* gemma-4-12B-it | 11.95B dense | 2026-05-23 | `mlx-community/gemma-4-12B-it-qat-4bit` | vLLM `Gemma4UnifiedForConditionalGeneration` | **Apache-2.0** | 280-token image cap (§4); MMMU-Pro 69.1 | not in sweep | LOW-MED |
| *out of class, noted* gemma-4-26B-A4B-it | 25.2B / 3.8B active | 2026-03-11 | `mlx-community/gemma-4-26b-a4b-it-4bit` | same | **Apache-2.0** | clean **3/3** — only model both fast and clean | **1,440 prefill tok/s** · 130 decode · 16 GB · **4.1 s total** | LOW |

**Licence corrections worth recording.** Gemma 4 is **Apache-2.0**, not the Gemma Terms —
`ai.google.dev/gemma/docs/gemma_4_license` serves verbatim Apache 2.0 text and every
Google Gemma 4 repo carries `license:apache-2.0` `[cited-2026-08]`. Gemma 3 was
`license:gemma`; the custom terms were dropped at Gemma 4. Trap: some `mlx-community`
Gemma 4 conversions still carry a stale `license:gemma` tag in their own cardData, which
will mislead a licence scanner. Qwen3.5 is Apache-2.0 across all sizes.

**Structural fact that shapes the whole choice.** There is **no ≤10B Qwen after March
2026.** Qwen3.6 (April) and Qwen3.8 (August) shipped 27B and above only; Qwen3.7 shipped
no open weights at all (API-only) `[cited-2026-08]`. `Qwen3.5-9B` is the terminal small
model in the line, not a stale one. All three generations load through the same
`qwen3_5` module. `Qwen3.8-Flash-Next` is 125B total + 51B n-gram embeddings / 6B active
— outside the premise, on the Qwen Community Licence, and currently broken on MLX
(mlx-vlm #2041, open: *"loads cleanly on main but generates garbage"*).

---

## 4. Excluded, with reasons

**EU-banned — and one is mislabelled.** `tencent/Penguin-VL-8B` and `-2B` carry an
**`apache-2.0` facet on Hugging Face** while `LICENSE.txt` §0 reads *"IS NOT INTENDED FOR
USE WITHIN THE EUROPEAN UNION. IN THE EVENT OF ANY CONFLICT, THIS CLAUSE SHALL PREVAIL."*
`[cited-2026-08]`. Same exclusion for `tencent/Youtu-VL-4B-Instruct`,
`tencent/HunyuanOCR` (EU + UK + South Korea; §5(c) leaves even the *outputs* unlicensed
outside Territory), and Huawei `FreedomIntelligence/openPangu-VL-7B`. **A licence-facet
scan will not catch these** — the LICENSE file must be read.

**Licence (non-OSI).** `LFM2.5-VL` — LFM Open License 1.0 conditions commercial use on
**not exceeding $10M annual revenue**; excluded as a shipped default despite being the
fastest small model measured (203 tok/s at 4.0 GB). Moondream 3 (BSL 1.1) and
Moondream 3.1 (custom licence with a hosted-service carve-out) — and there is **no MLX
and no GGUF path to Moondream 3.1 at all**. Apple FastVLM — research licence, and
abandoned.

**Measured-bad at temperature 0** `[measured]`. `MiniCPM-V-4.6` unusable 2/2 ("missing
required fields") — note this is a **1.3B phone model, a different product line** from
the 9.37B MiniCPM-o 4.5, and its failure does not condemn the latter. `GLM-4.6V-Flash`
repetition-looped on 2026-08-30 despite being the strongest MIT option on paper — I
downgrade it below its paper reputation for this reason. `gemma-3n-E4B` empty response.
`FastVLM-0.5B` missing fields (at 344 tok/s — fast and useless). `SmolVLM2-2.2B` copied
the supplied hint verbatim. `jina-vlm`, `Idefics3-8B`, `Molmo2-8B` — loops or run-to-run
instability. `Kimi-VL-A3B-Thinking` — 225 s at 4.55 tok/s and 40 GB for an unusable
answer.

**No serving path.** Ovis 2.6 and Keye are CUDA-only (absent from both mlx-vlm and
llama.cpp). Ovis 2.6 is MoE-only (30B-A3B / 80B-A3B); no small dense variant exists.
`microsoft/Mage-VL` (4.74B, Apache-2.0) has no MLX conversion.

**Load crash.** `tencent/Youtu-VL-4B-Instruct` — its own repo code imports
`DefaultFastImageProcessorKwargs`, removed in transformers 5.16.

**Dead ends — stop searching.** No SmolVLM3 (HuggingFaceTB's last VLM is SmolVLM2,
2025-02). No Phi-5. No Apple FastVLM successor — Apple shipped no VLM in 2026. Pixtral
superseded by Ministral-3. InternVL dormant since 2025-10-11. GLM shipped only GLM-OCR
in 2026. No small VL from Moonshot or MiniMax. Baidu's 2026 VL output is OCR-only.

**Fixed, not excluded.** The `LFM2.5-VL` load failure — `ValueError: Received 600
parameters not in model` — was a key-prefix bug: the OptiQ export writes flattened bare
`model.` keys where `lfm2_vl` expects `language_model.model.`. Fixed in mlx-vlm **#2092,
merged 2026-08-29**, but **not in any tagged release** (latest tag v0.6.17, 2026-08-26),
so `pip install mlx-vlm` still reproduces it; needs install from `main`. Post-fix it
rates clean at 203 tok/s / 4.0 GB `[measured]`. Licence still excludes it.

---

## 5. The threshold literature: where ≤10B actually breaks

Three independent 2026 measurements bracket the target size, and they agree the failure
is **format compliance, not perception**.

- **VAREX**, arXiv 2603.15118v2, 2026-03-15 `[cited-2026-03]`. 1,777 documents, 1,771
  unique schemas, 20 models with attention to ≤4B. *"Below 4B parameters, structured
  output compliance — not extraction capability — is the dominant bottleneck."* Its named
  failure is **schema echo**: the model emits schema-conforming *structure* instead of
  extracted *values*, depressing scores by **45–65 percentage points**. That is the
  structural sibling of example-anchoring — the small model reproduces the scaffold it
  was shown. Encouragingly, extraction-specific fine-tuning at 2B buys **+81 pp**: the
  deficit is addressable without scale.
- **GraphRAG on consumer hardware**, arXiv 2605.20815, 2026-05-20 `[cited-2026-05]`.
  Four models on one 8 GB GPU; Phi-4-mini (3.8B) could not complete the pipeline due to
  structured-output errors. Concludes a practical threshold: *"models below approximately
  7B parameters fail to reliably produce valid structured outputs."* (n=4, one domain.)
- **When Correct Isn't Usable**, arXiv 2605.02363, 2026-05-04 `[cited-2026-05]`. Three
  7–9B models: naive prompting reaches **up to 85% task accuracy but 0% output accuracy**
  (correct *and* valid JSON). Constrained decoding *"enforces syntactic validity but
  incurs 3.6x–8.2x latency overhead and in several settings degrades task performance
  substantially."* **That latency tax is unaffordable on an already prefill-bound
  workload** (§8).

Schema-valid is not facts-right, measured twice: the Structured Output Benchmark
(arXiv 2604.25359, 21 models) finds *"near-perfect schema compliance, yet the best Value
Accuracy reaches only 83.0% on text, 67.2% on images"* — image-sourced extraction sits
~16 pp below text `[cited-2026-04]`. OrderBench (arXiv 2607.18261): 100% schema validity
with semantic success near 80% in the strongest model `[cited-2026-05]`.

**Where this places the candidates.** `Qwen3.5-9B` clears both thresholds.
`Qwen3.5-4B` clears the 4B one and sits below the ~7B one.

The vendor's own single-generation numbers show the same shape — perception saturates,
steerability does not `[cited-2026-03]`:

| Benchmark | 3.5-4B | 3.5-9B | 3.5-27B | 9B→27B |
|---|---|---|---|---|
| OCRBench | 85.0 | 89.2 | 89.4 | **+0.2** |
| CountBench | 96.3 | 97.2 | 97.8 | +0.6 |
| HallusionBench | 65.0 | 69.3 | 70.0 | **+0.7** |
| MMStar | 78.3 | 79.7 | 81.0 | +1.3 |
| MMMU | 77.6 | 78.4 | 82.3 | +3.9 |
| **IFBench** | **59.2** | **64.5** | **76.5** | **+12.0** |

`Qwen3.8-27B` scores IFBench **79.5**. The incumbent's moat is ~15 points of
instruction-holding, not eyesight. The mechanism is confirmed, not inferred: the 0.8B and
2B use smaller vision towers (768x12, 1024x24), but **every size from 4B up shares the
identical 1152x27 tower** `[cited-2026-08]`. The 4B, the 9B and the 27B have the same eyes.

---

## 6. Corroborated priors

Four in-house measurements now have published mechanisms or calibration.

**Example-anchoring (the 107/107 flip, and the 2B echoing a placeholder 138/198 times).**
No paper measures this in VLMs by size — see §10 — but the text analogue is precise. In
small models the **surface form** of a string present in context accounts for **83%** of
the damage; replacing the verbatim string with a runtime-generated description removes
**76%** of the effect, while an explicit "do not repeat" instruction changes the measured
quantity **not at all** (arXiv 2608.23651, 6 checkpoints 135M–1.7B; probability of
repeating a just-failed call rises **0.06 -> 0.54**; **greedy decoding reproduces it
token-for-token on 19% of items**) `[cited-2026-08]`. A 1.7B model regurgitates
demonstrations *"regardless of input"*, byte-identically across quantizations
(arXiv 2605.13538) `[cited-2026-05]`. Anchoring is **threshold-like**: in a
185,271-evaluation judge study, the *presence* of an anchor does most of the damage while
changing its value adds little, and *neither chain-of-thought nor an explicit "disregard
the metadata" warning reduces the effect* (arXiv 2608.25869, Cohen's d up to 0.71;
anchored metadata blocks **48%** of error corrections and flips **10.18%** of correct
judgments) `[cited-2026-08]`. **The lever is removal, not rewording** — which is what the
in-house finding already concluded.

**Tile-position bias.** The in-house observation that the model anchors on tile position
when ranking is published: *"skewed attention favoring initial images"* (arXiv 2605.02378)
and systematic positional bias in cross-image attention allocation `[cited-2026-05]`.
Worse for a contact sheet specifically: **cross-image information leakage is real and
delimiter tokens fail to block it** (arXiv 2602.01984, ICLR 2026) `[cited-2026-02]` —
and a composite tile sheet carries **no delimiter tokens at all**, so the signal that
paper strengthens is entirely absent from this input format. The only 2026 source that
mosaics frames names the cost outright: *"hallucinated game state, resolution loss from
mosaicking"* (arXiv 2608.14016) `[cited-2026-08]`.

**The ~95% self-agreement floor is healthy, not a defect.** Text judges at temperature 0
still flip **13.6%** of pairwise preferences, with 28% of questions above a 20% flip rate
(arXiv 2606.13685, 50 trials per question; also **72% first-position bias** in
GPT-4o-mini) `[cited-2026-04]`. Forced greedy decoding leaves **1–2 of 7 borderline items
non-reproducible** across 690 calls spanning two providers and three model tiers
(arXiv 2606.26185) `[cited-2026-06]`. And under bf16 greedy decoding, accuracy varies up
to **9%** purely from GPU count, type and batch size (arXiv 2506.09501, pre-2026 by weeks).
**The incumbent at ~95% is roughly 2.5x more self-consistent than published text judges.**

**Rank, don't score — the reject-only audit contract was right.** VLM judges show a
documented **ranking/scoring decoupling**: they order responses correctly while producing
uninformative absolute scores, with conformal intervals covering **40% of the score range
for natural images, expanding to ~70%** for chart/math (arXiv 2604.25235v2)
`[cited-2026-04]`. For set-level audit: keep/reject or an ordering, **never an absolute
score**.

---

## 7. Why no ≤10B model should take the audit job

- **VIABLE** (arXiv 2605.31351v2, >300K judgment samples, 7 judges across scales)
  `[cited-2026-05]`: *"Existing models are largely unreliable across all evaluation axes.
  The strongest judge, GPT-5.4, achieves only 52.6% single-failure diagnostic accuracy,
  yet exhibits the highest self-preference rate at 94.2%; while open-source judges are
  strongly biased and adversarially fragile."*
- **GeoRC** (arXiv 2601.21278v2) `[cited-2026-01]`: on explaining which image evidence
  supports a claim about a photograph — the closest published proxy to card-writing —
  *"small open-weight VLMs such as Llama and Qwen catastrophically fail ... only slightly
  better than a baseline in which an LLM hallucinates a reasoning chain with oracle
  knowledge of the photo location but no visual information at all."*
- **"Image overload" is a named failure mode of 7–8B judges** (arXiv 2606.20364)
  `[cited-2026-06]`, which maps directly onto a 12-tile contact sheet. That work also
  measures a **0.94 order-flip rate** on independent base samples — near-total order
  instability when candidates are genuinely equivalent.
- Rubrics do not transfer down: a per-VLM discovered taxonomy beats a global schema **for
  every one of 16 VLMs tested**, mean relative improvement **~32%** (arXiv 2606.22918)
  `[cited-2026-06]`. **A rubric tuned on the 27B will not transfer to a 7B judge.**
- The one counterexample is instructive rather than encouraging: **MJ1** gets a **3B judge
  to 77.0% on MMRB2, beating Gemini-3-Pro** — but only via RL with grounded verification
  chains *plus an explicit counterfactual consistency reward penalising position bias*
  (arXiv 2603.07990v2) `[cited-2026-03]`. Existence proof, not a drop-in.

---

## 8. Speed economics: the lever is the vision tokenizer, not the language model

The workload is prefill-dominated — roughly **93%** of wall time on a large image
`[measured]`. Decode throughput, the number usually quoted, is nearly irrelevant here.

Same machine, same image, same prompt, 2026-08-30 `[measured]`:

| | prefill | decode | total | peak RAM |
|---|---|---|---|---|
| `Qwen3.5-9B-MLX-4bit` | 54.03 s | 90.5 tok/s | **63.34 s** | 10 GB |
| `Qwen3.8-27B-4bit` | 70.68 s | 28.8 tok/s | **82.96 s** | 21 GB |

**End-to-end gain: 1.31x, not 3x.** The 9B generates 3x faster but must still chew the
same 16,639 vision tokens.

Prompt throughput and token count, same run `[measured]`:

| Model | prompt tok/s | tokens for the test image | TTFT |
|---|---|---|---|
| granite-4.0-3b-vision-4bit | **3,383.1** | 1,510 | 0.45 s |
| InternVL3-8B-bf16 | 2,630.7 | 2,605 | 0.99 s |
| gemma-4-26b-a4b-it-4bit | 1,439.9 | **579** | **0.40 s** |
| gemma-4-31b-it-4bit | 520.3 | 579 | 1.11 s |
| Qwen3.5-35B-A3B-4bit | 318.0 | 16,639 | 54 s |
| **Qwen3.5-9B-4bit** | **307.9** | **16,639** | 54.03 s |

Qwen pays twice — slowest prefill rate *and* 29x the tokens. **Shrinking the language
model moves you 1.3x; changing the token budget moves two orders of magnitude.**

**The cheapest experiment is sheet resolution.** Qwen's dynamic ViT costs roughly
(H/28)x(W/28) tokens, so halving each linear dimension cuts prefill ~4x on the model
already in production. A 1600x1200 sheet ≈ 2,450 tokens ≈ 8 s prefill at 308 tok/s; an
800x600 sheet ≈ 610 tokens ≈ 2 s `[EST]`. Find where card quality degrades before
changing models.

**Why Gemma 4 is not the answer despite being fastest and Apache-2.0.** Its processor
config sets `"image_seq_length": 280, "max_soft_tokens": 280` with **no `pan_and_scan_*`
keys at all** — Gemma 3 had them, Gemma 4 removed them `[measured, config verified]`. A
12-tile audit sheet gets **~23 tokens per tile** `[EST]`. Its video path is more extreme
still: 70 soft tokens across 32 frames. Compounding this, its long-context axis is its
weakest — 26B-A4B scores MRCR-128k **44.1** against the dense 31B's 66.4
`[cited-2026-03]` — and a packed tile sheet *is* a long context.

**MoE does not help this workload.** `Qwen3.5-35B-A3B` prefills at 318 vs the dense 9B's
308 — **+3% for 3.4x the RAM** `[measured]`. The reason is structural: the vision encoder
is **dense in every MoE VLM checked** — all 96 expert tensors in Qwen3-VL-30B-A3B sit
under `language_model.`, **zero** under `visual.` `[measured, weight index]`. MoE is a
decode optimisation, and there is no decode problem here. Expert offloading did ship
(mlx-vlm #1813, merged 2026-08-28) but OOMs between 4K and 8K context — precisely where a
16K image prompt lives.

**4-bit is safe, and the usual worry is backwards.** Three independent measured sources
agree that weight-only 4-bit hurts OCR/document tasks *less* than text: W4A16 recovers
**99.85% on DocVQA** and 98.47% vision average while text MGSM CoT falls to **92.04%**
(Red Hat GPTQ ladder, pre-2026 cards); even at 3-bit, DocVQA (-0.8) and OCRBench (-1.3)
are the *least* damaged benchmarks (arXiv 2607.21076) `[cited-2026-07]`. The reason is
structural and verifiable: **the vision tower is never quantized** — zero `.scales`
tensors under `visual.`/`vision_tower.` in the shipped 4-bit checkpoints; the flat
`{"bits": 4}` in config.json is misleading `[measured, weight index]`. Rule: 4-bit
weight-only yes, **never below 4-bit**. Note DWQ for VLMs does not exist
(Blaizzy/mlx-vlm#346, open since 2025-05-06), and Gemma's QAT quality claim has no
published numbers behind it. Ignore vendor cards reporting >100% recovery — e.g.
`RedHatAI/Qwen3-VL-32B-Instruct-NVFP4` at **141% ChartQA** is a broken bf16 baseline.

---

## 9. Revised probe plan

Ordered by value, not by convenience. Steps 1 and 2 apply to the incumbent and may
remove the need for step 3.

**Step 1 — fix the schema, then re-measure the incumbent.** Apply the costless-escape
change from §2 to every guessable card field (`people`, `action`, relations). Re-run the
27B on N≈50 banked moments and measure the invented-people rate against the existing
banked cards. This is the change most likely to fix the stated problem, and it is free.
While here, add the parse-failure counter from §1.2 — the repair-ladder fire rate is
currently unknown and is the baseline everything else is judged against.

**Step 2 — settle the two fail-silent paths (§1).** The base64 A/B (data-URI vs file
path, same image, temperature 0) and the grammar liveness assertion (a schema the model's
natural output violates). Both are minutes of work and both gate the meaning of every
subsequent measurement.

**Step 3 — then, and only then, probe two models.** Pull `mlx-community/Qwen3.5-9B-MLX-4bit`
(10 GB) and `mlx-community/MiniCPM-o-4_5-4bit` (6.16 GB).

- **Probe A — flip control, against a run-to-run baseline.** The earlier "accept if the
  flip changes <5% of verdicts" criterion was **wrong**: the incumbent's own noise floor
  is ~5%, so a 5% threshold is unmeasurable. Correct method: for each model, first
  measure run-to-run disagreement on **identical** inputs (same prompt, temperature 0,
  repeated), then measure disagreement when one token of the prompt's example answer is
  flipped. **Accept only if the flip-induced delta is statistically indistinguishable
  from that model's own baseline.** Budget **~11 repeated trials** for a majority verdict
  to recover a 50-trial reference with 95% probability, and ~15 for high-variance items
  (arXiv 2606.13685) `[cited-2026-04]`. n≥100 pairs.
- **Probe B — cards vs banked 27B cards.** N≈50 moments, temperature 0, run with and
  without `response_format`. Three metrics: parse rate (and repair-ladder fire rate),
  invented-people rate against the banked cards, and **n-gram overlap with any supplied
  prior** — that last one is the anchoring detector, and is the same quantity the sweep
  reports as "draft hints copied unchanged".
- **For MiniCPM-o only:** raise `max_slice_nums` off its default of **9** before judging
  it — it will under-slice a packed contact sheet `[cited-2026-02]`.
- **Put constraints in schema key names, not prose** (§2, arXiv 2604.14862).
- **Harness:** `github.com/jrp2014/check_models` already runs mlx-vlm with explicit
  **metadata-blind vs metadata-assisted lanes** — blind-vs-assisted on one image *is* a
  ready-made anchoring control. Point it at real tile sheets rather than building a
  harness.

---

## 10. Unpublished territory

Five things nobody has published. These define what an in-house probe actually
contributes, and each is a reason not to expect a literature answer:

1. **Example-anchoring in VLMs with per-model-size numbers.** The 107/107 flip result
   appears to be genuinely unpublished. The nearest published anchors are text-only
   (arXiv 2608.23651, ≤1.7B) or measure score-sycophancy rather than answer-copying
   (SycoPhantasy, arXiv 2604.24346: **450M 22.3% -> 7B 6.0%**, r = -0.96, p = 0.002 —
   a steep monotone decline that **does not reach zero**).
2. **Any small-vs-large VLM comparison on the same grid-packed input.** "Does packing
   degrade small models more?" is unanswered.
3. **Any guidance on tile counts or per-tile resolution for contact-sheet prompting.**
4. **A dense-caption benchmark isolating invented *people*.** Relations are well covered
   (Tri-HE, Reefknot, MMRel, FINER); persons/identities are touched only by MIRAGE
   (artworks) and MIHBench's object-identity-consistency category.
5. **VLM self-agreement at greedy decoding on image judgments.** Every temperature-0
   self-agreement number available is a text judge or an agent pipeline.

One caution that cuts against the scaling story in both directions: while
SycoPhantasy finds sycophancy falling steeply with size, *"To See or To Please"*
(arXiv 2603.18373, 9 VLMs, 9,000 pairs) finds **72.9% of samples exhibit visual
sycophancy** and reports that within the Qwen-VL family scale **reduces language
shortcuts but amplifies visual sycophancy** `[cited-2026-03]`. Bigger is not uniformly
safer here.

---

## 11. Method warning: do not pick a card model by VQA or MMMU

*"An MLLM's performance on VQA benchmarks may not correlate with its ability to generate
detailed image captions"* (arXiv 2412.15484v4, CapMAS) `[cited-pre-2026]`. The same work
identifies the root cause of long-caption drift: *"the increasing reliance of MLLMs on
their generated text, rather than the input image, as the sequence length grows"* — which
is the card task's exact shape. Related and structural: debiasing premature-EOS yields
longer, more detailed captions *"albeit with an expected increase in the rate of
hallucinations"* (arXiv 2507.20077v2). **Detail and factuality trade off; a leaderboard
score measures neither.**

Also note MuirBench's standing floor for this input class: open multimodal LLMs trained on
single images *"can hardly generalize to multi-image questions, hovering below 33.3%"*
(arXiv 2406.09411) `[cited-pre-2026]` — and that MMIU/MuirBench have largely stopped being
reported in 2026, so the composite-grid axis must be measured in-house.

---

## 12. Sources

All URLs accessed 2026-08-30.

**Field measurements (Apple M5 Max, temperature 0, mlx-vlm).** Sweeps
[#2009](https://github.com/Blaizzy/mlx-vlm/issues/2009) (2026-08-22),
[#2088](https://github.com/Blaizzy/mlx-vlm/issues/2088) (2026-08-28),
[#2098](https://github.com/Blaizzy/mlx-vlm/issues/2098) (2026-08-30); harness
[jrp2014/check_models](https://github.com/jrp2014/check_models) and its
[model gallery](https://github.com/jrp2014/check_models/blob/main/src/output/reports/model_gallery.md).

**Defects.** mlx-vlm [#1925](https://github.com/Blaizzy/mlx-vlm/issues/1925) (base64,
open), [#2092](https://github.com/Blaizzy/mlx-vlm/issues/2092) (lfm2_vl fix, merged
2026-08-29), [#2041](https://github.com/Blaizzy/mlx-vlm/issues/2041) (Flash-Next garbage,
open), [#1813](https://github.com/Blaizzy/mlx-vlm/pull/1813) (MoE offload, merged
2026-08-28), [#346](https://github.com/Blaizzy/mlx-vlm/issues/346) (VLM DWQ, open);
[ml-explore/mlx-lm#845](https://github.com/ml-explore/mlx-lm/issues/845) (outlines
dependency rejected 2026-02-06); vLLM issue #53975 (`guided_json` silently ignored).

**Model cards / configs.** `Qwen/Qwen3.5-{4B,9B,27B}`, `Qwen/Qwen3.8-27B`,
`Qwen/Qwen3.8-Flash-Next`, `google/gemma-4-{E4B,12B}-it` (`processor_config.json`),
`openbmb/MiniCPM-o-4_5`, `mistralai/Ministral-3-3B-Instruct-2512`,
`ibm-granite/granite-4.0-3b-vision`, `LiquidAI/LFM2.5-VL-3B`; `ai.google.dev/gemma/docs/gemma_4_license`.

**Serving matrices.** mlx-vlm `mlx_vlm/models/` tree + release v0.6.17 (2026-08-26);
vLLM `vllm/model_executor/models/registry.py` (main, 2026-08).

**Literature.** Fabrication: 2607.20492v2. Thresholds: 2603.15118v2, 2605.20815,
2605.02363, 2604.25359, 2607.18261. Format tax: 2606.09410, 2604.03616, 2607.18476,
2604.14862v2. Anchoring: 2608.23651, 2605.13538, 2608.25869, 2608.14320, 2604.13403,
2605.02378. Multi-image: 2602.01984, 2604.22498, 2608.14016, 2406.09411 (pre-2026),
2408.02718 (pre-2026). Determinism: 2606.26185, 2606.13685, 2506.09501 (pre-2026).
Judges: 2605.31351v2, 2601.21278v2, 2603.07990v2, 2604.25235v2, 2606.20364, 2606.22918.
Sycophancy: 2604.24346, 2603.18373. Captioning: 2412.15484v4 (pre-2026), 2507.20077v2
(pre-2026), 2603.09160. Quantization: 2607.21076.
