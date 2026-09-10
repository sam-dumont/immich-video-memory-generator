---
date: 2026-09-02
status: research — answers the owner's 23:40 question on the #764 reading passes
issue: 764
---

> **The question (owner, 2026-09-02 23:40, verbatim):** "This is inadmissible. Way too slow
> and we're back to our issues. Launch a research on how to find the best balance between we
> analyze too much and we lose pictures."
>
> **Read `## Mapping hints` (last section) first** if you only have a minute. Every number in
> §2 was measured on the 2022-blind store tonight with read-only SQL; every number in §§3–6 is
> attributed to a named source with what it actually measured. §7 is the proposal, §8 the
> recall mechanism, §9 the falsifiers.
>
> Companion: `2026-08-25-editing-craft-research.md` (the craft the passes imitate),
> `2026-09-01-annotation-store-design.md` §2–3 (where the facts live),
> `docs/implementation-plans/2026-09-02-funnel-decision-brief.md` §3/§9/§12/§17 (the pipeline
> as it stands at 22:53).

# The reading budget

How much of a library an expensive judge has to read, at which unit, before an occasion stops getting lost — and where the time actually goes when it goes.

---

## 1. The question is not "how much do we analyse". It is "how much do we WRITE".

The owner's framing is reading. The measurement says otherwise.

From brief §9, the 8-minute rung, two stages measured end to end:

| stage | live calls | prompt chars | answer chars | wall clock |
|---|---|---|---|---|
| chapter selection | 25 | 671,000 | 122,000 | 1,062 s |
| asset cut | 19 | 873,000 | 87,000 | 1,205 s |
| **total** | **44** | **1,544,000 (≈386k tok)** | **209,000 (≈52k tok)** | **2,267 s** |

At the judge's observed decode rate of 20–25 tok/s, 52k answer tokens alone cost 2,080–2,600 s. The whole measured wall clock is 2,267 s. **Under any decode rate consistent with what the server does, generation is 76–100 % of the time and prefill is at most a quarter of it.** Reading 386k tokens is the cheap part of a stage that took 38 minutes.

The same arithmetic explains the number the owner called inadmissible. The 508-card pass went from 3.5 min to 36 min when cards grew ~4×. 3.5 min at 25 tok/s is 5,250 output tokens — about **10 tokens per card**. 36 min is ~54,000 — about **106 tokens per card**. Input grew 4×; output grew 10×. Two mechanisms are consistent with that and they have different fixes (§9, falsifier 3):

1. **The model writes in proportion to what it reads.** A card fed ten asset lines writes a
   ten-line answer unless the schema forbids it. Fix: cap the answer, not the input.
2. **508 separate calls re-prefill the shared instruction block 508 times.** The MLX serving
   stack does not carry a KV cache between requests — `mlx-vlm`'s server calls `mx.clear_cache()`
   after *every* generation (open issue Blaizzy/mlx-vlm#999; one logged session shows
   "Generation finished, cleared cache" 1,143 times). Fix: fewer, larger calls with a
   byte-identical leading prefix, or a server that keeps a prefix cache.

Both fixes point the same way and neither of them is "read less".

**So the budget to design against is a generation budget.** At 25 tok/s, ten minutes is **15,000 output tokens for an entire library-year**. Two minutes warm is 3,000. Everything below is built to fit inside that.

---

## 2. What the store says (all measured tonight, read-only, 2022-blind)

8,050 assets · 402 videos · 4,324 Live Photos · 685 favourites · 336 days · 8,014 descriptions · heads on all 8,050 · pixel facts on all 8,050 · 2,048 assets carrying named people.

### 2a. The unit problem, sized

Moments rebuilt from capture time with a 15-minute gap (the Cooper-style segmentation, §5): **1,455 moments, median 2 assets, mean 5.5, max 207.** 188 moments hold ≥10 assets and those 188 moments hold **5,098 assets — 63 % of the library.** Reading one representative per moment therefore leaves 63 % of the library behind a 1-in-10-or-worse lottery.

Where the owner-graded 73 sit, by the size of the moment they belong to:

| moment size | graded assets in it |
|---|---|
| 1 asset | 6 |
| 2–4 | 9 |
| 5–10 | 18 |
| 11–30 | 19 |
| 31+ | 21 |

**40 of 73 graded picks live in moments of 11 or more.**

### 2b. Reading budget vs reach — the curve

"Reach" = how many of the owner-graded 73 are even *present* in what the judge reads. A pick the judge never sees cannot be made. Shortlist rule = favourites first, then sharpest (pixel facts), then first and last frame, deduped.

