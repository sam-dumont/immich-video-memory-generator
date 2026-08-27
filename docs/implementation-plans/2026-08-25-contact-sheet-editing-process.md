# Contact-Sheet Editing Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current score-, quota-, and absorber-driven selector with a visual editing
process that sees the complete eligible corpus, banks every decision, minimizes model calls, and
produces one conserved chronological cut across every public surface.

**Architecture:** A long-lived feature trunk holds the replacement while `main` continues to ship
the current selector. Inside that trunk, one core flow owns source eligibility, a reusable visual
atlas, Insight, Cull/record shots, Selects, Structure, projection, Fine Cut, intended duration, and
the trace. Every decision-bearing pass receives the exact contact-sheet JPEG bytes recorded in its
provenance. Rendering consumes the final ordered membership and may not change it.

**Tech Stack:** Python 3.12/3.13, frozen Pydantic models, Pillow, FFmpeg/ffprobe, provider-neutral
multimodal LLM requests, SQLite-backed judgement cache, pytest through Makefile targets, NiceGUI,
Click, GitHub Actions.

**Spec:** `docs/designs/2026-08-25-contact-sheet-editing-process.md`

**Where each fact is cached:** `docs/designs/2026-08-27-the-annotation-layer.md`
amends Tasks 4–9. Each fact is cached at the unit it is a fact ABOUT — the picture's
own value per asset and forever, pairwise sameness per pair and forever, and only the
reading and the weighting per question. Selects becomes demand-driven on in-cut
moments rather than a pass over the corpus.

**Which instrument answers which question:**
`docs/implementation-plans/2026-08-27-visual-analysis-inventory.md` is the standing
inventory — nineteen call sites, seven questions, and what should answer each. Read it
before adding any pass that looks at pixels. Its line is the craft's own (Thein's
camera-back list vs his full-screen list, mapping hint 2): a pass may only ask what its
viewing conditions can answer, and it should use the cheapest instrument that can.

**Why the process looks like this:** `docs/research/2026-08-25-editing-craft-research.md`
is the sourced research on how photographers and film editors actually cull — this plan
carries only the quotes that survived into it. Its `## Mapping hints` section ties each
craft pass to a pipeline concept. `docs/research/2026-08-25-the-funnel-measured.md` is the
measured case against the selector being replaced.

## Global Constraints

- Run `make dev` before any other Make target in a fresh worktree.
- **The model is a last resort.** It is asked only where understanding what is
  depicted is required — Cull, and grouping by kind. Anything that can be done
  safely by arithmetic or signal processing must be, because a year is 12k–21k
  assets and a per-pair model question does not survive that. Build the pass
  with the model first, bank its answers, then calibrate the cheap path against
  them; a band that CUTS requires unanimity on real data plus cross-validation
  on a second period. See the design's "The model is a last resort".
- Use one RED → GREEN → REFACTOR cycle at a time. Tests exercise public behavior; no more than
  three mocked boundaries per test, and every mock carries a `# WHY:` comment.
- Run focused tests with `make test-one T="..."`, then `make test`, `make critique`, and `make ci`
  before every commit.
- Do not tune product rules to June. June, April, and August are private acceptance corpora; the
  synthetic suite must prove the same decision shapes under unrelated topics and label swaps.
- **Separate provenance from evidence.** "Only explicit source scope removes before Pass 0" means
  no *judgement* may pre-cull. It never meant the corpus is unfiltered. Whether a file came off
  this library's camera, and whether it is the motion half of a photograph, are facts about the
  file, not opinions about its quality — they belong to scope and they run before Pass 0. Both
  were lost by reading this constraint the other way; 52% of one acceptance month is material the
  owner never photographed.
- **Consult `2026-08-25-existing-selection-rules.md` before building any pool, group, or corpus.**
  It lists what the current selector already gets right, which of those rules are carried, and
  which are still AT RISK. A rule with four legacy callers and none of them yours is a rule you
  have lost. Adding a second door to a one-door rule removes the guarantee.
- Contact sheets are the model input, not merely a diagnostic. Hash, write, attach, cache, and trace
  the same encoded JPEG bytes.
- A video filmstrip is composed locally inside one tile. It costs no additional model request.
- A logical pass is not necessarily a model request. Reuse one visual atlas, pack complete groups,
  bank answers, and fuse only independent namespaces over identical evidence.
- Start at one sheet per request. Increase that limit only after a provider probe proves tile
  conservation and decision quality. Never infer image limits from a provider name.
- **Show the response shape; never describe it.** Every prompt embeds a complete example envelope
  built from the same wire keys its parser demands, and a test parses that very example. A prose
  schema and a strict parser drift silently, and fixtures written from the parser can never catch
  it: on the first real gate this voided three of four namespaces.
- **Everything in an example is instruction, values and whitespace included.** Show placeholders,
  not plausible literals — shown "ticket", the model labelled a pregnancy test a ticket; shown a
  populated cull entry, it copied that defect onto seven unrelated visuals. Decision arrays are
  shown EMPTY, which also states the honest default.
- **Ask the small local model for less.** Measured three times: each added paragraph made the
  answer worse — more rejects carrying one identical label, then a defect/evidence pair that was
  not even legal. Prefer deleting a sentence to adding one, and never restate in prose what the
  parser already enforces; a closed vocabulary cannot express "repetitive", so forbidding it only
  spends attention.
- **Bounded model prose is fitted, not fatal.** A reason that runs long is trimmed to its bound and
  its decision survives. The same images produced 91/84/76-character reasons on one run and
  103/105/104 on the next, so a hard bound makes every pass a coin flip that fails open and unseen.
- Reject-only actions fail open. Refusal, truncation, unknown IDs, incomplete partitions, or request
  failure cannot create an unnamed loss. Any mechanical preview carries `!!` and is invalid for an
  owner verdict.
- Preserve chronology in every contract. Sets may be used for validation, never as the stored order.
- Keep all private contact sheets, traces, and media outside Git and GitHub.
- Rewrite the existing Structure modules in place. PR #768 is evidence only; do not cherry-pick its
  implementation wholesale.
- Do not remove legacy machinery until the replacement owns the same public surface and all real
  gates pass.

## Branch and PR Strategy

```text
origin/main
  └─ feature/764-editorial-selection          # long-lived replacement trunk
       ├─ ci/764-feature-trunk
       ├─ feat/764-visual-foundation
       ├─ feat/764-insight-cull
       ├─ feat/764-selects
       ├─ refactor/764-structure-projection
       ├─ feat/764-fine-cut
       └─ refactor/764-editorial-cutover
```

- Bootstrap `feature/764-editorial-selection` from current `origin/main` with only the approved
  design, this plan, and CI support for feature-trunk PRs. Open an umbrella draft PR from the feature
  trunk to `main`; it stays unmergeable until final owner acceptance.
- Every production slice branches from the latest feature trunk and targets its PR back to
  `feature/764-editorial-selection`. Use squash merge for slice PRs.
- Short-lived slice branches rebase on the current feature trunk before their final gate. The
  published feature trunk itself merges `origin/main` forward and is never rebased beneath active
  slices.
- Each slice PR uses a conventional title, includes `Refs #764`, and stays below roughly 300 changed
  lines when cohesion permits. Do not use `Fixes #764` until the final cutover PR.
- A slice merges into the feature trunk only after focused tests, `make critique`, `make ci`, its
  warning-free trace, and the owner-visible contact-sheet gate described below.
- A slice that builds a pool, a group, or a corpus additionally answers the standing check in
  `2026-08-25-existing-selection-rules.md`: name every rule the legacy path applies at that stage
  and say, per rule, whether it is carried, superseded, or deliberately dropped. Green CI is not
  evidence here — both lost rules passed full CI and an independent review.
- Every gate runs on a corpus that is the owner's own material. A judgement measured on the wrong
  corpus tells you nothing: four rounds of prompt tuning were spent on behaviour that turned out to
  be caused by forwarded images in the pool.
- The final feature-trunk → `main` PR contains no new editorial behavior. It only syncs `main`,
  resolves integration drift, reruns all gates, and records the final owner verdict.

## Shared Public Contracts

Create immutable records in `src/immich_memories/analysis/editorial_contracts.py`. Add records only
when their first consumer lands; the complete target contract is:

```python
@dataclass(frozen=True)
class DecisionProvenance:
    pass_name: str
    pass_version: str
    schema_version: str
    model_identity: str
    input_ids: tuple[str, ...]
    sheet_hashes: tuple[str, ...]
    request_key: str
    cache_hit: bool

@dataclass(frozen=True)
class InsightEvidence:
    observation: str
    episode_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]

@dataclass(frozen=True)
class PeriodInsight:
    thesis: str | None
    evidence: tuple[InsightEvidence, ...]
    tensions: tuple[str, ...]
    recurring_threads: tuple[str, ...]
    unavailable_reason: str | None
    revision: int
    provenance: DecisionProvenance

@dataclass(frozen=True)
class EditorialCandidate:
    asset_id: str
    taken_at: datetime
    media_kind: Literal["photo", "video", "live_photo"]
    favourite: bool
    source: Asset
    proposed_segment: tuple[float, float] | None
    shippable_duration: float
    grounded_annotations: tuple[str, ...]

@dataclass(frozen=True)
class SelectedVisual:
    asset_id: str
    moment_id: str
    episode_id: str
    intended_segment: tuple[float, float] | None
    intended_duration: float
    render_options: tuple[Literal["still", "motion"], ...]

@dataclass(frozen=True)
class EditorialSelectionResult:
    selected: tuple[SelectedVisual, ...]
    insight: PeriodInsight | None
    trace: Trace
    warnings: tuple[str, ...]
    conservation: ConservationCheck
```

