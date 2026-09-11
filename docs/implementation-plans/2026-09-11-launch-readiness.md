# Launch readiness: what it takes to install the story-first product

Written against `feat/story-first-docs` (`151bcc64`), the tip of the story-first stack — selection,
CLI/UI and docs, the state the product ships in. `main` does not have this code.

The engine is done and graded. The install is not. Today a stranger who runs
`docker compose up` gets an app that stops on its first cut and tells them to go find four model
artifacts, two of which have no documented source at all. This plan inventories what the route
actually needs, walks two realistic self-hosting paths until they break, states honestly which
model families were exercised, and turns that into ordered work.

Two owner constraints frame it: **it needs to be easy**, and **it needs to say out loud that this
is heavy machinery**. Those pull in opposite directions only if we pretend the machinery isn't
there.

---

## 1. What the product needs to run

Six pieces. The wheel ships one of them.

| Piece | What it is | Where from | Size | Pinned by | Bundled or fetched | Licence |
|---|---|---|---|---|---|---|
| **Text model** | The reader: groups days into stories, weighs them, picks carriers. Also *sees pictures* — `PictureFactsProvider` posts 800 px JPEG tiles to the same endpoint (`editorial_picture_facts.py:100`, wired `editorial_runtime_backend.py:176`). So this seat needs vision, not just chat. | Any OpenAI-compatible `/chat/completions`. Owner ran `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX. | ~17 GB resident at 4-bit | `editorial_model_attestation.py` records repo + revision `0555d34c` + tree digest — **as provenance only**, and only when `llm.model` is that exact string (`editorial_picture_facts.py:116`). Nothing is enforced. | Operator supplies | Not recorded in `THIRD_PARTY_NOTICES` |
| **Caption server** | One 140-token description + setting per picture, once, banked. | Any OpenAI-compatible endpoint that advertises the alias `smolvlm2-500m-base-public` at `/models` (`editorial_description_contract.py:8`). Accepted weights: `mlx-community/SmolVLM2-500M-Video-Instruct-mlx@fa57db46`, SHA-256 `a9839c8f…`. | ~500 M params | Docs pin repo + revision + weights digest; the client only checks the advertised alias and three synthetic schema controls (`editorial_preparation_captions.py:check_provider`) | Operator supplies, separate service on `:8092` | Not recorded |
| **DINOv2-small ONNX export** | Frozen encoder behind the six context heads. | **Nowhere.** | 88 MB | `encoder.py:25` — SHA-256 `478164cd…`, hard `RuntimeError` on any other digest | Neither bundled nor fetched. No URL, no export command, no script. `scripts/triage_heads/` — cited by the bundle's own provenance card — **does not exist in this tree**. | Upstream `facebook/dinov2-small`; not recorded |
| **Head bundle** | PCA + six linear heads (location, people, children, activity, venue, swim). | `src/immich_memories/triage/bundled_heads/public-6heads-v3.npz` | 2.2 MB | Bound to encoder key `3d8a4df2…`; refuses any other encoder | **In the wheel.** The one easy piece. | Coefficients under the repo's MIT; training images CC BY 2.0 Open Images, not redistributed |
| **Two CPU detectors** | `nsfw_marqo` and `doc_docling` facts. Both CPU by construction — Docling forces `CPUExecutionProvider`, Marqo runs `torch.set_num_threads(6)`. | Hugging Face: `Marqo/nsfw-image-detection-384@0c26ec22`, `docling-project/DocumentFigureClassifier-v2.0@2a12e026` | ~400 MB combined snapshots | Git revision only — no checksum | Fetched by `hf_hub_download`, but **`allow_model_downloads` defaults to `false`**, so a fresh install with a cold HF cache fails here | Not recorded |
| **`editorial` extra** | `onnxruntime`, `torch`, `timm`, `huggingface-hub` (`pyproject.toml:119`). | PyPI | linux/arm64: 454 MB. **linux/amd64: 3.05 GB** — torch 555 MB plus 15 `nvidia-*` CUDA wheels totalling 2.5 GB, for two detectors that never touch a GPU. | `uv.lock`; the Docker build constrains to it | In the image (`INSTALL_EXTRAS=all`) | Permissive |

**Request shape.** Pages are capped at 32,000 chars / 60 episodes for grouping
(`editorial_story_grouping.py:27`), 18,000 / 16 for episode readings, 14,000 / 16 for the moment
inventory. Output ceilings run 1,400–4,500 tokens. So ~35 kB requests are normal and a 32k-token
native window is enough; the weighing call, which sees every story of the period at once, is the
only one that is not paged.

**There is no `prepare` command.** Preparation runs inside `generate`, from
`editorial_runtime.py:480`. A first cut on a cold library does every caption, head and detector
pass inline, and anything missing raises `EditorialInputsRequired` with a count per producer.
`preflight` checks the LLM endpoint and nothing else — not the caption alias, not the encoder
digest, not the detector snapshots.

**Dead weight to clean up:** `description_llm` (config reference documents it on `:8090`) has
exactly two mentions in `src/`, both in `config_loader.py`. Nothing reads it. It is a third model
endpoint in the docs that no code has ever called.

---

## 2. What a self-hoster faces today

### The Synology path

1. Copies the compose file from the NAS guide. One service, port 8080. Fine.
2. Sets `IMMICH_URL` and `IMMICH_API_KEY`. Fine.
3. Runs a cut. It stops: `editorial runtime needs a nonblank LLM model`.
4. Points `llm.base_url` at… what? The NAS has no GPU and 8 GB of RAM. The docs' tested pair is a
   17–24 GB download. The NAS guide's honest answer is "another machine", which for most Synology
   owners means "no machine".
5. Suppose they have a spare box. Next stop: `caption_base_url` defaults to
   `http://localhost:8092/v1`, which inside the container means the container. Nothing listens.
   No doc names a server that can serve the required alias, or how to alias a repo to it.