| what gets read | asset reads | % of library | graded reachable | graded days |
|---|---|---|---|---|
| medoid, 1/moment (uniform proxy) | 1,455 | 18 % | **16/73 (22 %)** | 30/30 |
| favourite-first, 1/moment | 1,455 | 18 % | **29/73 (40 %)** | 30/30 |
| shortlist 3/moment | 2,792 | 35 % | 50/73 (68 %) | 30/30 |
| shortlist 5/moment | 3,605 | 45 % | 56/73 (77 %) | 30/30 |
| 3/moment, 8 on candidate days | 4,311 | 54 % | 61/73 (84 %) | 30/30 |
| 3/moment, all on candidate days | 7,913 | 98 % | 73/73 | 30/30 |
| everything | 8,050 | 100 % | 73/73 | 30/30 |

Three findings, in order of leverage:

1. **Changing the representative rule from medoid to favourite-first nearly doubles reach at
   identical cost.** 16 → 29 of 73, 1,455 reads either way. This is the cheapest fix available
   anywhere in the pipeline and it needs no model call.
2. **Day coverage is never the failure.** Every budget above touches all 336 days. An occasion is
   not lost because the day went unread; it is lost because the day's read was uninformative —
   a race day represented by its most average frame is a day the judge later drops for having
   nothing in it. The brief's own defect list says the same thing from the other end (§17.1: the
   20k day is 161 assets in the store, 11 one-asset moments on the wall).
3. **Reach is sublinear and saturates late.** Going from 18 % to 35 % of the library buys 21
   graded assets; the last 45 % buys 12. There is a knee, and it is at roughly one third.

### 2c. What a line costs, in tokens (4 chars/token)

| line shape | avg chars | whole library | per month | per day |
|---|---|---|---|---|
| compact triage (time · place · marks · 2 head codes · first 14 description words) | 98 | **198,000 tok** | 5k–36k | — |
| store full line (all heads, who-count, place, marks, full description) | 398 | **801,000 tok** | 21k–148k | median 790, p90 7,687, max 26,551 |
| runner card line (adds person facts, ages, summary) — coordinator's measurement | ~500 | **~1.0 M tok** | ~85k | — |

**The whole library, every asset, as a compact line, is 198k tokens.** At the assumed 1,500 tok/s prefill that is 132 seconds. Nobody has ever tried it. Every run to date has read a representative because reading everything *sounded* expensive.

The full line for everything is 801k–1.0M tokens — 9–11 minutes of pure prefill with zero generation. That one is genuinely unaffordable, and it is the one the 36-minute card pass was walking toward.

### 2d. Metadata candidacy, measured against the graded days

Home centroid = median lat/lon of the library. "Away" = >50 km from it.

| rule | days flagged | of 336 | graded days caught |
|---|---|---|---|
| favourite ≥ 1 | 46 | 14 % | 18/30 |
| away > 50 km | 48 | 14 % | 13/30 |
| favourite ∨ away | 67 | 20 % | 20/30 |
| favourite ∨ away ∨ ≥20 assets | 114 | 34 % | 25/30 |
| favourite ∨ away ∨ ≥20 assets ∨ ≥1 video | **163** | **49 %** | **27/30** |
| + ≥15 assets, ≥4 named people | 184 | 55 % | 28/30 |

**No metadata rule reaches 30/30.** The three days that survive every rule are: a 17-asset day at home whose graded frame is a pastry; a 4-asset day whose graded frames are a watch face; and a **single-asset day** whose one frame is a cat sitting on a pizza box. No favourite, no video, nobody tagged, no travel. Their content is the only thing that says they mattered.

That is the whole argument in one measurement. **Metadata is a superb rule for deciding how DEEP to read. It is a fatal rule for deciding WHETHER to read.** The literature agrees (§5) and so does the owner's own bar: none of those three is an *occasion* — they are texture days — so metadata candidacy at 163/337 loses no occasion, only three good pictures, which [[95-beats-100]] explicitly permits. It is safe as a depth gate and unsafe as a reading gate.

---

## 3. How large curators decide what to read

**Apple, "Recognizing People in Photos Through Private On-Device Machine Learning"** (Apple ML Research, read directly). Memories runs on cheap facts computed once and cached: face embeddings at **<4 ms on the Neural Engine, 8× faster than the equivalent GPU model**; incremental clustering at **2.4 s vs 69.2 s for average-linkage at iteration 35 (~29×)**. The output is a persistent on-device graph of people, places, events, trips; Apple never re-reads pixels to build a Memory, it queries the graph — the layer-1 bank already built here. **"Learning Iconic Scenes with Differential Privacy"** (same source) adds the production key-photo case: frequencies for **4.5 M location-category pairs across 1.5 M locations and 100 categories**, driving **Memories and Places key-photo selection since iOS 16/17** — a key-photo chosen from a metadata prior, not a per-photo model read.