The only final public orchestration API is:

```python
def prepare_editorial_source(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> PreparedEditorialSource: ...

def select_editorial_cut(
    prepared: PreparedEditorialSource,
    dependencies: EditorialDependencies,
) -> EditorialSelectionResult: ...

def run_editorial_selection(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> EditorialSelectionResult:
    return select_editorial_cut(prepare_editorial_source(request, dependencies), dependencies)
```

The split form exists only so UI owner exclusions can be recorded between retrieval and Pass 0. It
is still one flow: the UI cannot select, score, suppress, or rebuild membership itself.

---

## Phase M: Evaluate the instruments before rewriting any pass

**Added 2026-08-27, owner directed: no pass is rewritten until the instrument
evaluation is finished.** Tasks 4–11 all assume a particular instrument answers a
particular question. Several of those assumptions have already been measured
false, and rewriting a pass around an instrument that does not work is more
expensive than measuring first.

Every entry descends the instrument ladder
(`docs/designs/2026-08-27-the-annotation-layer.md` §0b) and is calibrated against
answers the existing pipeline has already banked — no new model calls to validate.

### Settled

| question | instrument | verdict |
|---|---|---|
| representatives for the wall | farthest-first clustering | **46% better coverage**; the model is 3% better than random |
| `failed` | Laplacian variance | **works**; floor is the bottom ~1%, not 10% |
| documents / `notes` | Immich OCR coverage | **routes it** — 78% cleared as proof; cannot close it (a record shot sits at 18.8%) |
| `category: people` | Immich face clusters | **98% precision, 77% recall** |
| pair sameness | perceptual hash alone | **model required** — the hash drops distinct pictures |
| pair sameness | DINOv2 ViT-S | **ties the hash** (286 v 291); does not earn the dependency |
| `setting` | Places365 ResNet18 | **fails** — 60% even at high confidence; no person concept, and family photos are people photos |

### Open, in priority order

- [ ] **Persist the pairwise co-occurrence count in `people.yaml`** — the cheapest
      open win in the whole phase. `graph.py` already computes it to emit its
      `tight-dyad` links and then discards the numbers, so a sampled inner-tier
      person has `links: []`. One count per pair turns 11–12 candidates into 2 and
      unlocks every other relationship observation. **No model, no pixels, no new
      API call.** Write it as `constant` — a presence pattern, never a kinship
      noun — into `inferred:` only, so `confirmed.role` (free text) always wins.

- [x] **Is `setting` needed at all?** **Measured: no, and it needs no model.**
      Its only consumers store it, parse it, and show it to the review model as
      *context* — it gates nothing. Immich already returns a **named place** for
      **80%** of the dense month and 62% of the sparse one (Jette, Etterbeek,
      Watermael-Boitsfort…), which is richer context than a five-value guess and
      costs nothing. Grouping is unaffected: it uses GPS coordinates directly,
      not a category. **Do not evaluate another scene classifier for `setting`.**
- [ ] **The 23% of `people` faces miss** — someone photographed from behind.
      A person *detector* is the only thing that closes it: `hustvl/yolos-tiny`
      (Apache-2.0) or `onnxmodelzoo/ssd_mobilenet_v1_12` (Apache-2.0, COCO).
- [ ] **DINOv2 for EVENT clustering** — the job it was never tested on. It tied a
      hash on near-duplicates, where a hash is strong; "same event, different
      angle" is where a hash's pixel-layout assumption breaks. **Lower priority
      than it looks:** hash clustering already beats the model at representatives
      by 46%, so this is "can we do better than good enough", not a blocker.
- [ ] **`animal`** — 6% of labelled segments, and the only `category` value with
      no free source.
- [~] **Relationship inference** from `people.yaml` — rung 3, arithmetic.
      **Child detection works**: 5 found on this library with a 12-month
      tolerance. **Parents are blocked on one missing field** — "inner tier and
      predates the child" leaves 11-12 candidates, and the discriminator is
      co-occurrence with that child, which `graph.py` computes for its link
      detection and then discards. Persist one count per pair and the rest
      follows; no model, no pixels, no new API call.

### Reopened by an external survey, 2026-08-27

A second survey (Gemini) named models the first one missed. Repo existence and
licences below were **verified against the Hugging Face API**; sizes, latencies
and training-set claims are **the survey's, unverified here** — and some of its
citations look dubious (an arXiv id with a future date), so treat every number as
a claim to test rather than a fact.

**Two of this phase's conclusions are overturned and must be re-run.**

- [x] **Document vs photo — measured. Useful as a router; does not close the
      question.** `vlad-m-dev/mobilenet_v3_small_onnx_photo_doc`, MIT, **6.1 MB
      (1.7 quantized), 10 ms/image on CPU**, binary. On the dense month:

      | set | called `document` |
      |---|---|
      | high OCR coverage (≥15%) | **96%** |
      | **zero OCR boxes** | **0%** — never misfires on a text-free photo |
      | photo *containing* text (1–8%) | 32% — genuinely mixed |
      | the announcement card | **50%** |

      Sharper and cheaper than an OCR-coverage threshold, and it corrects the
      earlier claim that nothing off-the-shelf existed. **But it does not solve
      the hard case.** The announcement card splits 50/50 — correctly, because a
      designed card *is* a document by form. The question that matters was never
      "is this a document" but "is this document worth keeping", and a receipt and
      a birth announcement are both documents. That is editorial and no perception
      model closes it. **Cheap signals route; the model judges the shortlist.**

- [ ] ~~**Document vs photo — I said nothing off-the-shelf existed. Wrong.**~~
      `vlad-m-dev/mobilenet_v3_small_onnx_photo_doc` is **MIT with ONNX already
      exported** (fp32 and quantised), ~10–15 MB, claimed <50 ms. It is a binary
      *document / photo* classifier, which is exactly the distinction OCR
      coverage cannot make. **Test it on the known hard case first**: the birth
      announcement at 18.8% OCR coverage, which must come back *photo*, and a
      receipt, which must come back *document*. Survey caveat: trained on Italian
      documents and Japanese photos, so it may not transfer.
- [x] **Re-price the person detector — UNBLOCKED, and the answer is a different
      shape.** **OpenCV Zoo ships NanoDet-m-plus-1.5x 416 as official ONNX,
      Apache-2.0 for every file in the directory, 3.62 MB FP32 / ~1 MB INT8**, with
      published Raspberry Pi 4 latency of **215 ms** — about 72 minutes for 20,000
      images. So it is affordable **at ingestion, once per asset**, and not
      affordable as a query-time pass. That is the re-pricing: the model is fine,
      the *stage* was wrong.

      **And do not ask it for a subject label.** Aggregate detections into
      supercategories, weighting each by confidence × box-area share, then combine
      with signals already held: people from person boxes **plus Immich face count
      and face area**; animal from COCO animal classes; screen from `tv`/`laptop`/
      `cell phone` **plus OCR**; object from the remaining salient boxes;
      landscape from Places365 when object coverage is low. This handles "mainly
      people" properly and permits mixed images rather than forcing one bucket —
      and it dissolves the `category`-versus-presence confusion recorded above.

- [x] ~~**Re-price the person detector — blocked on obtaining the model.**~~ The
      decline stands at `ssd_mobilenet_v1_12`, 29.5 MB / 16 ms. The survey's
      NanoDet-Plus claim (<2 MB INT8, ~10 ms, Apache-2.0) is **unverified**: no
      ready ONNX export exists on Hugging Face under any of the obvious names,
      so it would have to be exported from the upstream repo. Until then the
      re-pricing cannot happen and the decline holds.
      **New landmine, verified:** `Xenova/yolov9-c_all` is **GPL-3.0**. Add YOLOv9
      to the excluded list alongside Ultralytics.
      **Scope reminder:** the decline is about `category` (what a picture is
      mainly *of*). Using a detector for *presence*, to close the 23% of people
      that face detection misses, is a different question this decline does not
      cover.

**Three additions worth evaluating.**

- [x] **Eyes-open — use OpenSeeFace.** BSD-2-Clause for **both code and models**,
      ships ONNX, 1.8–12.9 MB. Run it only on the face crops Immich already
      provides, and only on a candidate set — never the library. Treat it
      probabilistically, as a tie-break between otherwise similar burst frames.
      The author notes eye-region tracking is the weaker part, so validate on
      glasses, profiles and children before trusting it.