6. Suppose they solve that too. Next stop: the DINOv2 export. **Dead end.** Digest-pinned, not
   bundled, not downloadable, no export recipe anywhere in the repo or the docs. ONNX exports are
   not byte-reproducible across torch/onnx versions, so "export it yourself" is not a workaround
   either — they cannot hit `478164cd…` by luck.

The Synology path breaks at step 4 for most people and at step 6 for everyone.

### The mini-PC path (amd64, 32 GB, no discrete GPU)

Further, then the same wall.

1. `pip install "immich-memories[editorial]"` pulls **3.05 GB** including the full CUDA stack for
   CPU-only detectors.
2. A small local text model on Ollama gets them past step 3 — at unknown quality, since the route
   was never graded on one.
3. Cold HF cache plus `allow_model_downloads: false` fails the detectors with a message that does,
   to its credit, name the fix.
4. DINOv2 export: same dead end.

### Kubernetes

`deploy/kubernetes/base/networkpolicy.yaml` allows egress to DNS, 80, 443, 2283 and 11434.
Port 8092 — the documented caption default — is blocked by our own shipped policy. The k8s page
says so and leaves it to the reader. No manifest mounts the encoder or an HF cache.
`base/kustomization.yaml` still pins `newTag: "0.70.0"`.

### The release gate that will fail

`release.yml`'s `docker-smoke` runs a real `generate` inside the image with only `IMMICH_URL` and
`IMMICH_API_KEY` set. On the story-first route that hits
`build_editorial_planner`'s blank-model guard (`editorial_runtime.py:540`) before anything else.
The gate has not run against this code because the stack is not on `main` yet. It will fail on the
first release after the merge.

---

## 3. Tested model families and hardware

The docs currently say **"Developed and tested against Qwen3.6-27B and Qwen3.6-35B-A3B"** in seven
places, including the README and the Docker page. That sentence is true of the *old* per-clip
scorer. It is not true of the story-first route, whose approved matrix sheets were produced on
Qwen3-VL-30B-A3B-Instruct-4bit — which is what `editorial_model_attestation.py` pins. Shipping the
old claim next to the new engine is the kind of thing that gets reported as a bug by someone who
followed the docs exactly.