**Google Photos has no technical primary source.** All that exists is UX journalism listing signals (repeated faces, GPS clusters, laughter, smile intensity) with no number. **Top Shot** is documented — up to **90 frames in the 1.5 s around the shutter**, ranked on lighting, eyes-open, smiling — but it is burst selection, not curation; do not cite it as the latter.

**AlbumBench: Beyond Single Images** (Huang, Price, Fan, Morse, Adobe Research, CVPR 2026 — verified via Adobe Research and the CVF listing; the PDF 403'd so its tables are **unverified**): **27,051 images across 641 albums**, 5 annotations per image, built on CUFED (30–100 images per life-event album). Its three tasks are this pipeline's — selection for an album objective, rating against user intent, contextual grouping. Only recovered finding: a "significant performance gap between open-source and proprietary VLMs" — a caution about the 500M student's descriptions, not the 30B judge. **Automatic Triage for a Photo Series** (Chang, Yu, Wang, SIGGRAPH/TOG 2016): **15,545 unedited photos in 5,953 series**, ground truth as pairwise human preference *within* a series — quality in a burst modelled as **relative, never absolute**, the same shape as [[the-model-cannot-rank]] (accuracies unverified, PDF unreadable).

**SumMe** (Gygli et al., ECCV 2014): **25 videos, 15–18 human reference summaries each**, and a hard rule — **the summary must be ≤15 % of source length**. **TVSum**: 50 videos, 20 annotators. F1 at that budget today: **~65–67 % supervised, ~61–63 % unsupervised** (aggregated from 2024–25 papers, not re-derived from primary tables). A whole field, given a 15 % budget and human ground truth, reaches two-thirds agreement. **This pipeline's medoid reading reaches 22 % of the graded picks at an 18 % budget.** The budget is not the problem; the selection rule inside it is.

**Learning Mixtures of Submodular Functions for Image Collection Summarization** (Tschiatschek, Iyer, Wei, Bilmes, NIPS 2014): **14 collections, 100 images each, 50–250 human summaries per collection**; introduced V-ROUGE because no metric existed. (The "Sinha & Jain photo album summarization" citation could **not** be verified as a distinct paper — do not repeat it.) Mechanism to take: the objective is a **mixture** of a coverage term (facility location) and a diversity term, greedily maximised under the (1−1/e) guarantee — separate terms, never one quality score ([[editing-not-scoring]]).

**Budget-vs-loss curves.** **CRAIG** (Mirzasoleiman, Bilmes, Leskovec, ICML 2020): facility-location greedy over gradients, **up to 6× faster (logistic regression), ~3× (DNNs)** at a matching convergence rate. **GLISTER** (Killamsetty et al., AAAI 2021) gives the clean curve: **CIFAR-10 at a 10 % budget = 6× speedup for −3 % accuracy; 30 % = 2.5× for −1.2 %; 50 % = 1.5× for −0.2 %; MNIST at 10 % = 3× for −0.2 %.** The same knee as §2b — most value by a third of the data, an expensive tail after.

**Cascades.** **Viola-Jones** (2001) is still the cost model: **22 stages**, most windows rejected in the cheapest, **15× faster at equal accuracy**. **FrugalGPT** (Chen, Zaharia, Zou, arXiv:2305.05176, read directly): **98.3 % cost reduction on HEADLINES, 73.3 % on OVERRULING, 59.2 % on COQA** at matched accuracy, up to +4 % accuracy at equal cost. **RouteLLM** (ICLR 2025, arXiv:2406.18665): **>85 % cost reduction on MT-Bench retaining 95 % of GPT-4 quality, sending ~14 % of queries to the strong model** (45 % on MMLU, 35 % on GSM8K); independent commentary puts real deployments nearer **35–70 % at <2 % quality loss**, so cite both. **Big Little Decoder** (NeurIPS 2023): **1.50× average speedup at zero quality loss, 1.76× at −1 point**.

**Hierarchical summarisation and what it costs.** **Lost in the Middle** (Liu et al., TACL 2024, read directly): GPT-3.5-Turbo, 20-document QA, accuracy by position of the answer-bearing document — **75.8 % at position 1, 57.2 % at 5, 53.8 % at 10, 55.4 % at 15, 63.2 % at 20: a 22-point drop from best to worst**, replicated across six model families. **BooookScore** (Chang et al., ICLR 2024), 1,193 human annotations over 100 books: **hierarchical merging gives higher coherence but lower detail; incremental updating gives lower coherence but higher detail** — a measured trade-off, and exactly the trade made every time a month thesis merges into a year thesis. Detail loss at the merge is where "the marathon vanished with no check" (brief §16) comes from.

---

## 4. Reading a representative vs reading the group

**The medoid is defined as the most average member.** k-medoids/PAM picks the member minimising summed dissimilarity to all others — robust to outliers precisely because it refuses to pick one. `selection_structure._visual_medoid` is that definition applied to a perceptual hash. On a race day the most average frame is a spectator's back.

**Diversity-aware selection is the standard alternative.** Determinantal point processes (Gong, Chao, Grauman, Sha, NIPS 2014, "Diverse Sequential Subset Selection for Supervised Video Summarization") select by the *determinant* of a similarity kernel, so a subset holding near-duplicates has near-zero probability; seqDPP reports **F-score 60.3 % (±0.5) on YouTube** (secondary-sourced; the baseline table could not be extracted — **unverified**). Facility location is the coverage-only special case, k-center the maximally-spread one. **No head-to-head recall-at-budget bake-off of medoid vs k-center vs facility location on a photo-selection task was found** — a genuine gap, which is why §2b measures it here instead.

**The recall cost of representative-only reading is asserted everywhere and measured nowhere.** Near-duplicate reviews state plainly that "choosing the photograph nearest to the group centroid as seed image is not accurate enough" and propose contextual-relevance ranking — qualitatively, no number. The IR cluster hypothesis is used to *justify* cluster-based retrieval, never to quantify what single-representative reading loses. **§2b is, as far as this research found, the only measurement of it on a personal library: 16/73 for the medoid, 29/73 for favourite-first, at identical cost.**

**The cheap-then-expensive architecture has hard numbers, from IR.** BM25 → cross-encoder on MS MARCO dev: **BM25 Recall@1000 ≈ 0.77–0.87** (implementation-dependent; reconcile before quoting one figure), **MRR@10 0.187**; a monoT5 reranker over that same top-1000 pool reaches **MRR@10 0.398**. The first stage is not asked to be right — it is asked to be *complete*, and it is graded on recall alone. **BEIR** (Thakur et al., arXiv:2104.08663) adds the caveat that first-stage recall is workload-dependent (TREC-COVID R@100: BM25 0.498 beats DPR 0.457; NQ: DPR 0.880 beats BM25 0.760), so a candidate budget must be validated on the actual library, never inherited.

**Does reading text instead of pixels lose recall?** **Socratic Models** (Zeng et al., 2022, arXiv:2204.00598) composes a vision encoder and a language model through a *text* interface and reaches **42.8 R@1 on MSR-VTT 1k-A zero-shot video-to-text retrieval**, competitive with the SOTA of its day. It runs no caption-vs-direct-vision ablation, so it shows the pattern *works*, not that it is free. The consistent 2025–26 VLM finding is a **semantic bottleneck** — only what the captioner verbalises survives, and fine-grained spatial and relational detail is lost (qualitative across several papers, no single percentage). That is the honest caveat on the whole text-only bet, and it is why [[annotations-always-on-the-line]] is a hard rule: the line *is* the bottleneck, so widening the line is the only lever there is.

---

## 5. Metadata as a free read

**Cooper, Foote, Girgensohn, Wilcox — "Temporal Event Clustering for Digital Photo Collections"** (ACM TOMM 1(3):269–288, 2005), the canonical time-gap segmentation. Confirmed finding: the unsupervised similarity-based approach **"approximates the performance of hand-tuned semi-automatic techniques."** Its precision/recall table could not be extracted (ACM 403) — **unverified**, and it is the load-bearing citation of this section; worth a manual pull. **Platt & Czerwinski, "PhotoTOC"** (MSR-TR-2002-17, 2002) clusters on time plus colour histogram and **automatically picks one representative per cluster** — the exact medoid pattern this pipeline inherited, from 2002. Its confirmed result is a user-study preference ranking, not an accuracy. **Graham et al.** (JCDL 2002) and **Loui & Savakis** (IEEE TMM 2003) share the premise — time-gap bursts are semantic events — and neither yields a retrievable figure. **Flagged: the time-gap family is universally adopted and thinly measured.**

**Importance is not saliency.** Berg, Berg, Daumé et al., "Understanding and Predicting Importance in Images" (CVPR 2012): people can be salient-but-unimportant and important-but-unsalient, and the paper uses *what people choose to describe* as importance ground truth. Directly relevant: sharpness and exposure are saliency-flavoured and must stay warnings on the line (brief §13), never merit. **Wang, Lin, Shen, Mech, Miller, Cottrell** (BMVC 2017, arXiv:1707.05911) trains a Siamese importance predictor jointly with event-type recognition, each helping the other (numbers **unverified**); the transferable claim is that importance is **event-conditional** — the same frame is important at a wedding and filler on a Tuesday. That is the structural argument for per-heading picks over a library-wide quality bar.

**The burst-length claim is folklore.** "People photograph in bursts, and a long burst means something important happened" appears in the motivation sections of Cooper/Foote, Graham, Loui and most of the album-summarisation literature. **No paper found measures it as a predictor.** Same for "number of photos predicts event importance". §2d is the local measurement: burst mass alone (≥20 assets) catches 25/30 graded days while flagging 34 % of the calendar, and still misses a 4-asset day and a 1-asset day the owner graded.

**Favourites, measured here:** 41 of the 73 graded assets are favourites (56 %) against a 685/8,050 (8.5 %) base rate — a **6.6× lift**. 34 of 73 are Live Photos, 7 are videos. The published Flickr-favourites-predict-importance study was **not located**; the local lift is the evidence.

---

## 6. Where the tokens actually go

**Prefix caching is real, and this stack does not have it.** vLLM's automatic prefix caching is block-based and content-addressed (16-token blocks, hash chained from the parent), so **one differing token in the first block invalidates everything after it** — the shared content must come *first* and be *byte-identical*. Production reports (blog-sourced, methodology unstated) claim TTFT 480 ms → 110 ms at a 94 % hit rate. **SGLang's RadixAttention** (Zheng et al., arXiv:2312.07104, NeurIPS 2024) reports **up to 6.4× higher throughput** from sharing KV cache across matching-prefix requests.

**But `mlx-vlm`'s server calls `mx.clear_cache()` + `gc.collect()` after every generation** (three call sites in `server.py`; open issue Blaizzy/mlx-vlm#999, no owner). One reported session logged the clear **1,143 times**; a 58k-token context re-prefilled every turn cost "several minutes" per turn. The one worked fix in the MLX ecosystem is `vllm-mlx`, whose trie-based LRU prefix cache took **TTFT from 22 s to 2–3 s** on an M3 Ultra by reusing 9,010 of 9,011 prefix tokens. `mlx_lm` does ship `make_prompt_cache` / `cache_prompt` and a disk-persisted LRU server cache (PR #1405) — the capability exists, it is just not what is serving the judge today. This is the mechanical reason brief §9 measured "only 57k of 527k prompt tokens hit the cache": not a prompt-ordering bug alone, the server throws the cache away.

**Prefill speed: 1,500 tok/s is an assumption, not a measurement.** No verified Apple-Silicon prefill throughput for Qwen3-30B-A3B was found anywhere. Decode numbers exist and cluster at **64–88 tok/s for the 4-bit model** (M4 Max 87.6, M3 Ultra 76.3 at 8k context — secondary-sourced), well above the 20–25 tok/s this pipeline observes, so the local serving penalty is real and already priced in. The nearest real local-MoE prefill datum is **≈545 tok/s derived** from a 397B-A17B 6-bit model on an M3 Ultra (12,000 tokens in ~22 s); scaling by active parameters would put A3B in the low thousands, but that is an extrapolation from one point. **Treat 1,000–1,500 tok/s as a band and report both ends of every budget.**

**Context ceilings kill the one-big-prompt idea outright.** Qwen3-30B-A3B is **32,768 tokens native, 131,072 with YaRN** (the 262,144 figure belongs to Qwen3-**Coder**-30B-A3B — do not conflate). The 801k-token full-line corpus fits in none of those, and even the 198k compact corpus exceeds the YaRN ceiling. Chunking is mandatory. **And chunking is independently correct**: Lost in the Middle's 22-point positional drop at 20 documents, RULER's finding that Llama-3.1-70B's *effective* length is 64k against a longer claimed window, and NoLiMa (Modarressi et al., ICML 2025, arXiv:2502.05167) — **GPT-4o falls from 99.3 % to 69.7 %** as context grows on needles without lexical overlap — all say a flat 130k-token list of one-line descriptions is close to the worst-case shape for a long-context model. **Scope by month-part; never by stripping** ([[annotations-always-on-the-line]]).

**Encoding.** JSON costs **1.4–2.8× the tokens of the same data as CSV/TSV** across measured datasets (Twitter 3,673 vs 1,303; GitHub 968 vs 688; financial 643 vs 408) — all blog-sourced, none peer-reviewed, so quote the range, not "2×". Field names repeat per object; delimiters tokenise as separate subwords. The pipeline's lines are already delimiter-separated, which is right; the saving left on the table is in the *answer* schema, not the input.

**So: is "read everything once, cheaply, as text" real?** Yes, at the compact line. **198,000 tokens for all 8,050 assets — 2.2 min at 1,500 tok/s, 3.3 min at 1,000** — split over 12–24 month-parts of 5k–20k tokens each, every one comfortably inside the 32k native window and far from the lost-middle zone. **It has never been tried.** What is not real is the full annotation line for everything: 801k–1.0M tokens, 9–11 minutes of prefill before a single token is generated.

---

## 7. The proposal: a reading budget

Four principles, each traceable to a number above.

- **P1. Nothing is invisible; depth is what varies.** Every asset gets a line in some read
  (§2d: three graded days are invisible to every metadata rule). Metadata decides how deep, never
  whether.
- **P2. The representative is chosen by facts, not by averageness.** Favourite first, then pixel
  facts, then diversity on (subject, framing) from the facet bank (brief §11) — never the medoid
  (§2b: 29/73 vs 16/73 at identical cost).
- **P3. Cap the answer, not the input.** The budget is 15,000 output tokens per library-year
  (§1). Reading is the cheap half.
- **P4. Bank at the unit the judgment is ABOUT** ([[annotation-layer]], brief §9). A per-run cut
  that re-reads 779 reservoir rows because its prompt embeds the chapter capacity is a banked
  judgment written at the wrong unit.

### The passes

| # | pass | unit | model? | reads | writes | when |
|---|---|---|---|---|---|---|
| 0 | funnel, segmentation, candidacy, shortlists | asset / moment / day | **no** | SQL over layer 1 | — | every run, ~30 s |
| 1 | month read | month-part (24 parts) | yes | compact line for **every** asset + full line for the shortlist | headings + nominated indices | once per library |
| 2 | promotion | month-part | yes | full lines for ≤20 indices the month read asked for | ordered per-heading shortlist | once, adaptive |
| 3 | year thesis | year | yes | the 12 month verdicts only | binds cross-month occasions, confirms carries | once |
| 4 | allocation | run | **no** | banked ladders + capacity | the cut | **every run** |
| 5 | audit + review | run | yes (1 call) | assembled cut | drop/expand/swap with reasons | every run |

**Pass 0 is all rules, no model** ([[ai-last-resort]]). From layer-1 facts only: moments by the 15-min gap; twins collapsed to the burst peak; screens and documents culled by docling plus description text; day candidacy by favourite ∨ away>50 km ∨ ≥20 assets ∨ ≥1 video (163/337 days, 27/30 graded); per-moment ordered shortlist by favourite → sharpness → (subject, framing) diversity → first/last frame; occasion depth by sustained days (brief §13: `want = max(1, round(days × target/600))`).

**Pass 1 is the read that has never been tried.** One call per month-part, each carrying a byte-identical instruction prefix *first* (so a prefix-caching server can help, §6), then the part's lines. Every asset in the month appears as a compact line — **the judge sees the whole library, once**. The shortlist assets additionally carry their full annotation line. The month read's job is nomination, never veto ([[coverage-first-triage]]): it names the month's headings and, per heading, the indices it wants. It may nominate a compact-line index it cannot fully see — that is pass 2's input.

This satisfies [[annotations-always-on-the-line]] as written: **no judgment is ever taken on a leaner line.** The compact line is an index, not a verdict surface. Anything it nominates is promoted to its full line before any pick is made.

**Pass 2 is the adaptive step** — Viola-Jones' cheap-stage-rejects-most, FrugalGPT's cascade, BM25's recall-first pool. Cost is bounded by the promotion cap.

**Pass 4 has no model at all.** A new duration is allocation arithmetic over banked ordered shortlists: depth per heading from capacity × sustained days, floors from favourite mass and away days, compressed never refused (brief §10). This is what makes a new duration cost seconds, and it is the direct answer to "after a first full year it should just be choosing between moments and episodes."

### The budget, both ends of the prefill band

Tier-2 depth set to **2/moment** (2,240 lines, 216k tokens) — the 3/moment tier costs 269k and fits only at the optimistic end.

| pass | prefill tok | output tok | @1,500 tok/s | @1,000 tok/s |
|---|---|---|---|---|
| 0 rules | 0 | 0 | 0.5 min | 0.5 min |
| 1 month read (24 parts) | 198k compact + 216k full + 19k instr = **433k** | 4,000 | 4.8 + 2.7 = 7.5 min | 7.2 + 2.7 = 9.9 min |
| 2 promotion (12 calls) | 50k | 1,500 | 0.6 + 1.0 = 1.6 min | 0.8 + 1.0 = 1.8 min |
| 3 year thesis | 14k | 800 | 0.7 min | 0.8 min |
| 5 audit + review | 15k | 600 | 0.6 min | 0.6 min |
| **cold total** | **512k** | **6,900** | **≈10.9 min** | **≈13.6 min** |
| **warm (new duration): passes 4 + 5** | **15k** | **600** | **≈0.6 min** | **≈0.6 min** |

Against the targets: **warm clears 2 min with room to spare. Cold clears 10 min only at the optimistic prefill end.** The three knobs that close the gap, in order of preference:

1. **Fix the serving prefix cache** (§6). 24 parts × ~800 instruction tokens is small, but a
   server that keeps the cache also makes pass 2 near-free and would take the 508-call card
   pattern off the table permanently.
2. **Drop tier-2 depth to favourite-first 1/moment** (140k instead of 216k): −1.3 min, reach
   29/73 instead of 39/73 — still nearly double today's medoid.
3. **Shorten the description clause on the full line** (the 287-char student prose is the bulk
   of the 398-char line). This trades against [[annotations-always-on-the-line]] and must be
   measured, not assumed (§9, falsifier 4).

Everything ports into the runner's `_text_phase`, replacing `_hierarchical_thesis` and `_hierarchical_selection` and the pre-trimmed pick, through `text-only-cut.py` ([[reading-plugs-into-runner]]). The source funnel, cull, floors, day ceiling, favourite law, motion renderings, video selection, review ledger and editorial contract are **not** touched.

---

## 8. Why an occasion cannot be lost under this

Five independent guards, each one measurable:

1. **Every asset has a line in pass 1.** Measured: the three graded days invisible to every
   metadata rule (§2d) are reachable only this way. A model cannot skip what it has read; it can
   only decline it, and declining is recorded with a reason.
2. **Headings come from rules, not from a call.** Every month-named occasion is a heading (brief
   §12 v6: this alone took month presence from 9/12 to 12/12). No model call can delete a
   heading; the model chooses *within* one.
3. **Parts are ≤20k tokens.** Lost in the Middle's 22-point positional collapse and RULER's
   effective-length finding both bite at 20-document / 64k scales. A month-part is an order of
   magnitude below that, so no occasion is lost to position in a list — which is exactly how
   Christmas was lost in v4 ("dropped by list order").
4. **Depth is arithmetic, not judgment.** Sustained-day depth (brief §13) means a 14-day trip
   cannot arrive as one frame because a call forgot it — the fit reserves its seats before round 1.
5. **The audit runs before the review.** Mechanical, no model: every year heading has a frame,
   every carried heading has its depth, no month is empty, no kept moment has zero survivors
   ([[the-acceptance-bar]]'s two gate assertions). The marathon failed this check twice
   (brief §16) and nothing caught it because the check did not exist.

The residual risk is honest and named: **a heading the month read never names cannot be recovered.** Guard 1 makes that a reading failure rather than a budget failure — it means the compact line was too thin for that occasion, which is falsifier 4.

---

## 9. Falsifiers — the exact measurement that kills each claim

All on `~/.immich-memories-matrix/annotations/2022-blind/annotations.sqlite`, graded against `~/.immich-memories-matrix/smart-edit-validation-30b-l-2026-09-01/2022-blind/result.json.graded` (73 assets, 30 days). No model calls were made for this document.

1. **"The medoid is the leak."** Rebuild run c's cards with the favourite-first representative
   instead of `selection_structure._visual_medoid`, everything else byte-identical, fresh
   `--out` and `--text-cache`. *Prediction:* graded-asset reach 29/73 vs 16/73 and graded days
   ≥ 22/30. *Kill:* reach does not roughly double → the medoid is not the mechanism and §2b's
   uniform proxy is wrong.
2. **"Generation is the budget."** Log `prompt_tokens` / `completion_tokens` per call for one
   full run. *Prediction:* completion_tokens ÷ 25 tok/s accounts for ≥75 % of wall clock.
   *Kill:* prefill exceeds 40 % → the fix is the prefix cache (§6), and output caps buy nothing.
3. **"The 4× card cost 10× because the model wrote more."** Same log, on the 508-card pass at
   both card widths. *Prediction:* completion tokens per card rose ~10× (≈10 → ≈106). *Kill:*
   completion is flat and prompt tokens explain the wall clock → the cause is the 508-fold
   re-prefill (mlx-vlm#999) and the fix is a caching server, not a smaller card.
4. **"The compact line is enough to nominate."** Run pass 1 over all 8,050 compact lines and
   count how many of the 73 graded assets it nominates. *Prediction:* ≥55/73 (it can see all of
   them). *Kill:* <40/73 → the compact line is too thin; widen the line before touching the
   budget, and knob 3 in §7 is dead.
5. **"Metadata candidacy is safe as a depth gate."** Already measured: 163/337 days, 27/30
   graded. *Kill:* the owner calls any of the three missed days (a pastry, a watch face, a cat on
   a box) an *occasion* rather than texture — then candidacy may not gate depth either, and pass
   0's shortlist must be uniform across all days.
6. **"A new duration costs seconds."** After the bank, run 60/120/180/300/600 s in sequence.
   *Prediction:* each under 2 min, zero new model reads except pass 5, and graded-day recall at
   300 s within 1 day of the cold run's. *Kill:* any rung re-enters pass 1 or 2 → a judgment is
   still banked at the wrong unit ([[unit-granularity-trap]]).
7. **"Reading everything as text is affordable."** Time one month-part of compact lines against
   the wall clock and derive the real prefill rate. *Prediction:* 1,000–1,500 tok/s. *Kill:*
   below 700 tok/s → the whole §7 budget slips past 15 min cold and the compact corpus must
   shrink (fewer description words, not fewer assets).

---

## Mapping hints

Not a design — which finding maps onto which pipeline concept.

1. **The budget is output tokens, not input tokens.** 10 min ≈ 15,000 generated tokens for a
   library-year (§1). Every prompt-schema decision is a spending decision; every read is nearly
   free by comparison.
2. **The medoid is the single largest measured leak.** 16/73 vs 29/73 for favourite-first at
   identical cost (§2b). Replace `_visual_medoid` before anything else in this document is built.
3. **Day coverage is not the failure mode; day *evidence* is.** Every budget tested touches all
   336 days (§2b). Occasions are lost downstream, because the day's read said nothing.
4. **Metadata gates depth, never reading.** 27/30 graded days at 163/337 (§2d), and the 3 misses
   are texture, not occasions — safe under [[95-beats-100]], fatal as a read gate.
5. **The knee is at a third.** GLISTER's 10 %/30 %/50 % curve and §2b's reach curve agree: most
   of the value by ~a third of the items, an expensive tail after. Budget to the knee.
6. **Cheap-read-then-expensive-read is the standard architecture and it is measured.** BM25
   R@1000 0.77–0.87 → reranker MRR@10 0.187 → 0.398; FrugalGPT 59–98 % cost cut; RouteLLM 85 %
   at 14 % strong-model traffic. First stage is graded on *recall only*.
7. **Read everything once, as a compact line: 198k tokens, 2–3 min.** Never tried. The full
   annotation line for everything is 801k–1.0M tokens and is the thing that is actually
   unaffordable (§2c).
8. **Scope by month-part; never strip.** Lost in the Middle: 22-point positional drop at 20
   documents. NoLiMa: GPT-4o 99.3 % → 69.7 %. Qwen3-30B-A3B is 32k native / 131k YaRN — the
   corpus does not fit in one prompt at any line width.
9. **Hierarchical merging trades detail for coherence, measured.** BooookScore, 1,193 human
   annotations. The month→year merge is exactly that trade, and it is where the marathon died.
10. **The serving stack throws the KV cache away after every request** (mlx-vlm#999). Any design
    with hundreds of small same-prefix calls pays full prefill every time. Fewer, larger calls
    with a byte-identical leading prefix — or a different server.
11. **Importance is event-conditional, not a library-wide bar** (Berg CVPR 2012; Wang BMVC 2017).
    Per-heading picks, not a quality threshold — the same conclusion as
    [[editing-not-scoring]].
12. **Bank at the unit the judgment is about.** Apple queries an on-device graph and never
    re-reads pixels; the per-run asset cut re-reads 779 rows because capacity is in its prompt.
    Pass 4 must be arithmetic, or nothing else in this document buys anything.
13. **Where the literature is thin, say so.** No published number exists for the recall cost of
    medoid-only reading; burst-length-predicts-importance is folklore repeated in a dozen
    motivation sections and measured in none; Google's curation pipeline has no technical
    primary source. §2 measures locally because the literature does not.