- [ ] **SPAQ for technical quality.** *Downgraded:* the surveyed learned IQA
      models are all export-required, 100 MB+, or licence-unclear at the
      checkpoint (ARNIQA ~107 MB no ONNX; MUSIQ ~108 MB, checkpoint licence not
      restated; NIMA's circulating weights are third-party reimplementations).
      **Classical measurement remains the better engineering fit** — with the
      refinement that sharpness should be computed **inside the face/subject
      region**, not globally, and paired with directional-gradient anisotropy to
      separate motion smear from deliberate defocus. That addresses the
      shallow-depth-of-field false positive without a model. Trained on 11,125 smartphone photographs
      from 66 phones, annotated for brightness, colourfulness, contrast,
      graininess and **sharpness** — not on contest or stock imagery. This
      directly addresses the false-positive class flagged in the blur work:
      a Laplacian cannot tell deliberate shallow depth of field from a failed
      focus, and a model trained on phone photographs plausibly can. **Compare it
      against the Laplacian floor on the same frames.**
- [ ] **Intel `open-closed-eye-0001`** — Apache-2.0, **46 KB**, claimed <1 ms, on a
      32×32 eye crop. Immich already supplies face boxes, so this is nearly free.
      A blink is a real reason to prefer a sibling frame.
- [ ] **DINOv2 for event clustering, confirmed as the right tool** — and **SSCD is
      not**, despite being MIT and compact: it is trained to push *different*
      originals apart, so two photographs of one cake from opposite sides are
      pushed apart by design. Useful confirmation that the DINOv2 rejection was
      correctly scoped to near-duplicates only.

**One landmine caught:** `MichalMlodawski/open-closed-eye-classification-mobilev2`
is **CC-BY-NC-ND-4.0** — verified. Non-commercial *and* no-derivatives. Excluded.

**One discrepancy to resolve:** the survey puts DINOv2 ViT-S CPU latency at
100–150 ms; measured here it was **18 ms**. Probably input size — 224px
thumbnails here against full-resolution there. Do not quote either number without
saying which input it was measured on.

### Rules for this phase

- Calibrate against **banked answers**, never against intuition.
- A band that **acts** needs unanimity on real data plus a second period; a band
  that only ever **keeps** is safe by construction.
- **Look at the pixels** for every counterexample before believing a table.
- Record negatives with their numbers in §6b of the annotation-layer design. Half
  of this phase's value is the models we do *not* add.

## Task 0: Bootstrap CI for the Feature Trunk

**Branch:** `ci/764-feature-trunk`, based on `feature/764-editorial-selection`

**Files:**

- Modify: `.github/workflows/ci.yml:4-5, 229-263`
- Modify: `.github/workflows/benchmark.yml:31-43`
- Modify: `Makefile:464-548`

- [ ] Run `make dev`, then capture the current failure mode without changing files:

```bash
make dev
make -n integration-coverage-for-diff DIFF_BASE=origin/feature/764-editorial-selection
```

Expected before implementation: the printed Git diff still contains `origin/main`.

- [ ] Make the diff base injectable once, with `main` as the safe default:

```make
DIFF_BASE ?= origin/main

diff-cover:
	uv run pytest --cov=src/immich_memories --cov-branch --cov-report=xml -q
	uvx diff-cover coverage.xml --compare-branch=$(DIFF_BASE) --fail-under=80
```

Use `$(DIFF_BASE)` in `diff-cover-local`, `integration-coverage-for-diff`, and `diff-cover-ci`,
including line-count and changed-path Git diffs.

- [ ] Allow CI and benchmark pull-request workflows to run when the PR base is the feature trunk:

```yaml
on:
  pull_request:
    branches: [main, feature/764-editorial-selection]
```

- [ ] Pass the actual PR base to every diff-aware target and report command:

```yaml
env:
  DIFF_BASE: origin/${{ github.base_ref }}
run: |
  make integration-coverage-for-diff
  make diff-cover-ci
```

- [ ] Verify the resolved commands, then the repository gate:

```bash
make -n integration-coverage-for-diff DIFF_BASE=origin/feature/764-editorial-selection
make ci
```

Expected: no hard-coded `origin/main` appears in the diff commands when `DIFF_BASE` is supplied;
all CI checks pass.

- [ ] Commit and merge this bootstrap into the feature trunk:

```bash
git add Makefile .github/workflows/ci.yml .github/workflows/benchmark.yml
git commit -m "ci(selection): support feature-trunk pull requests (#764)"
```

**Gate:** Open a deliberately empty test PR against the feature trunk or inspect this PR's
`pull_request` run. Quality, test matrix, commitlint, and diff coverage must all start and compare
against `origin/feature/764-editorial-selection`.

---

## Task 1: Establish Contracts, Chronological Trace, and Conservation

**Branch:** `feat/764-visual-foundation`

**Files:**

- Create: `src/immich_memories/analysis/editorial_contracts.py`
- Modify: `src/immich_memories/analysis/selection_trace.py:34-116, 172-269`
- Create: `tests/test_editorial_contracts.py`
- Create: `tests/test_editorial_trace.py`
- Modify: `tests/test_selection_accountability.py`

- [ ] RED: add one public behavior test proving chronological IDs survive and every input has one
  fate:

```python
def test_editorial_pass_keeps_chronology_and_conserves_every_input() -> None:
    trace = Trace()
    trace.record_editorial_pass(
        PassTrace(
            name="cull",
            input_ids=("late", "early", "middle"),
            kept_ids=("late", "middle"),
            rejected=(TraceDecision("early", "unusable exposure"),),
            unresolved=(),
            duration_before=12.0,
            duration_after=8.0,
            provenance=_provenance(("late", "early", "middle")),
        )
    )

    payload = trace.as_dict()
    assert payload["editorial_passes"][0]["kept_ids"] == ["late", "middle"]
    assert payload["editorial_passes"][0]["conservation"]["valid"] is True
```

Run `make test-one T="tests/test_editorial_trace.py"`; expect failure because the editorial trace
API does not exist.

- [ ] GREEN: add frozen tuple-based `DecisionProvenance`, `TraceDecision`, `PassTrace`,
  `RequestTrace`, and `ConservationCheck`. Extend the existing `Trace`; do not create a second
  ledger. Preserve the legacy `record()` adapter for the old selector until Task 14.

- [ ] RED/GREEN: add a test where one input is both kept and rejected and one disappears. The pass
  must produce `valid=False`, list duplicate and missing IDs, and add a `!! conservation failure`
  warning.

- [ ] RED/GREEN: assert Markdown emits exactly `showing 12 of N; full list in JSON` when decisions
  are abbreviated, while JSON remains complete.

- [ ] REFACTOR: keep serialization in `selection_trace.py`; keep immutable cross-pass values in
  `editorial_contracts.py`. Avoid a registry or generic event bus.

- [ ] Run:

```bash
make test-one T="tests/test_editorial_contracts.py tests/test_editorial_trace.py tests/test_selection_accountability.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/editorial_contracts.py src/immich_memories/analysis/selection_trace.py tests/test_editorial_contracts.py tests/test_editorial_trace.py tests/test_selection_accountability.py
git commit -m "feat(selection): account for every editorial decision (#764)"
```

---

## Task 2: Build One Reusable Visual Atlas

**Files:**

- Create: `src/immich_memories/analysis/visual_atlas.py`
- Create: `src/immich_memories/analysis/contact_sheets.py`
- Modify: `src/immich_memories/processing/frame_sampling.py:29-136`
- Modify: `src/immich_memories/analysis/moment_reading.py:35-178, 293-359`
- Modify: `src/immich_memories/analysis/thumbnail_prefetch.py:38-128`
- Create: `tests/test_visual_atlas.py`
- Create: `tests/test_contact_sheets.py`
- Modify: `tests/test_frame_extraction.py`
- Modify: `tests/test_moment_reading.py`

- [ ] RED: create generated red/green/blue frames and assert a video becomes one chronological
  filmstrip tile, with no requester involved:

```python
def test_video_tile_is_one_locally_composed_chronological_filmstrip(tmp_path: Path) -> None:
    source = fake_video_source(tmp_path, colors=("red", "green", "blue"))
    atlas = build_visual_atlas((source,), frame_cache_dir=tmp_path / "frames")

    tile = atlas.tile_for(source.asset.id)
    assert tile.kind == "filmstrip"
    assert tile.frame_count == 3
    assert tile.sha256 == sha256(tile.jpeg_bytes).hexdigest()
```

Run `make test-one T="tests/test_visual_atlas.py"`; expect import failure.

- [ ] GREEN: add `AtlasSource`, `AtlasTile`, and `VisualAtlas`. Photos use cached/server preview
  JPEGs. Videos and Live Photo motion options call a new cached segment-aware sampler:

```python
def sample_segment_frames(
    video: Path,
    *,
    start_time: float,
    end_time: float,
    count: int,
    width: int,
    cache_dir: Path | None,
) -> tuple[Path, ...]: ...
```

Its cache identity includes source path metadata, segment bounds, count, width, and a render
version. Move useful `.part`, zero-byte, and short-video fallback behavior from
`scripts/contact_sheet.py:118-182` into this core seam; the core must not import the script.

- [ ] RED/GREEN: prove a changed segment or render version invalidates cached filmstrip frames.

- [ ] RED: assert one encoded page's bytes are identical across hash, disk, request attachment,
  and trace:

```python
page = build_contact_sheets(tiles, scope_id="episode-1", output_dir=tmp_path)[0]
assert page.path.read_bytes() == page.jpeg_bytes
assert sha256(page.jpeg_bytes).hexdigest() == page.sha256
assert page.tile_refs == tuple(TileRef(i + 1, tile.entity_id) for i, tile in enumerate(tiles))
```

- [ ] GREEN: generalize `sheet_layout`, `sheets_of`, and `tile_sheet` from
  `moment_reading.py`. `ContactSheetPage` holds `sheet_id`, path, exact JPEG bytes, SHA-256, ordered
  `TileRef`s, and layout version. Keep the current 120-tile/2100px safety bounds and global tile
  numbering.

- [ ] REFACTOR: adapt `moment_reading.py` to the shared sheet builder without changing its legacy
  behavior. This proves reuse before the new editor depends on it.

- [ ] Run focused tests and FFmpeg coverage:

```bash
make test-one T="tests/test_visual_atlas.py tests/test_contact_sheets.py tests/test_frame_extraction.py tests/test_moment_reading.py"
make test-integration-processing
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/visual_atlas.py src/immich_memories/analysis/contact_sheets.py src/immich_memories/processing/frame_sampling.py src/immich_memories/analysis/moment_reading.py src/immich_memories/analysis/thumbnail_prefetch.py tests/test_visual_atlas.py tests/test_contact_sheets.py tests/test_frame_extraction.py tests/test_moment_reading.py
git commit -m "feat(selection): build a reusable visual atlas (#764)"
```

---

## Task 3: Pack, Attach, Cache, and Trace Visual Requests

**Files:**

- Create: `src/immich_memories/analysis/visual_request_planner.py`
- Create: `src/immich_memories/analysis/editorial_gateway.py`
- Modify: `src/immich_memories/analysis/llm_query.py:138-193, 228-445`
- Modify: `src/immich_memories/cache/judgment_cache.py:40-110`
- Modify: `src/immich_memories/analysis/selection_trace.py`
- Create: `tests/test_visual_request_planner.py`
- Create: `tests/test_editorial_gateway.py`
- Modify: `tests/test_llm_query.py`
- Modify: `tests/test_judgment_cache.py`

- [ ] RED: prove planning conserves complete groups and defaults to one sheet per request:

```python
def test_request_plan_conserves_groups_without_guessing_provider_limits() -> None:
    plans = plan_visual_requests(groups=(group("a"), group("b")), limits=VisionRequestLimits())
    assert tuple(group_id for plan in plans for group_id in plan.group_ids) == ("a", "b")
    assert all(len(plan.pages) == 1 for plan in plans)
```

- [ ] GREEN: add pure `VisionRequestLimits`, `SheetGroup`, `VisualRequestPlan`, and
  `plan_visual_requests()`. Oversized groups get explicit numbered continuations; a group is never
  silently split across unrelated requests.

- [ ] RED/GREEN: pass a page into `EditorialGateway.ask()` and decode the outgoing request payload.
  Assert its image SHA-256 equals the page and request-trace hashes exactly.

- [ ] GREEN: define one provider-neutral seam:

```python
class EditorialGateway(Protocol):
    def ask(self, request: VisualEditorialRequest) -> BankedVisualAnswer: ...
```

`VisualEditorialRequest` contains pass/prompt/schema versions, ordered IDs, exact pages, grounded
annotations, upstream decision versions, and request limits. It returns raw complete text plus
provenance; pass-specific parsers own semantics.

- [ ] RED/GREEN: add `VisualJudgmentIdentity` and `VisualJudgmentCache` without changing the legacy
  text-cache meaning. Independently test invalidation for pixels, page order, annotations, model,
  prompt/schema/render/layout version, and upstream insight version. Exclude API keys.

- [ ] RED/GREEN: make request trace record planned calls, actual calls, cache hits, attached pages,
  tile count, provider/model identity, and every retry. A cache hit records original provenance and
  current reuse.

- [ ] Probe before increasing call packing. Against generated sheets and the configured model,
  compare `image_detail=low` with `auto`/`high`, then one versus multiple pages per request. Require
  exact tile accounting and equivalent decisions. Record the result outside Git. Keep production
  defaults at one JPEG if the probe is inconclusive.

- [ ] Run:

```bash
make test-one T="tests/test_visual_request_planner.py tests/test_editorial_gateway.py tests/test_llm_query.py tests/test_judgment_cache.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/visual_request_planner.py src/immich_memories/analysis/editorial_gateway.py src/immich_memories/analysis/llm_query.py src/immich_memories/cache/judgment_cache.py src/immich_memories/analysis/selection_trace.py tests/test_visual_request_planner.py tests/test_editorial_gateway.py tests/test_llm_query.py tests/test_judgment_cache.py tests/test_editorial_trace.py
git commit -m "feat(selection): bank visual editorial requests (#764)"
```

**Slice gate:** The attached provider payload, saved sheet, and trace hash are byte-identical. The
trace reports the measured request count and no tile disappears under packing.

---

## Task 4: Prepare the Complete Source-Eligible Corpus

**Branch:** `feat/764-insight-cull`

**Files:**

- Create: `src/immich_memories/analysis/selection_flow.py`
- Modify: `src/immich_memories/analysis/source_filter.py:68-113`
- Modify: `src/immich_memories/analysis/moment_grouping.py:66-138`
- Modify: `src/immich_memories/analysis/source_quality.py`
- Create: `tests/test_selection_source.py`
- Modify: `tests/test_moment_grouping.py`
- Modify: `tests/test_pool_stages_are_on_the_record.py`

- [ ] RED: feed a pregnancy-test image, a screenshot, a short clip, and an owner-excluded asset
  through `prepare_editorial_source()`. Assert only the explicit owner exclusion leaves before
  Pass 0; the other signals are annotations:

```python
def test_only_source_scope_and_owner_exclusions_apply_before_pass_zero() -> None:
    prepared = prepare_editorial_source(request_with_four_assets(), fake_dependencies())
    assert prepared.candidate_ids == ("pregnancy-test", "screenshot", "short-clip")
    assert prepared.excluded_ids == ("owner-excluded",)
    assert prepared.trace.story_of("pregnancy-test").first_pass == "pass-0"
```

- [ ] GREEN: implement `SourceScope`, `EditorialSelectionRequest`, `EditorialDependencies`,
  `PreparedEditorialSource`, `prepare_editorial_source()`, and a normalized `EditorialCandidate`
  adapter for raw `Asset`/`VideoClipInfo`. Eligibility is date/library scope, supported media, and
  explicit hard owner exclusions only.

- [x] RED/GREEN: provenance is part of scope, not evidence. A Live Photo's motion component and
  anything `not_shot_here()` rejects are excluded before Pass 0, each recorded with a named reason
  so the account still answers for the whole fetch. `SourceScope` carries the filename patterns and
  the stills-need-a-camera flag; a star still overrides both. Landed `b8ecc10` + this branch.

- [ ] RED/GREEN: decide, do not default, whether `with_burst_neighbours()` and
  `expand_to_neighbors()` are still needed. The editorial path fetches a date range rather than a
  person, so they may be genuinely unnecessary — record which, and why, in the rules inventory.

- [ ] RED/GREEN: expose pure chronological `build_episode_groups()` and `build_moment_groups()`
  over already-eligible candidates. Stable group IDs derive from ordered asset IDs and grouping
  version. Move no winners here.

- [ ] GREEN: retain re-encode, resolution, duration, blur, burst, inferred subject, and similarity
  findings as grounded evidence. Do not call legacy subject quota, thumbnail winner, burst winner,
  photo-vs-motion suppression, or density shortlist in the new flow.

- [ ] Run:

```bash
make test-one T="tests/test_selection_source.py tests/test_moment_grouping.py tests/test_pool_stages_are_on_the_record.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/selection_flow.py src/immich_memories/analysis/source_filter.py src/immich_memories/analysis/moment_grouping.py src/immich_memories/analysis/source_quality.py tests/test_selection_source.py tests/test_moment_grouping.py tests/test_pool_stages_are_on_the_record.py
git commit -m "feat(selection): start from the complete eligible corpus (#764)"
```

---

## Task 5: Read Episodes and Form a Provisional Period Insight

**Files:**

- Create: `src/immich_memories/analysis/period_insight_answer.py`
- Create: `src/immich_memories/analysis/period_insight.py`
- Create: `tests/test_period_insight_answer.py`
- Create: `tests/test_period_insight.py`
- Create: `tests/test_period_insight_generalisation.py`

- [ ] RED: assert strict parsing accepts the final complete balanced object, maps displayed numbers
  to stable IDs, and rejects an auto-closed truncated object.

```python
def test_truncated_episode_answer_cannot_select_representatives() -> None:
    raw = '{"episode_id":"day-1","representative_tiles":[1,2]'
    assert read_episode_answer(raw, episode_id="day-1", tile_map={1: "a", 2: "b"}) is None
```

- [ ] GREEN: implement strict `read_episode_answer()` and `read_period_answer()`. Do not reuse
  `_parse_json_object_text()` from `llm_response_parser.py` because it repairs truncation.

- [ ] RED/GREEN: build chronological episode sheets from every prepared candidate, request short
  `EpisodeReading`s and ~~reasoned representative IDs~~ **(struck: representatives are
  chosen by clustering, not asked for -- see the revisit note below)**, then build the
  period sheet from every episode
  reading and its representatives. A representative decision makes the period wall legible; it
  does not cull the episode.

- [ ] RED/GREEN: if any required visual evidence is unreadable, return `PeriodInsight(thesis=None,
  unavailable_reason=...)`, retain all assets for Cull, and add `!!`. Never invent representative
  IDs mechanically and present them as editorial.

- [ ] RED/GREEN: the generalisation matrix swaps cycling/live-show labels while preserving pixels
  and evidence. The shape of the insight evidence must remain stable; tests must not assert exact
  prose.

- [ ] Add Pass 0 to `selection_flow.py`, bank its answer, and record actual call count. At this
  point the new flow is observation-only and has no public caller.

- [ ] Run:

```bash
make test-one T="tests/test_period_insight_answer.py tests/test_period_insight.py tests/test_period_insight_generalisation.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/period_insight_answer.py src/immich_memories/analysis/period_insight.py src/immich_memories/analysis/selection_flow.py tests/test_period_insight_answer.py tests/test_period_insight.py tests/test_period_insight_generalisation.py
git commit -m "feat(selection): read the period before making cuts (#764)"
```

---

> **Revisited 2026-08-27.** `representative_tiles` is a superlative over N — the shape
> measured at 0 of 12 following tile position rather than the picture. Probed under
> cyclic rotation on six real episodes: **0.42 overlap by picture, 0.42 by position,
> against a 0.22 random floor.** It is close to arbitrary, it ships today, and the
> period wall — and therefore the thesis — is built on it. n=6 is thin; the finding is
> "this is not a reasoned choice", not a precise number.
>
> It is also the sub-question with the best mechanical answer. Mapping hint 5 makes
> coverage first-class: headings get ticked off, and an over-covered heading stops
> competing. "Which tiles make this wall legible" asks what the episode CONTAINS, so it
> is clustering — deterministic, free, and unable to anchor on position because
> position is not an input.
>
> `visual_summary` stays with the model. It is language, and it scales with episodes
> (~400 for a year) rather than assets.

## Task 6: Fuse Cull and the Record-Shot Lane into Episode Scans

**Files:**

- Create: `src/immich_memories/analysis/cull_answer.py`
- Create: `src/immich_memories/analysis/selection_cull.py`
- Modify: `src/immich_memories/analysis/period_insight.py`
- Modify: `src/immich_memories/analysis/subject_policy.py:28-177, 215-274`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_cull_answer.py`
- Create: `tests/test_selection_cull.py`
- Modify: `tests/test_subject_policy.py`

- [ ] RED: one episode response contains independent `episode_reading`, `record_shots`, and
  `cull_rejects` namespaces. A record/reject collision invalidates only the rejection:

```python
def test_record_mark_wins_a_namespace_collision_without_discarding_siblings() -> None:
    answer = read_episode_scan_answer(raw_collision(), tile_map={1: "test", 2: "blur"})
    assert answer.record_shots[0].asset_id == "test"
    assert answer.cull_rejects == (CullDecision("blur", "unusable motion blur"),)
    assert answer.warnings == ("!! cull reject conflicted with record-shot mark: test",)
```

- [ ] GREEN: version the episode-scan schema and request identity. Ask record-shot function first;
  Cull may reject only clearly unusable non-record items. Subject labels, repetition, relative
  weakness, and thesis relevance are invalid Cull reasons.

- [ ] RED/GREEN: freeze `episode-scan-v3` at one physical page per request with unique pack-local
  tile aliases. Scopes exactly partition that page. A future multi-page request bumps the pass,
  prompt, and schema instead of reinterpreting v3 aliases.

- [ ] RED/GREEN: normalize duplicate snapshots with favourite OR semantics. Preserve a complete,
  aligned, source-owned `LivePhotoRenderingFamily` with a versioned manifest hash and propagate its
  reference to every admitted member. Owner exclusions trim aligned entries. Incomplete,
  contradictory, or cross-moment manifests warn and create no family; never elect a carrier or a
  render mode here.

- [ ] RED/GREEN: refusal, timeout, invalid IDs, and truncated JSON reject nothing. Valid sibling
  namespaces remain banked. More than 75% rejection adds `!! possible over-cull` but does not
  restore by score.

- [ ] RED/GREEN: convert `classify_subject()` to evidence-only use in the new path. Screenshots,
  documents, and the pregnancy-test fixture reach the visual request. Legacy quota functions stay
  callable only for the old selector until Task 13.

- [ ] Add `run_cull()`:

```python
def run_cull(
    episodes: Sequence[EpisodeSheet],
    *,
    requester: EditorialGateway,
    trace: Trace,
) -> CullPassResult: ...
```

Return chronological survivors, independent `RecordShotMark`s, decisions, warnings, and
provenance. Add it after Pass 0 in `selection_flow.py`.

- [ ] Run:

```bash
make test-one T="tests/test_cull_answer.py tests/test_selection_cull.py tests/test_subject_policy.py tests/test_selection_source.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/cull_answer.py src/immich_memories/analysis/selection_cull.py src/immich_memories/analysis/period_insight.py src/immich_memories/analysis/subject_policy.py src/immich_memories/analysis/selection_flow.py tests/test_cull_answer.py tests/test_selection_cull.py tests/test_subject_policy.py
git commit -m "feat(selection): cull visibly and preserve record shots (#764)"
```

**Slice gate:** The private Pass 0/1 sheet is judgeable and chronological; every source-eligible
asset appears; the pregnancy test is marked as a record shot; refusal/truncation rejects nothing;
the trace reports episode-scan packs rather than one request per asset.

Measured on the first real run, and required of any re-run:

- The sheet contains no material the owner did not photograph. Before provenance was restored the
  same day yielded 15 candidates of which 6 were forwarded, and the record lane marked tweet
  screenshots and an advert — record marks shield from Cull, so the lane was protecting the junk.
  With provenance applied the same day yields 9, and the marks are the medical result and the
  handwritten notes.
- A record shot is proof of something that happened to these people. Legible text is never enough
  on its own, and the scene has to be one they were in.
- Cull returns an empty array unless the pixels are unusable. Confabulated defects are the failure
  to watch for: three invented rejects on the first run, then seven carrying one identical copied
  label. The asymmetry is the reason to keep — a wrong keep is fixed by a later pass a person can
  check, a wrong cull is permanent and invisible.
- Zero `!!` in the trace. Two of the first three runs looked clean by their decision counts alone.

---
> **Revisited 2026-08-27 against the inventory.** Cull is the craft's *fast* pass —
> Hurn marks a whole contact sheet in one sitting, and Cooke's rule is that anything
> not clearly bad survives by default. Both of its decision buckets are camera-back
> questions in Thein's sense and neither needs a language model:
>
> - `failed` is technical, and `photos/frame_quality.py` already measures sharpness,
>   contrast and exposure — it is imported only by the legacy photo pipeline. Measured
>   on the dense month, the softest photograph of 1,725 is genuinely motion-blurred and
>   **Cull banked it as `ordinary`**, so the zero-`failed` result is the pass
>   under-firing rather than clean material.
> - `notes` is a question about text, and Immich has already answered it:
>   `GET /api/assets/{id}/ocr` returns boxes and strings for every asset. 2,016 assets
>   in 22 seconds; **78% have zero boxes**, which is a proof of not-a-document rather
>   than a probability.
>
> **Exposure may not actuate on its own.** Of 37 blown-highlight photographs, 35 carry
> real text and are documents — including a designed card among the most valuable
> images in the library. Blur is clean: 0 of the 20 softest carry any text. This is
> mapping hint 11 — a record shot is judged on whether it is the only one, not on
> whether it is good — and an unguarded exposure rule violates it.
>
> `SourceEvidence` already declares `blur`, `exposure` and `similarity` and reaches no
> candidate. Wiring it is the change, not writing new measurement.

## Task 7: Reduce Repetition Without Asking the Model to Rank

**Branch:** `feat/764-selects`

**Rewritten 2026-08-26.** The original task asked for one `MomentSelect` per group chosen by a
visual battle. That question was measured against the real model and it does not work: see
`docs/implementation-plans/2026-08-26-what-the-model-can-be-asked.md`. Peak-of-N follows tile
position, not the picture, in **0 of 12** cases across widths 3–8 and fidelities 150–700px, while
the answers parse cleanly and carry fluent grounded-sounding reasons. Built as specified, this pass
would ship confident nonsense through every gate the project has.

The rule the measurements support: **a pass may ask the model to classify against a fixed
definition; it may not ask the model to rank its inputs against each other.** Cull's questions point
at something outside the comparison and are byte-identical across repeats. Every ranking question
points only inward, and with nothing to anchor on the model anchors on position.

The craft says the same. duChemin: "Pick them or don't pick them, but don't rate them." Thein builds
a rank as an *output* of repeated binary rounds. Eisenhardt: documentary selects keep **25–50%**,
and "the big cuts happen later, at structure, not at the item filter." Selects marks. Structure cuts.

**Files:**

- Create: `src/immich_memories/analysis/selection_selects.py`
- Modify: `src/immich_memories/analysis/editorial_contracts.py`
- Modify: `src/immich_memories/analysis/selection_flow.py` (after the source split below)
- Create: `tests/test_selection_selects.py`
- Modify: `tests/test_favourite_law.py`

**Prerequisite:** `selection_flow.py` is at 798 of 800 lines and Tasks 7–11 all modify it. Split
source preparation out first, as its own commit — its tests are already `test_selection_source.py`,
and the split lets the orchestrator import passes at module level instead of through the
function-local import that papers over the cycle today.

### A. Arithmetic absorbs the repetition it can prove

- [ ] RED/GREEN: candidates sharing an exact capture instant inside one moment collapse to one
  survivor. Measured share of a real month: **558 of 1468 candidates (38%)** in the dense month,
  1 of 261 in the sparse one. Zero model calls.

- [ ] The survivor is chosen by a **stated** rule, in this order: a favourite; then the existing
  `SourceEvidence`; then the earliest. Written to the trace as a rule, never as a judgement. This is
  not scoring returning by the back door — Thein's set test is "at a glance, one should not be
  mistaken for another", so when two frames are the same instant, which one ships is not an
  editorial question.

- [ ] RED/GREEN: two devices at one instant are two *vantages*, so record what was absorbed and why.
  This is the one place the rule is known to be occasionally wrong, and the trace has to admit it.

- [ ] Do NOT extend this to a similarity threshold. "Within 2 seconds" would absorb 50% of the dense
  month, and a 2-second gap is a different photograph, not the same one.

### B. The model is asked only questions with an external referent

- [ ] **Probe before building.** No question for this stage has been measured yet, and it must not
  be guessed. Build the probe from the production path, delete its judgment cache first, and check
  every candidate question under **cyclic rotation** — a question whose answer follows tile position
  rather than the picture is not usable at any width or fidelity.

- [ ] The question must be answerable about **one tile against a definition**, in Cull's shape.
  "Which is better", "which are alike" and "which is the peak" are all excluded by measurement.

- [ ] Whatever survives the probe actuates only through the intersection of **two arrangements** of
  the same tiles. Thein: "those that overlap are the ones that make it into the final cut."
  Disagreement means keep, matching the project's asymmetry — a wrong keep is fixed by a later pass
  a person can check, a wrong cut is permanent and invisible.

- [ ] Sheets for this pass declare their own `tile_px`. The answer moved between 150px and 400px in
  4 of 4 moments and stopped moving above 400px, so packing battles to the 120-tile page cap would
  run the pass at 150px — 86% less pixel area on its own decision.

### B2. A cheap band may route the question, and does not yet exist

**Added 2026-08-27.** Q4 is the only visual question that scales badly — roughly
13,600 calls for a year — so a pixel measure that settles the easy pairs is the whole
cost story. Two things are measured and one is not.

- **An embedding does not beat a hash here.** On 656 real pairs the perceptual hash's
  unanimous band is 291 and DINOv2 ViT-S's is 286, against 18 ms/image and an 86.6 MB
  model. Both cap at 44%, which says the ceiling is the pairs rather than the signal.
  Do not add the dependency for this question.
- **The published band does not survive cross-validation.** Recalibrated on `pair-v2`
  verdicts, the unanimous band collapses to **zero on both months** — there is a pair at
  hamming distance 0 the model calls different. The 291/291 figure was calibrated
  against `pair-v1`, the prompt that was then replaced. **Ground truth has a version.**
- **Untested:** an 8×8 dhash is 64 bits of low-frequency structure and distinct pictures
  can collide. `compute_thumbnail_hash` takes `hash_size`; 16 is the next probe.

Any band that ACTS must be unanimous on two periods, not correlated on one. A band that
only ever keeps both frames is safe by construction and needs no such proof.

### C. Selects marks; it does not reduce to one per moment

- [ ] RED/GREEN: a moment may keep several survivors. There is no "exactly one representative"
  requirement, and no `no_peak` verdict, because neither is a question the model can answer.

- [ ] RED/GREEN: a favourite always survives this pass. Where several favourites share one moment
  they all survive; Structure decides among them with the whole cut visible.

- [ ] RED/GREEN: a moment whose model answers disagree is left whole with a warning, not resolved
  arbitrarily. Scalar score never selects a fallback.

- [ ] Report the surviving share in the trace. The craft's expectation is 25–50%; a pass cutting far
  past that is cutting on something it cannot justify.

### D. Grouping is content-blind and this task does not fix it

Moments are grouped by time and place, so one moment holds several attempts at several pictures —
the model objected to this on its own, unprompted: "1-3 are one attempt (baby sleeping) and 4-7 are
another (man posing with empty carrier)". Chaining is **not** the defect; measured, only 1 moment
across two months is a drizzle-chain, and median gaps inside the big moments are 0.0–4.2 seconds.

- [ ] Record the limitation in the trace rather than working around it. Sub-splitting a moment by
  content needs a stable partition, and the partition question measured at pair Jaccard **0.15**.

- [ ] Do not tune `MOMENT_WINDOW_MINUTES` to compensate. No time-and-place rule can separate two
  different things happening in one room inside ten minutes.

### What this task must not do

- [ ] Do not ask for a peak, a best, a winner, a rank, or an alternate ranked against a chosen one.
- [ ] Do not consume Cull's `ordinary` bucket as a decision. It was formed at 161–210px on a
  question needing 400px, and it is the pass's own judgement arriving pre-made. It may route which
  moments are worth looking at; it may not say which frame wins.
- [ ] Do not treat a parsed answer as a working one. Every failed shape in the measurements parsed.

- [ ] Run:

```bash
make test-one T="tests/test_selection_selects.py tests/test_favourite_law.py tests/test_same_moment.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/selection_selects.py src/immich_memories/analysis/editorial_contracts.py src/immich_memories/analysis/selection_flow.py tests/test_selection_selects.py tests/test_favourite_law.py
git commit -m "feat(selection): reduce repetition without asking the model to rank (#764)"
```

**Slice gate:** the sheet shows what was absorbed as an exact-instant duplicate and by which rule,
what the model marked and under how many arrangements, and what survived. Every favourite present.
Surviving share inside 25–50% or a stated reason why not. Zero `!!`, and the count of model calls
matches the count the trace planned.

## Task 8: Move Expensive Analysis behind Selects

- [ ] RED/GREEN: carry `looks_like_a_photograph()`. Anything with a still goes to the photo scorer,
  Live Photo or not. Sent to the video analyser a burst carrier "fails in milliseconds, is marked
  attempted, and is never looked at again" — and ships undescribed.

**Files:**

- Modify: `src/immich_memories/analysis/smart_pipeline.py:269-390`
- Modify: `src/immich_memories/photos/photo_pipeline.py:63-138, 648-721`
- Modify: `src/immich_memories/analysis/llm_response_parser.py:227-284`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_selected_visual_analysis.py`
- Modify: `tests/test_pipeline_integration.py`

- [ ] RED: script a corpus of 20 assets resolving to four representatives and one alternate. Assert
  content/deep analysis runs only for those five visuals after Pass 2, while every source asset was
  visible in Pass 0/1.

- [ ] GREEN: add `analyze_selected_visuals()` after Selects. Materialize selected video segments,
  compute actual shippable durations, and attach objective motion/audio/content evidence to the
  selected visual contracts. Do not call `_enhance_with_llm()` once per discarded photo.

- [ ] RED/GREEN: when selected media cannot be analyzed or rendered into its promised segment,
  leave the editorial result unresolved with `!!`; do not silently substitute the next score.

- [ ] REFACTOR: use the atlas's frame sampler instead of the uncached
  `ContentAnalyzer.extract_frames()` path where the evidence is identical.

- [ ] Run:

```bash
make test-one T="tests/test_selected_visual_analysis.py tests/test_pipeline_integration.py tests/test_frame_extraction.py"
make test-integration-processing
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/smart_pipeline.py src/immich_memories/photos/photo_pipeline.py src/immich_memories/analysis/llm_response_parser.py src/immich_memories/analysis/selection_flow.py tests/test_selected_visual_analysis.py tests/test_pipeline_integration.py
git commit -m "refactor(selection): analyze the visuals that reach the cut (#764)"
```

**Slice gate:** The Pass 2 sheet exposes complete moment battles, correct favorite behavior, the
pregnancy-test sidecar, bounded alternates, actual durations, and measured request counts. Judge
representatives only—not the final narrative cut yet.

---

## Task 9: Rewrite Structure as a Visual Reject-Only Rough Cut

- [ ] RED/GREEN: **temporal coverage.** At least one visual per period across the whole range, with
  the granularity the current selector uses: daily up to a month, weekly to three months, monthly
  to a year, quarterly beyond. The plan did not mention this rule at all before the rules
  inventory; Structure is where a period can vanish silently.

- [ ] RED/GREEN: **proportion.** No single moment may supply more than a quarter of the cut. Selects
  kills duplicate frames by construction, which covers redundancy but not proportion: nothing else
  stops a memory spending itself on one very good afternoon. `ceil()` alone is the measured wrong
  answer — it "handed every memory two per moment".

**Branch:** `refactor/764-structure-projection`

**Files:**

- Rewrite: `src/immich_memories/analysis/selection_structure.py:69-663`
- Rewrite: `src/immich_memories/analysis/structure_answer.py:157-298`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Rewrite/adapt: `tests/test_selection_structure.py`
- Modify: `tests/test_structure_answer.py`

- [ ] RED: feed a fully fitting workprint and assert Structure still receives its exact sheet.
  Unnamed moments survive; only complete named rejects act.

```python
def test_structure_runs_when_cut_already_fits_and_only_named_rejects_act() -> None:
    result = run_structure(workprint("a", "b", "c", seconds=40), insight=insight(), target_duration=60, requester=scripted_reject("b"), trace=Trace())
    assert result.kept_ids == ("a", "c")
    assert result.rejected == (StructureDecision("b", "duplicates the same contribution"),)
```

- [ ] GREEN: replace `_ask()` text tables with a chronological selected workprint sheet containing
  record shots, Pass 2 reasons, actual durations, and current insight. Remove the already-fits
  bypass, global rank, `_shipped_est`, per-moment cap, and release-to-fit logic.

- [ ] RED/GREEN: if the initial valid rough cut is above 110%, ask for the smallest additional
  named sacrifice set. Apply a complete set atomically. A failed/incomplete continuation leaves
  the valid overlong cut unchanged and adds `!! unresolved envelope`.

- [ ] RED/GREEN: rejecting the last surviving visual of an episode or the last record/favorite
  representative requires an explicit protected-occasion reason. Visual weakness, repetition, and
  duration alone are invalid protected-occasion reasons.

- [ ] RED/GREEN: assert chronology is stored, even when sacrifice importance order is deliberately
  non-chronological. There is no runtime prefix/tail interpretation.

- [ ] Keep only the safe #768 ideas with fresh tests: balanced complete-object parsing,
  reject-only application, chronological IDs, reason ledger, and no invented fallback order.

- [ ] Run:

```bash
make test-one T="tests/test_selection_structure.py tests/test_structure_answer.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/selection_structure.py src/immich_memories/analysis/structure_answer.py src/immich_memories/analysis/selection_flow.py tests/test_selection_structure.py tests/test_structure_answer.py
git commit -m "refactor(selection): make structure a visual rough cut (#764)"
```

---

## Task 10: Project and Revise the Insight Once

**Files:**

- Create: `src/immich_memories/analysis/projection_answer.py`
- Create: `src/immich_memories/analysis/selection_projection.py`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_selection_projection.py`

- [ ] RED: a revised insight replays Structure exactly once over the complete original Pass 2
  workprint, including an item rejected by the first Structure answer.

```python
def test_revised_insight_replays_structure_once_over_complete_workprint() -> None:
    result = select_with_script(first_reject="b", projection="revise", replay_keep="b")
    assert result.structure_calls == 2
    assert result.structure_inputs[1] == ("a", "b", "c")
    assert result.selected_ids == ("a", "b", "c")
```

- [ ] GREEN: implement `project_insight()` returning confirm, revise, or discard with evidence. If
  no provisional thesis exists, skip the model call and record the skip.

- [ ] RED/GREEN: enforce a single revision and a single replay. A second revision request is
  invalid and fail-open. Cache identity includes prior insight, complete workprint, Structure
  decisions, and sheet hashes.

- [ ] RED/GREEN: bank before/after evidence in the same trace. First Structure decisions are
  evidence, never vetoes during replay.

- [ ] Run:

```bash
make test-one T="tests/test_selection_projection.py tests/test_selection_structure.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/projection_answer.py src/immich_memories/analysis/selection_projection.py src/immich_memories/analysis/selection_flow.py tests/test_selection_projection.py
git commit -m "feat(selection): revise the thesis once against the cut (#764)"
```

**Slice gate:** Only now judge the June Structure cut. It must receive the pregnancy-test record
shot, actual Pass 2 representatives, favorites, durations, and insight. Two professional races may
not both survive merely for being separate events; the Brussels Tour setup/action/payoff sequence
may survive if its three contributions earn the time. No topic count is asserted.

---

## Task 11: Judge the Whole Cut and Perform One Bounded Repair

- [ ] RED/GREEN: **a period's last voice survives Fine Cut unless it is unusable.** Carry
  `spare_last_voices()` as a CORRECTION applied after the verdict, never as an exemption granted
  before it — "exempting has to guess in advance which clips carry a period, and both ways of
  guessing are wrong." Unusable stays dropped however alone it is; merely weak does not. Every
  drop site in the cut must treat a period's only visual as untouchable, which is the lesson
  `_trim_non_favorites` records as the last site that did not.

**Branch:** `feat/764-fine-cut`

**Files:**

- Create: `src/immich_memories/analysis/fine_cut_answer.py`
- Create: `src/immich_memories/analysis/selection_fine_cut.py`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_fine_cut_answer.py`
- Create: `tests/test_selection_fine_cut.py`
- Adapt: `tests/test_selection_review.py`
- Adapt: `tests/test_review_parsing.py`
- Adapt: `tests/test_review_drops_are_applied.py`

- [ ] RED: Fine Cut sees one complete chronological cut sheet, current insight, Structure reasons,
  record-shot marks, and one banked alternate per eligible moment. It returns a strict partition.

- [ ] GREEN: implement:

```python
def run_fine_cut(
    structure: StructurePassResult,
    *,
    insight: PeriodInsight | None,
    alternates: Mapping[str, tuple[str, ...]],
    record_shots: Sequence[RecordShotMark],
    requester: EditorialGateway,
    trace: Trace,
) -> FineCutResult: ...
```

Retain the useful strict partition and fail-open parser semantics from `selection_review.py`, but
replace its text-only question and post-hoc favorite vetoes.

- [ ] RED/GREEN: a reject reason such as “missing establishing context” may inspect one banked
  alternate from the same moment. The replacement gets one visual judgment. A rejected moment or
  occasion cannot return through a sibling; reopening requires the already-bounded projection
  replay.

- [ ] RED/GREEN: a shorter strong cut remains short. Fine Cut cannot call backfill, score-ranked
  replenish, stabilise, numeric judge, or an open-ended repair loop.

- [ ] RED/GREEN: failure, refusal, or truncation rejects nothing further and adds `!!`; complete
  named rejects before a truncated tail may act only when the complete partition contract still
  validates.

- [ ] Probe the combined Projection + provisional Fine Cut request against separate calls over the
  same sheet. Enable fusion only if conservation and decisions match. If projection revises,
  discard the provisional Fine Cut response, replay Structure, and run Fine Cut once on the changed
  workprint. Record planned and actual call counts.

- [ ] Run:

```bash
make test-one T="tests/test_fine_cut_answer.py tests/test_selection_fine_cut.py tests/test_selection_review.py tests/test_review_parsing.py tests/test_review_drops_are_applied.py tests/test_the_review_makes_the_cut.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/fine_cut_answer.py src/immich_memories/analysis/selection_fine_cut.py src/immich_memories/analysis/selection_flow.py tests/test_fine_cut_answer.py tests/test_selection_fine_cut.py tests/test_selection_review.py tests/test_review_parsing.py tests/test_review_drops_are_applied.py
git commit -m "feat(selection): judge the complete visual cut (#764)"
```

---

## Task 12: Make Rendering a Conserved Implementation Detail

**Files:**

- Modify: `src/immich_memories/analysis/motion_rendering.py:20-76`
- Modify: `src/immich_memories/analysis/live_photo_pipeline.py:28-120`
- Modify: `src/immich_memories/generate_clips.py:96-184`
- Modify: `src/immich_memories/generate_timeline.py:78-129`
- Modify: `src/immich_memories/generate.py`
- Create: `tests/test_render_membership.py`
- Modify: `tests/test_rendering_last.py`
- Modify: `tests/test_motion_rendering.py`
- Modify: `tests/test_timeline_budget.py`
- Add: `tests/integration/assembly/test_editorial_membership.py`

- [ ] RED: select photo/video/Live Photo visuals in a known order and assert render planning chooses
  a mode from the selected asset's immutable `LivePhotoRenderingFamily` without changing membership.

```python
def test_render_plan_preserves_ordered_editorial_membership() -> None:
    selected = (visual("photo"), visual("live", modes=("still", "motion")), visual("video"))
    plan = plan_renderings(selected)
    assert tuple(item.asset_id for item in plan) == ("photo", "live", "video")
```

- [ ] GREEN: resolve still versus stitched motion only after Fine Cut from the preserved ordered,
  aligned family manifest. Emit the selected asset identity for either mode, remove legacy
  `_motion_by_carrier`, and never independently align sorted still/video/trim/shutter arrays.
- [ ] RED/GREEN: pre-render stages carry per-mode duration options or explicit mode-neutral bounds;
  a single intended duration must not secretly choose motion. Validate the final chosen mode's
  duration contract during render planning.
- [ ] RED/GREEN: the visual atlas eventually places a Live Photo still and bounded local motion
  evidence in the same tile and existing request. It adds no LLM call and never claims motion was
  judged when motion pixels were unavailable.

- [ ] RED/GREEN: remove membership changes from `_sample_for_minimum_duration()`. Budget adjustment
  may trim intended segments proportionally within their contracts; it cannot add, drop, reorder,
  or substitute visuals.

- [ ] RED/GREEN: make extraction failure fatal to the run. After extraction and after assembly,
  compare ordered selected IDs with ordered produced IDs. Missing, extra, or reordered IDs create a
  conservation failure and abort; never ship a partial output.

- [ ] Add a real FFmpeg `testsrc` integration proving ordered membership and an injected extraction
  failure proving no partial output is accepted.

- [ ] Run:

```bash
make test-one T="tests/test_render_membership.py tests/test_rendering_last.py tests/test_motion_rendering.py tests/test_timeline_budget.py"
make test-integration-assembly
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/motion_rendering.py src/immich_memories/analysis/live_photo_pipeline.py src/immich_memories/generate_clips.py src/immich_memories/generate_timeline.py src/immich_memories/generate.py tests/test_render_membership.py tests/test_rendering_last.py tests/test_motion_rendering.py tests/test_timeline_budget.py tests/integration/assembly/test_editorial_membership.py
git commit -m "fix(selection): preserve the cut through rendering (#764)"
```

**Slice gate:** Review the complete June, April, and August cuts. Each is chronological,
warning-free, conserved, and near its requested duration without filler. June includes the
pregnancy test, excludes the generic selfie unless it earns a role, and resolves repeated races
from their contribution—not a cycling cap.

---

## Task 13: Converge CLI, UI, Automation, Dry-Run, and Contact Sheets

**Branch:** `refactor/764-editorial-cutover`

**Files:**

- Modify: `src/immich_memories/cli/_pipeline_runner.py:134-178, 263-313, 316-568`
- Modify: `src/immich_memories/cli/_candidate_pool.py:27-215`
- Modify: `src/immich_memories/cli/generate.py:243-289, 370-602`
- Modify: `src/immich_memories/cli/_album_generation.py:30-148`
- Modify: `src/immich_memories/cli/_trip_generation.py:147-317`
- Modify: `src/immich_memories/ui/pages/step2_loading.py:41-234`
- Modify: `src/immich_memories/ui/pages/clip_pipeline.py:183-370`
- Modify: `src/immich_memories/ui/state.py:65-113, 279-281`
- Modify: `src/immich_memories/ui/pages/step3_options.py`
- Modify: `src/immich_memories/ui/pages/step4_export.py`
- Modify: `src/immich_memories/ui/pages/_step4_generate.py`
- Modify: `src/immich_memories/automation/runner.py:503-580`
- Modify: `scripts/contact_sheet.py:35-72, 197-303`
- Create: `tests/test_selection_surface_parity.py`
- Modify: `tests/test_no_render.py`
- Modify: `tests/test_clip_pipeline_ui.py`
- Modify: `tests/test_auto_runner.py`
- Modify: `tests/test_contact_sheet.py`

- [ ] RED: dependency-inject one scripted gateway and run CLI, UI adapter, automation, dry-run, and
  no-render over the same fake source. Assert exact equality of source IDs, owner exclusions, pass
  order, ordered final IDs, intended segments/durations, warnings, request count, and trace
  fingerprint.

- [ ] GREEN: make all surfaces call only `prepare_editorial_source()` and
  `select_editorial_cut()`. Source scopes cover date range, album, and trip. The core imports no UI
  or CLI package, preserving `pyproject.toml` import-linter contracts.

- [ ] RED/GREEN: UI Step 2 checkboxes become explicit owner exclusions before Pass 0. Persist the
  typed `EditorialSelectionResult` in UI state. Steps 3 and 4 consume its unified ordered visual
  list directly; never reconstruct selected photos against a video-only source list.

- [ ] RED/GREEN: automation dry-run invokes the real CLI with rendering, upload, delivery, and
  notifications disabled. It reports success only after selection succeeds.

- [ ] RED/GREEN: `--dry-run` and `--no-render` share full editorial semantics. Keep `--no-render`
  as a compatibility alias if needed. If a cheap inventory-only operation remains useful, give it
  a different explicit name; it is never gate evidence.

- [ ] Replace `scripts/contact_sheet.py` private SmartPipeline monkeypatching with the public
  no-render outcome and trace. Preserve the private-media warning and sweep output layout.

- [ ] Run:

```bash
make test-one T="tests/test_selection_surface_parity.py tests/test_no_render.py tests/test_clip_pipeline_ui.py tests/test_auto_runner.py tests/test_contact_sheet.py tests/test_surface_parity.py"
make test-integration-pipeline
make test-integration-cli
make e2e
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/cli src/immich_memories/ui src/immich_memories/automation/runner.py scripts/contact_sheet.py tests/test_selection_surface_parity.py tests/test_no_render.py tests/test_clip_pipeline_ui.py tests/test_auto_runner.py tests/test_contact_sheet.py tests/test_surface_parity.py
git commit -m "refactor(selection): route every surface through one editor (#764)"
```

---

## Task 14: Delete Superseded Selectors and Migrate Configuration

**Files:**

- Delete after replacement tests pass: `src/immich_memories/analysis/arithmetic_funnel.py`
- Delete after replacement tests pass: `src/immich_memories/analysis/selection_review.py`
- Delete or reduce: `src/immich_memories/analysis/selection_quality.py`
- Delete or reduce: `src/immich_memories/analysis/clip_backfill.py`
- Delete or reduce: `src/immich_memories/analysis/clip_refiner.py`
- Delete or reduce: `src/immich_memories/analysis/photo_look.py`
- Modify: `src/immich_memories/analysis/smart_pipeline.py`
- Modify: `src/immich_memories/analysis/subject_policy.py`
- Modify: `src/immich_memories/config_models_analysis.py`
- Modify: `src/immich_memories/config_models_render.py`
- Modify: `src/immich_memories/config_loader.py:73-121`
- Modify: `src/immich_memories/config_presets.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs-site/docs/create/pipeline/pipeline-overview.md`
- Modify: `docs-site/docs/create/pipeline/clip-selection-scoring.md`
- Modify: `docs-site/docs/create/pipeline/photo-support.md`
- Modify: `docs-site/docs/create/cli/generate.md`
- Modify: relevant pages under `docs-site/docs/create/web-ui/`
- Modify: `docs-site/docs/reference/config-reference.md`

- [ ] RED: replace obsolete behavior tests with assertions that the new public flow cannot import
  or call arithmetic rank, quota, backfill, numeric judge, text Structure/Review, photo cap, or
  timeline membership sampling.

- [ ] GREEN: delete each legacy membership changer only after its replacement coverage is green:

  - arithmetic funnel after Structure convergence and honest unresolved-envelope behavior;
  - subject quotas after Cull evidence and cross-surface parity;
  - `let_the_favourite_win()` after construction-time favorite tests;
  - membership-changing dedup after visual Selects/Fine Cut reasons and conservation;
  - backfill after shorter-strong-cut and bounded-alternate tests;
  - numeric judge/stabilize/photo look after Fine Cut owns final visible rejection;
  - legacy trace adapters after every public path emits complete editorial pass/request records.

  Keep source similarity evidence, Live Photo component normalization, and non-membership segment
  trimming.

- [ ] RED/GREEN: removed nested Pydantic fields currently disappear silently. Emit explicit removed
  field warnings before deleting them; do not add compatibility aliases or reinterpret old ratios
  as new editorial policy.

- [ ] Update CLI/config generated references, pipeline docs, UI docs, and `ARCHITECTURE.md`. Explain
  the pass order, minimum-call packing, dry-run semantics, cache invalidation, owner exclusions,
  and the fact that shorter strong cuts beat filler.

- [ ] Run:

```bash
make docs-cli
make docs-cli-check
make docs-config-check
make docs-build
make docs-check
make test-integration-processing
make test-integration-pipeline
make test-integration-cli
make e2e
make test
make critique
make ci
```

- [ ] Commit deletion and docs as separate reviewable commits, each after `make ci`:

```bash
git commit -m "refactor(selection): remove superseded membership paths (#764)"
git commit -m "docs(selection): document the contact-sheet editor (#764)"
```

---

## Task 15: Final Acceptance and Cutover

**Branch:** `feature/764-editorial-selection`, after all slice PRs are squash-merged

- [ ] Merge current `origin/main` into the feature trunk. Resolve conflicts by preserving the new
  single-flow contracts; never resurrect a legacy membership stage merely to make a test pass.

- [ ] Run synthetic acceptance tests covering:

  - 1, 2, and 3 useful repetitions under short, medium, and long envelopes;
  - five repetitions in an unrelated domain;
  - topic-label swaps preserving decision shape;
  - low-aesthetic record shot survival and composition;
  - favorite auto-win and protected-occasion omission;
  - refusal/truncation fail-open behavior;
  - one projection replay maximum;
  - complete chronological pass/request trace and conservation;
  - exact CLI/UI/automation/dry-run parity;
  - real FFmpeg membership/order conservation and fatal extraction failure.

- [ ] Run a fresh private output directory for the feature-trunk SHA:

```bash
make dev
make contact-sheets SPEC=/absolute/private/764-gates.json OUT=/absolute/private/cutover-<sha>
```

The private spec contains June plus accepted April and August controls. Never commit or attach the
media, sheets, or traces.

- [ ] Owner acceptance requires all three sheets to be chronological, judgeable, warning-free,
  conserved, and near target without filler. June additionally checks the observed regressions:
  pregnancy-test record shot present, generic selfie absent unless it earns a role, kitten selfie
  allowed if useful, repeated professional races resolved by contribution, and Brussels Tour
  setup/action/payoff allowed to survive when earned.

- [ ] Run final repository gates:

```bash
make test-integration
make test-integration-cli
make e2e
make docs-check
make critique
make ci
make launch-check
```

- [ ] Update the umbrella draft PR body with per-slice PRs, measured cached/uncached model call
  counts, synthetic results, private owner verdicts without private media, configuration removals,
  and rollback instructions. Change it from draft only after owner approval.

- [ ] Merge the final `feature/764-editorial-selection` → `main` PR. This is the only PR that closes
  #764. Keep the previous selector available in Git history; do not add a hidden runtime fallback
  that can silently masquerade as the new editor.

## Final Self-Review Checklist

- [ ] No `TODO`, `TBD`, placeholder parser, provider-name limit guess, or topic-specific quota is in
  production code.
- [ ] Every pass consumes exact pixels and carries input IDs, sheet hashes, schema/model versions,
  cache identity, duration, decisions, and conservation in one trace.
- [ ] The normal uncached call budget is episode-scan packs + one period synthesis + packed Selects
  + one Structure + one Projection/Fine request, with extra calls only for proven continuations,
  one revision replay, or a failed combined-call probe.
- [ ] No per-asset or per-moment LLM loop exists where a packed sheet can preserve judgment quality.
- [ ] No scalar score, quota, deduper, backfill, reviewer, or renderer changes final membership.
- [ ] The feature trunk is the base of every production slice; `main` changes only at final cutover.
- [ ] `make ci`, `make critique`, required integration suites, docs gates, and owner contact-sheet
  gates all pass at the exact final SHA.