| Seat | Family | Status | Notes |
|---|---|---|---|
| Text/vision reader | `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX, Apple Silicon | **Tested** — the graded matrix ran on this | The only configuration whose output the owner has approved |
| Text/vision reader | Qwen3.6-27B / Qwen3.6-35B-A3B (Ollama, vLLM, oMLX) | **Untested on this route** | Tested on the retired scorer. Needs vision — this seat is sent 800 px tiles |
| Text/vision reader | Any other OpenAI-compatible vision model with ≥32k context and strict JSON | **Expected to work** | The contract is `/chat/completions`, images, `response_format` json_schema. Unknown quality |
| Text/vision reader | Text-only models | **Unsupported** | The picture-facts pass posts images to this endpoint |
| Caption | `mlx-community/SmolVLM2-500M-...-mlx@fa57db46` | **Accepted** — the digest the banked descriptions came from | MLX format: Apple Silicon only |
| Caption | Same SmolVLM2 generation served by vLLM / llama.cpp on Linux | **Untested** | Plausible and necessary; nobody has served the alias this way and compared descriptions |
| Encoder | The pinned DINOv2-small ONNX export | **Required, exact** | Digest-checked; any other export is refused |
| Detectors | Marqo + Docling at the pinned revisions | **Tested** | CPU only, both |

### Honest hardware tiers

| Tier | Hardware | What runs where | Reality |
|---|---|---|---|
| **Tested** | Apple Silicon with 32 GB+ unified memory | Everything on one box: reader, caption server, app, render | The only end-to-end configuration anyone has graded |
| **Expected to work** | App on a NAS/mini-PC + a 24 GB GPU box or a second Mac for the two model services | App is cheap (2–4 GB); the models are not | Two machines. Say so plainly |
| **Expected to work, slowly** | One amd64 box, CPU-only, small reader model | A 4B-class reader will answer; quality unmeasured | Fine for a first look, not for a verdict on the editor |
| **Not yet supported** | NAS alone | Captions, encoder and detectors on the NAS CPU; the reader hosted (4.21) or gone (4.22) | Two profiles, both owed before release. Not a smaller local reader |

**VAAPI, Quick Sync and NVENC are media accelerators.** They decode, scale and encode. They do not
run inference. The hardware pages are correct today and must stay that way.

**Cost.** The one published end-to-end number (14-clip monthly, 4 arm64 cores, no GPU: 10 min 08 s
on `preset: fast`) predates this route and says so. Preparation cost per picture on the story-first
route is **unmeasured**, and the README already admits it. It should be measured before launch, not
estimated.

---

## 4. Work plan

Sizes are S (a day), M (a few days), L (a week or more).

### Docker & bootstrap

**4.1 — Publish the DINOv2 export and a command that fetches it. L. Blocks everything.**
Nothing else on this list matters while the required file has no source. Publish the exact 88 MB
export as a release asset (GitHub release or a dedicated HF repo), add `immich-memories models
fetch`, and have it verify `478164cd…` before writing. Also restore or re-publish the export
script so the artifact is reproducible rather than a blob we happen to have.
*Exit test:* on a clean machine with no HF cache, one documented command leaves a file at the
default path whose SHA-256 matches, and a cut proceeds past the heads stage.

**4.2 — Extend the same command to the detectors. S.**
`models fetch` also warms the two pinned HF snapshots, so `allow_model_downloads` can stay `false`
in normal operation and mean what it says.
*Exit test:* `HF_HUB_OFFLINE=1` after a fetch, detectors run.

**4.3 — Fix the release smoke gate. S.**
`docker-smoke` must either configure the editorial stack (a tiny stub caption server and a stub
chat endpoint alongside the fake Immich) or assert the honest refusal. Silently green is not an
option; neither is a release that fails at the last gate.
*Exit test:* `release.yml` `docker-smoke` passes on the combined tree, and fails if the caption
endpoint is removed.

**4.4 — A compose profile that stands the models up. L.**
`docker compose --profile editorial up` brings a caption server on 8092 next to the app, on both
amd64 and arm64, Debian-based. This is the "one blessed deployment path" — without it, every
install is a bespoke integration.
*Exit test:* on a clean amd64 host and a clean arm64 host, the profile answers `/models` with the
alias and passes the three schema controls; a monthly cut completes.

**4.5 — CPU torch for the shipped image. S.**
Pin the CPU wheel index for linux/amd64. Measured saving: ~2.5 GB of CUDA that no shipped code
path uses.
*Exit test:* `make test` and `docker-smoke` unchanged; the amd64 image is ≥2 GB smaller.

**4.6 — Unblock 8092 in the shipped NetworkPolicy, mount a models volume, re-pin the kustomize
tag. S.**
*Exit test:* a k8s apply with the editorial overlay reaches the caption service; `newTag` matches
the current release.

**4.7 — Add a `.dockerignore`. S.**
There is none; `make docker` ships `tests/`, `docs-site/`, `output/` and any local `.venv` to the
daemon.
*Exit test:* build context under 50 MB.

### Model publishing

**4.8 — Decide and document where each artifact lives. M.**
Encoder: ours to publish. Caption weights: point at upstream, and publish the non-MLX serving
recipe. Detectors: upstream, pinned by revision — add a checksum so the pin is a pin.
*Exit test:* every row of the §1 table has a URL and a digest a user can verify.

**4.9 — Grade one Linux-servable caption path. M.**
The MLX caption weights are Apple-only. Serve the same SmolVLM2 generation under vLLM or
llama.cpp, run the schema controls, and diff descriptions against the banked MLX ones on a fixed
sample. If they match, Linux gets a real answer; if they don't, we say so.
*Exit test:* a documented Linux recipe, plus a recorded agreement rate against the banked
descriptions.

**4.10 — Licence audit, recorded. S.**
`THIRD_PARTY_NOTICES` lists no models at all. The four model artifacts get entries with their
actual licences, and anything not MIT/Apache/BSD gets removed from the shipped path rather than
footnoted.
*Exit test:* `THIRD_PARTY_NOTICES` covers every artifact in §1; no non-permissive licence remains.

### Docs

**4.11 — One self-hosting guide, at the top of Deploy. M.**
Everything a user needs to go from nothing to a first cut, in order, on one page: the three
services, the three ports, the fetch command, what runs where on one machine and on two. Today
this is scattered across `editorial-preparation.md`, `mac-local-llm.md`, `nas-only.md` and the
config reference, and `editorial-preparation.md` sits last in Configuration behind three
networking pages.
*Exit test:* someone who has never seen the repo gets a cut using only that page.

**4.12 — Fix the tested-families claim in all seven places. S.**
Replace "Developed and tested against Qwen3.6-27B and Qwen3.6-35B-A3B" with the §3 table's truth:
what was graded, what is expected to work, what is unsupported, and that the reader seat needs
vision.
*Exit test:* no page claims a test that did not happen.

**4.13 — Hardware tiers that include the models. S.**
Every RAM table today sizes the app at 2–8 GB and ignores the 17–38 GB of resident weights next to
it. Add the model row.
*Exit test:* the Docker resource table and the README table both name the model's memory.

**4.14 — Heavy machinery, said out loud. S.**
A short, unapologetic paragraph near the top of the README and the install page: this runs real
models, it wants a machine that can hold them, and here is the cheapest configuration that works.
Not a warning banner — a spec.
*Exit test:* the reader knows the cost before they `docker compose up`, not after.

**4.15 — New headline. S.**
"only the good five seconds of each clip" sells the scorer we deleted. The product is an editor
that reads a period and argues for every picture it keeps. The second paragraph already says this
well; the headline should catch up.
*Exit test:* the headline describes editorial understanding, not clip trimming.

**4.16 — Measure preparation cost and publish it. M.**
Seconds per picture for captions, heads and detectors, on Apple Silicon and on a CPU-only amd64
box, cold and warm. The README currently says "not yet measured on this route", which is honest
and unhelpful.
*Exit test:* a table with numbers and a date.

**4.17 — Delete `description_llm` or give it a consumer. S.**
*Exit test:* the config reference no longer documents an endpoint nothing calls.

### UI vocabulary

**4.18 — Map the machine words to reader words. S.**
`memory_story.py` shows the model's own vocabulary to users: `dominant`/`major`/`minor` weight
badges (`:14`), `remarkable`/`maybe` standing badges (`:15`), and `"7 granted"` (`:65`). Proposed
mapping, display only — the stored vocabulary does not change:

| Stored | Shown |
|---|---|
| `dominant` | Main story |
| `major` | Important |
| `minor` | Supporting |
| `glimpse` | Small moment |
| `remarkable` / `maybe` | (behind Details) |
| `7 granted` | `7 pictures` |

The reading labels stay available behind a **Details** disclosure, because the whole point of this
engine is that it can show its work.
*Exit test:* the story page contains no word from the model's answer schema above the fold.

### The two NAS profiles — both owed before release

A NAS owner has two honest routes and we owe them both. Neither is a smaller local reader: shipping
an ungraded model to the tier least able to judge the output is how the acceptance bar dies.

**What neither route removes.** Captions, the encoder and the two detectors run on the NAS either
way. The 500M captioner on a CPU is the real first-run cost, it is paid once per picture, and
VAAPI does not help it — that silicon decodes and encodes video, it does not run neural networks.

**4.21 — The hosted reader. M.**
Config already allows it: `llm.base_url` plus `llm.api_key`, with `max_tokens_param` negotiating the
reasoning-model field name. Three things are missing, and the second is the one nobody expects.
1. *No hosted provider has been graded on this route.* One route, one provider, compared against the
   approved plan, or we are guessing.
2. **Captions cannot go hosted.** `editorial_description_contract.API_MODEL` pins the alias
   `smolvlm2-500m-base-public`, and a hosted provider will never advertise it. So "use a hosted
   model" shrinks the local footprint from ~17 GB to ~2 GB — it does not empty it. Say that in the
   docs before someone buys a subscription expecting otherwise.
3. *Privacy is a decision, not a default.* The annotation lines carry people's real names and place
   names, and the reader is sent picture tiles. A third-party endpoint therefore needs an explicit
   opt-in that names what leaves the machine, not a base-url change that quietly starts uploading a
   family album.
Cost, measured earlier at 2026-09 prices: a full-year cut is ~746k input and ~42k output tokens, a
month ~111k and ~18k — EUR 0.02 to 0.12 per render. With a user's own key that is zero to us.
*Exit test:* a BYO-key recipe in the self-hosting guide, one route graded against a hosted provider,
and a first-run consent gate that states what is sent.

**4.22 — The no-LLM rule reader. L.**
Designed in [`docs/designs/2026-09-11-rule-reader.md`](../designs/2026-09-11-rule-reader.md): a
deterministic reader behind the existing reading contracts, four of its eight judgments already in
the tree as today's failure path. Four-day spike on one route, sequenced after the scorer removal.
*Exit test:* the design's own bar — every approved story keeps a carrier on all ten routes and zero
audience regressions, or it does not ship.

### Provenance

**4.19 — Issue #784: per-episode evidence-line hashes in attempts. M.**
A warm replay drifted with no code change and the attempt tree could not say which asset's line
moved. Until that is recorded, every future drift costs the same day of bisecting.
*Exit test:* `scripts/replay_editorial_routes.py` names the first asset whose line changed, not
just that something did.

### CI

**4.20 — One clean full CI run on the combined tree before merging the stack. S.**
Selection + CLI/UI + docs together, `make ci` plus the Docker jobs on both platforms.
*Exit test:* green, once, on the merged branch — not four green branches.

---

## 5. Deliberately not done before launch

- **Phase 4 deletion.** In progress on `feat/story-first-removal`. Noted, not planned here; it
  should follow the merge quickly so the tree stops carrying two selection stories.
- **Retraining or replacing any head.** The bundle is graded and bound to the encoder. Nothing on
  this list touches it.
- **A smaller reader model for the NAS-alone tier.** Tempting and wrong: shipping an ungraded model
  to the tier least able to judge the output is how we lose the acceptance bar. That tier gets 4.21
  or 4.22, not a model nobody graded.
- **GPU inference anywhere in the shipped image.** Stays out. The image's job is the app and the
  render; models are services.

---

## The three things most likely to embarrass us

1. **The DINOv2 export cannot be obtained.** Digest-pinned, not bundled, not downloadable, no
   export recipe in the tree — and the provenance card cites a script directory that does not
   exist. No user can complete a first cut. Everything else is secondary to 4.1.
2. **Both graded model artifacts are MLX.** The reader and the caption model were exercised only on
   Apple Silicon, while the docs point Linux users at a different family (Qwen3.6) on Ollama that
   this route has never been run on — and that seat needs vision, which the docs never say.
3. **The release pipeline fails on merge.** `docker-smoke` runs a real `generate` with no LLM
   configured and hits the blank-model guard. It has been green only because this code has never
   been on `main`.
